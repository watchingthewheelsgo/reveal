import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select

from server.bot.base import BotAdapter
from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import SocialPost, StockWatch, TwitterState
from server.db.time import utc_now_naive
from server.portfolio.markers import add_portfolio_holding_marker
from server.social.monitor import (
    _build_tweet_card,
    _epoch_or_zero,
    cache_user_tweets,
    check_and_notify,
    fetch_user_tweets,
    run_twitter_monitor,
)
from server.social.processor import TweetAnalysis, _parse_analysis


class DummyAdapter(BotAdapter):
    def __init__(self):
        self.admin_chat_ids = ["admin"]
        self.messages: list[tuple[str, str]] = []
        self.cards: list[tuple[str, dict]] = []
        self.uploaded_images: list[str] = []

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))

    async def send_card(self, chat_id: str, card: dict) -> None:
        self.cards.append((chat_id, card))

    async def send_card_returning_id(self, chat_id: str, card: dict) -> str | None:
        self.cards.append((chat_id, card))
        return f"card-{len(self.cards)}"

    async def upload_image(self, image_url: str, alt_text: str | None = None) -> str | None:
        self.uploaded_images.append(image_url)
        return f"img-{len(self.uploaded_images)}"

    def register_command(self, command: str, handler) -> None:
        return None

    async def push_to_admin(self, text: str) -> None:
        self.messages.append(("admin", text))


class DummyProcessor:
    def __init__(self):
        self.summary_input = ""

    async def analyze(self, context: str, author: str = "") -> TweetAnalysis:
        self.summary_input = context
        return TweetAnalysis(
            summary="测试摘要",
            translation=f"译文: {context}",
            mentioned_tickers=["NVDA"],
            topics=["AI基建"],
            sentiment="neutral",
            urgency="high",
            urgency_reason="测试",
            is_market_relevant=True,
            is_noteworthy=True,
            attention_reason="出现明确催化，值得立即关注。",
        )

    async def translate(self, text: str) -> str:
        return f"译文: {text}"

    async def summarize(self, text: str) -> str:
        self.summary_input = text
        return "测试摘要"


class MarketProcessor:
    async def analyze(self, context: str, author: str = "") -> TweetAnalysis:
        return TweetAnalysis(
            summary=f"{author} 提到特朗普关税新闻可能影响市场。",
            translation=None,
            mentioned_tickers=["SPY"],
            topics=["特朗普", "关税"],
            sentiment="mixed",
            urgency="medium",
            urgency_reason="政治政策可能影响风险偏好。",
            is_market_relevant=True,
            is_noteworthy=True,
            attention_reason=f"{author} 认为关税变化会影响美股风险偏好。",
        )


class SocialOnlyProcessor:
    async def analyze(self, context: str, author: str = "") -> TweetAnalysis:
        return TweetAnalysis(
            summary="作者庆祝粉丝增长。",
            translation=None,
            mentioned_tickers=[],
            topics=["粉丝庆祝"],
            sentiment="neutral",
            urgency="low",
            urgency_reason="无市场相关性。",
            is_market_relevant=False,
            is_noteworthy=False,
            attention_reason="",
        )


class TwitterMonitorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        await db_engine.init_db()

    async def asyncTearDown(self):
        await db_engine.close_db()
        self.tmpdir.cleanup()

    def test_epoch_or_zero_handles_legacy_null_state(self):
        self.assertEqual(_epoch_or_zero(None), 0)
        self.assertEqual(_epoch_or_zero(1780818687), 1780818687)

    async def test_tweet_analysis_parser_preserves_attention_signal(self):
        analysis = _parse_analysis(
            """
            {
              "summary": "重大订单更新",
              "translation": null,
              "mentioned_tickers": ["nvda"],
              "topics": ["AI基建"],
              "sentiment": "bullish",
              "urgency": "high",
              "urgency_reason": "可能影响股价",
              "is_market_relevant": true,
              "is_noteworthy": true,
              "attention_reason": "新增订单金额超预期"
            }
            """
        )

        self.assertTrue(analysis.is_noteworthy)
        self.assertEqual(analysis.attention_reason, "新增订单金额超预期")
        self.assertEqual(analysis.mentioned_tickers, ["NVDA"])

    async def test_fetch_user_tweets_prefers_graphql_when_auth_token_is_configured(self):
        graphql = AsyncMock(
            return_value={
                "screen_name": "alice",
                "history_cursor": "cursor-bottom",
                "latest_tweets": [
                    {"tweetID": "2", "date_epoch": 2, "text": "newer"},
                    {"tweetID": "1", "date_epoch": 1, "text": "older"},
                ],
            }
        )

        with (
            patch(
                "server.social.monitor.get_settings",
                return_value=SimpleNamespace(twitter_auth_tokens=["token-a"]),
            ),
            patch("server.social.monitor.fetch_user_tweets_graphql", new=graphql),
        ):
            data = await fetch_user_tweets("alice", count=10, cursor="cursor-old")

        graphql.assert_awaited_once_with(
            "alice",
            ["token-a"],
            count=10,
            cursor="cursor-old",
        )
        assert data is not None
        self.assertEqual(data["history_cursor"], "cursor-bottom")
        self.assertEqual([tweet["tweetID"] for tweet in data["latest_tweets"]], ["1", "2"])
        self.assertEqual(data["latest_tweets"][0]["tweetURL"], "https://x.com/alice/status/1")

    async def test_first_check_backfills_latest_ten_tweets(self):
        requested_counts = []

        async def fake_fetch_user_tweets(username: str, count: int = 20, cursor: str | None = None):
            requested_counts.append((count, cursor))
            return {
                "screen_name": username,
                "history_cursor": "cursor-old-page",
                "latest_tweets": [
                    {"tweetID": str(tweet_id), "date_epoch": tweet_id, "text": f"tweet {tweet_id}"}
                    for tweet_id in range(101, 113)
                ],
            }

        adapter = DummyAdapter()
        with patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets):
            posts = await check_and_notify("alice", adapter)

        self.assertEqual(len(posts), 10)
        self.assertEqual(requested_counts, [(10, None)])
        self.assertEqual(len(adapter.cards), 10)
        self.assertEqual(adapter.cards[0][0], "admin")
        pushed = "\n".join(
            line for _, card in adapter.cards for line in [card["title"], *card["sections"]]
        )
        self.assertIn("Twitter Backfill ·", pushed)
        self.assertIn("@alice 提到", pushed)
        self.assertIn("tweet 112", pushed)
        self.assertIn("tweet 103", pushed)
        self.assertNotIn("tweet 102", pushed)
        self.assertNotIn("/research", pushed)
        self.assertNotIn("/deep", pushed)
        self.assertIn("回复这张卡片", adapter.cards[0][1]["footer"])

        session_factory = get_session_factory()
        async with session_factory() as session:
            state = (
                await session.execute(select(TwitterState).where(TwitterState.username == "alice"))
            ).scalar_one()
            post_count = await session.scalar(select(func.count()).select_from(SocialPost))
            old_post = (
                await session.execute(select(SocialPost).where(SocialPost.tweet_id == "101"))
            ).scalar_one()
            latest_post = (
                await session.execute(select(SocialPost).where(SocialPost.tweet_id == "112"))
            ).scalar_one()

        self.assertEqual(state.last_tweet_epoch, 112)
        self.assertEqual(state.newest_tweet_id, "112")
        self.assertEqual(state.history_cursor, "cursor-old-page")
        assert state.last_check_at is not None
        self.assertIsNone(state.last_check_at.tzinfo)
        self.assertEqual(post_count, 12)
        self.assertIsNone(latest_post.posted_at.tzinfo)
        self.assertFalse(old_post.is_pushed)
        self.assertTrue(latest_post.is_pushed)

    async def test_latest_returns_cached_posts_during_fetch_cooldown(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(
                TwitterState(
                    username="alice",
                    last_tweet_epoch=200,
                    last_check_at=utc_now_naive(),
                )
            )
            session.add(
                SocialPost(
                    username="alice",
                    tweet_id="200",
                    tweet_url="https://x.com/alice/status/200",
                    content="cached tweet",
                    posted_at=datetime.fromtimestamp(200, tz=UTC).replace(tzinfo=None),
                )
            )
            await session.commit()

        fetch = AsyncMock(return_value=None)
        with patch("server.social.monitor.fetch_user_tweets", new=fetch):
            posts = await cache_user_tweets("alice", count=5)

        fetch.assert_not_awaited()
        self.assertEqual([post.tweet_id for post in posts], ["200"])

    async def test_new_tweet_stores_and_pushes_rich_metadata(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(TwitterState(username="alice", last_tweet_epoch=100))
            await session.commit()

        async def fake_fetch_user_tweets(username: str, count: int = 20, cursor: str | None = None):
            return {
                "screen_name": username,
                "latest_tweets": [
                    {
                        "tweetID": "101",
                        "date_epoch": 101,
                        "text": "hello https://example.com/report",
                        "tweetURL": "https://twitter.com/alice/status/101",
                        "media_extended": [
                            {
                                "url": "https://pbs.twimg.com/media/chart.jpg",
                                "type": "image",
                                "altText": "chart",
                            }
                        ],
                        "qrtURL": "https://twitter.com/bob/status/88",
                    }
                ],
            }

        async def fake_fetch_tweet(username: str, tweet_id: str):
            return {
                "tweetID": tweet_id,
                "date_epoch": 88,
                "text": "quoted context",
                "tweetURL": f"https://twitter.com/{username}/status/{tweet_id}",
            }

        adapter = DummyAdapter()
        processor = DummyProcessor()
        with (
            patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets),
            patch("server.social.monitor.fetch_tweet", new=fake_fetch_tweet),
        ):
            posts = await check_and_notify("alice", adapter, processor)

        self.assertEqual(len(posts), 1)
        self.assertEqual(len(adapter.cards), 1)
        pushed = "\n".join([adapter.cards[0][1]["title"], *adapter.cards[0][1]["sections"]])
        self.assertIn("NVDA · AI基建 · @alice 提到", pushed)
        self.assertIn("北京时间 1970-01-01 08:01", pushed)
        self.assertIn("**市场**", pushed)
        self.assertIn("相关股票: NVDA", pushed)
        self.assertIn("**事实**", pushed)
        self.assertIn("**观点**", pushed)
        self.assertIn("**影响和建议**", pushed)
        self.assertIn("出现明确催化", pushed)
        self.assertIn("测试摘要", pushed)
        self.assertIn("https://x.com/alice/status/101", pushed)
        self.assertIn("引用", pushed)
        self.assertIn("参考", pushed)
        self.assertNotIn("引用链接 / 外部链接", pushed)
        self.assertNotIn("https://pbs.twimg.com/media/chart.jpg", pushed)
        self.assertNotIn("quoted context", pushed)
        self.assertNotIn("消息 ID", pushed)
        self.assertNotIn("**正文**", pushed)
        self.assertNotIn("/research", pushed)
        self.assertNotIn("/deep", pushed)
        self.assertEqual(adapter.cards[0][1]["card_link"]["url"], "https://example.com/report")
        self.assertEqual(adapter.cards[0][1]["header"]["template"], "orange")
        self.assertIn("回复这张卡片", adapter.cards[0][1]["footer"])
        self.assertIn("quoted context", processor.summary_input)
        self.assertEqual(adapter.uploaded_images, [])

        async with session_factory() as session:
            post = (
                await session.execute(select(SocialPost).where(SocialPost.tweet_id == "101"))
            ).scalar_one()
            quoted_post = (
                await session.execute(select(SocialPost).where(SocialPost.tweet_id == "88"))
            ).scalar_one()

        self.assertTrue(post.is_quote)
        self.assertEqual(post.tweet_url, "https://x.com/alice/status/101")
        media = post.media
        references = post.referenced_tweets
        assert media is not None
        assert references is not None
        self.assertEqual(media[0]["alt_text"], "chart")
        self.assertEqual(post.links, ["https://example.com/report"])
        self.assertEqual(references[0]["text"], "quoted context")
        self.assertTrue(post.is_noteworthy)
        assert post.raw_json is not None
        self.assertTrue(post.raw_json["reveal_analysis"]["is_market_relevant"])
        self.assertEqual(post.attention_reason, "出现明确催化，值得立即关注。")
        self.assertEqual(quoted_post.username, "bob")
        self.assertEqual(quoted_post.content, "quoted context")
        self.assertFalse(quoted_post.is_pushed)
        self.assertTrue(post.is_pushed)

    async def test_low_priority_summary_card_stays_compact(self):
        long_summary = (
            "该推文作者庆祝粉丝数突破70万，并在过去3天内新增5000名订阅者，"
            "特别感谢中文社区的支持。同时引用内容显示其已成为X平台订阅数第一的账号，"
            "超越了埃隆·马斯克。"
        )
        post = SocialPost(
            id=103,
            username="aleabitoreddit",
            tweet_id="103",
            tweet_url="https://x.com/aleabitoreddit/status/103",
            content=(
                "哇？我的粉丝数竟然突破70万了……而且在过去的短短3天里，"
                "我又额外收获了5000名订阅者。\n\n真的非常感谢大家。\n\n"
                "尤其要特别感谢最近给予我巨大支持的中文社区！ https://t.co/nLLSohl0lN"
            ),
            translated_content="译文重复内容，不应该在已有摘要的低优先级卡片中展开。",
            summary=long_summary,
            media=[
                {
                    "url": "https://pbs.twimg.com/media/fans.jpg",
                    "type": "image",
                    "alt_text": "followers",
                }
            ],
            links=["https://t.co/nLLSohl0lN"],
            referenced_tweets=[
                {
                    "type": "quote",
                    "username": "aleabitoreddit",
                    "url": "https://x.com/aleabitoreddit/status/102",
                }
            ],
            is_quote=True,
            posted_at=utc_now_naive(),
            topics=["社交媒体增长", "粉丝庆祝"],
            sentiment="bullish",
            urgency="low",
        )

        card = await _build_tweet_card(post, DummyAdapter())
        pushed = "\n".join([card["title"], *card["sections"]])

        self.assertEqual(card["title"], "社交媒体增长 · 粉丝庆祝 · @aleabitoreddit 提到")
        self.assertNotIn("消息 ID #103", pushed)
        self.assertIn("**事实**", pushed)
        self.assertIn(long_summary, pushed)
        self.assertNotIn("**正文**", pushed)
        self.assertIn("https://t.co/nLLSohl0lN", pushed)
        self.assertNotIn("译文重复内容", pushed)
        self.assertNotIn("**媒体**", pushed)

    async def test_monitor_can_notify_admin_when_account_has_no_new_updates(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(TwitterState(username="alice", last_tweet_epoch=200))
            await session.commit()

        async def fake_fetch_user_tweets(username: str, count: int = 20, cursor: str | None = None):
            return {
                "screen_name": username,
                "latest_tweets": [
                    {"tweetID": "101", "date_epoch": 101, "text": "old tweet"},
                ],
            }

        adapter = DummyAdapter()
        with patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets):
            total = await run_twitter_monitor(
                ["alice"],
                adapter,
                notify_no_updates=True,
            )

        self.assertEqual(total, 0)
        self.assertEqual(adapter.messages, [])
        self.assertEqual(adapter.cards, [])

    async def test_monitor_keeps_no_update_notifications_opt_in(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(TwitterState(username="alice", last_tweet_epoch=200))
            await session.commit()

        async def fake_fetch_user_tweets(username: str, count: int = 20, cursor: str | None = None):
            return {
                "screen_name": username,
                "latest_tweets": [
                    {"tweetID": "101", "date_epoch": 101, "text": "old tweet"},
                ],
            }

        adapter = DummyAdapter()
        with patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets):
            total = await run_twitter_monitor(["alice"], adapter)

        self.assertEqual(total, 0)
        self.assertEqual(adapter.messages, [])
        self.assertEqual(adapter.cards, [])

    async def test_monitor_filters_irrelevant_social_updates(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(TwitterState(username="alice", last_tweet_epoch=100))
            await session.commit()

        async def fake_fetch_user_tweets(username: str, count: int = 20, cursor: str | None = None):
            return {
                "screen_name": username,
                "latest_tweets": [
                    {
                        "tweetID": "101",
                        "date_epoch": 101,
                        "text": "Thanks everyone for 700k followers. Huge milestone!",
                    },
                ],
            }

        adapter = DummyAdapter()
        with patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets):
            total = await run_twitter_monitor(["alice"], adapter, SocialOnlyProcessor())

        self.assertEqual(total, 0)
        self.assertEqual(adapter.cards, [])
        self.assertEqual(adapter.messages, [])

        async with session_factory() as session:
            state = (
                await session.execute(select(TwitterState).where(TwitterState.username == "alice"))
            ).scalar_one()
            post = (
                await session.execute(select(SocialPost).where(SocialPost.tweet_id == "101"))
            ).scalar_one()

        self.assertEqual(state.last_tweet_epoch, 101)
        self.assertFalse(post.is_pushed)

    async def test_monitor_does_not_push_raw_text_match_without_agent_relevance(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(TwitterState(username="alice", last_tweet_epoch=100))
            await session.commit()

        async def fake_fetch_user_tweets(username: str, count: int = 20, cursor: str | None = None):
            return {
                "screen_name": username,
                "latest_tweets": [
                    {
                        "tweetID": "101",
                        "date_epoch": 101,
                        "text": "Trump joins an NBA movie premiere with no market implications.",
                    },
                ],
            }

        adapter = DummyAdapter()
        with patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets):
            total = await run_twitter_monitor(["alice"], adapter, llm_processor=None)

        self.assertEqual(total, 0)
        self.assertEqual(adapter.cards, [])
        self.assertEqual(adapter.messages, [])

        async with session_factory() as session:
            post = (
                await session.execute(select(SocialPost).where(SocialPost.tweet_id == "101"))
            ).scalar_one()

        self.assertFalse(post.is_pushed)

    async def test_monitor_groups_similar_news_across_accounts(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(TwitterState(username="alice", last_tweet_epoch=100))
            session.add(TwitterState(username="bob", last_tweet_epoch=100))
            await session.commit()

        async def fake_fetch_user_tweets(username: str, count: int = 20, cursor: str | None = None):
            tweet_id = "201" if username == "alice" else "202"
            return {
                "screen_name": username,
                "latest_tweets": [
                    {
                        "tweetID": tweet_id,
                        "date_epoch": int(tweet_id),
                        "text": f"{username} on Trump tariff headline and market risk",
                        "expanded_urls": [
                            {
                                "expanded_url": (
                                    "https://example.com/news/trump-tariff?utm_source=x&ref=twitter"
                                )
                            }
                        ],
                    },
                ],
            }

        adapter = DummyAdapter()
        with patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets):
            total = await run_twitter_monitor(["alice", "bob"], adapter, MarketProcessor())

        self.assertEqual(total, 2)
        self.assertEqual(len(adapter.cards), 1)
        card = adapter.cards[0][1]
        pushed = "\n".join([card["title"], *card["sections"]])
        self.assertEqual(card["title"], "SPY · 特朗普 · 关税 · @alice, @bob 提到")
        self.assertIn("北京时间 1970-01-01 08:03", pushed)
        self.assertIn("2 条更新 · 2 位博主提到", pushed)
        self.assertIn("**市场**", pushed)
        self.assertIn("**事实**", pushed)
        self.assertIn("**观点**", pushed)
        self.assertIn("**影响和建议**", pushed)
        self.assertIn("@alice", pushed)
        self.assertIn("@bob", pushed)
        self.assertIn("关税", pushed)
        self.assertIn("alice 认为关税变化", pushed)
        self.assertIn("bob 认为关税变化", pushed)

        async with session_factory() as session:
            pushed_count = await session.scalar(
                select(func.count()).select_from(SocialPost).where(SocialPost.is_pushed.is_(True))
            )

        self.assertEqual(pushed_count, 2)

    async def test_monitor_adds_personal_impact_for_manual_stock_watch(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(TwitterState(username="alice", last_tweet_epoch=100))
            session.add(
                StockWatch(
                    ticker="NVDA",
                    chat_id="chat-1",
                    platform="feishu",
                    threshold_pct=5.0,
                    is_active=True,
                )
            )
            await session.commit()

        async def fake_fetch_user_tweets(username: str, count: int = 20, cursor: str | None = None):
            return {
                "screen_name": username,
                "latest_tweets": [
                    {
                        "tweetID": "301",
                        "date_epoch": 301,
                        "text": "Nvidia announces a major AI infrastructure order.",
                    },
                ],
            }

        adapter = DummyAdapter()
        with patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets):
            total = await run_twitter_monitor(["alice"], adapter, DummyProcessor())

        self.assertEqual(total, 1)
        self.assertEqual(len(adapter.cards), 1)
        card = adapter.cards[0][1]
        pushed = "\n".join([card["title"], *card["sections"]])
        self.assertIn("**影响和建议**", pushed)
        self.assertIn("关注/持仓: NVDA (观察列表)", pushed)
        self.assertIn("NVDA（观察列表）", pushed)
        self.assertIn("与你的观察列表相关", pushed)
        self.assertIn("出现明确催化", pushed)
        self.assertIn("建议关注后续价格、成交量和消息确认", pushed)

    async def test_monitor_adds_personal_impact_for_holding_marker(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(TwitterState(username="alice", last_tweet_epoch=100))
            await session.commit()
        await add_portfolio_holding_marker("NVDA")

        async def fake_fetch_user_tweets(username: str, count: int = 20, cursor: str | None = None):
            return {
                "screen_name": username,
                "latest_tweets": [
                    {
                        "tweetID": "302",
                        "date_epoch": 302,
                        "text": "Nvidia announces a major AI infrastructure order.",
                    },
                ],
            }

        adapter = DummyAdapter()
        with patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets):
            total = await run_twitter_monitor(["alice"], adapter, DummyProcessor())

        self.assertEqual(total, 1)
        self.assertEqual(len(adapter.cards), 1)
        pushed = "\n".join([adapter.cards[0][1]["title"], *adapter.cards[0][1]["sections"]])
        self.assertIn("**影响和建议**", pushed)
        self.assertIn("关注/持仓: NVDA (持仓关注标记，未记录数量/成本)", pushed)
        self.assertIn("NVDA（持仓）", pushed)
        self.assertIn("持仓关注标记，未记录数量/成本", pushed)
        self.assertIn("与你的持仓直接相关", pushed)


if __name__ == "__main__":
    unittest.main()
