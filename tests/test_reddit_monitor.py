import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from server.bot.base import BotAdapter
from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import RedditState, SocialPost
from server.social.processor import TweetAnalysis
from server.social.reddit import RedditPost, run_reddit_monitor, set_reddit_subreddit_active


class DummyAdapter(BotAdapter):
    def __init__(self):
        self.admin_chat_ids = ["admin"]
        self.cards: list[tuple[str, dict]] = []
        self.messages: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))

    async def send_card(self, chat_id: str, card: dict) -> None:
        self.cards.append((chat_id, card))

    async def send_card_returning_id(self, chat_id: str, card: dict) -> str | None:
        self.cards.append((chat_id, card))
        return f"card-{len(self.cards)}"

    async def push_to_admin(self, text: str) -> None:
        self.messages.append(("admin", text))

    def register_command(self, command: str, handler) -> None:
        return None


class MarketOnlyProcessor:
    async def analyze(self, context: str, author: str = "") -> TweetAnalysis:
        is_market = "NVIDIA" in context
        return TweetAnalysis(
            summary="NVIDIA Reddit 讨论热度上升" if is_market else "游戏帖子",
            translation=None,
            mentioned_tickers=["NVDA"] if is_market else [],
            topics=["AI芯片"] if is_market else ["游戏"],
            sentiment="bullish" if is_market else "neutral",
            urgency="medium" if is_market else "low",
            urgency_reason="Reddit 讨论可能影响散户情绪。" if is_market else "无市场相关性。",
            is_market_relevant=is_market,
            is_noteworthy=is_market,
            attention_reason="r/stocks 讨论 NVIDIA 业绩和 AI 需求。" if is_market else "",
        )


class RedditMonitorTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_set_reddit_subreddit_active_creates_watch_state(self):
        await set_reddit_subreddit_active("r/stocks", True)

        async with get_session_factory()() as session:
            state = await session.scalar(
                select(RedditState).where(RedditState.subreddit == "stocks")
            )

        self.assertIsNotNone(state)
        assert state is not None
        self.assertTrue(state.is_active)

    async def test_reddit_monitor_pushes_only_market_relevant_posts(self):
        posts = [
            RedditPost(
                id="aaa",
                subreddit="stocks",
                title="NVIDIA earnings discussion accelerates on AI demand",
                permalink="/r/stocks/comments/aaa/nvidia/",
                author="investor",
                selftext="NVIDIA demand and margins are the focus.",
                score=120,
                upvote_ratio=0.91,
                num_comments=80,
                created_utc=200,
            ),
            RedditPost(
                id="bbb",
                subreddit="stocks",
                title="Weekend gaming setup thread",
                permalink="/r/stocks/comments/bbb/gaming/",
                author="gamer",
                selftext="No market relevance.",
                score=90,
                upvote_ratio=0.88,
                num_comments=20,
                created_utc=210,
            ),
        ]

        async def fake_fetch(subreddit: str):
            return posts

        adapter = DummyAdapter()
        with patch("server.social.reddit.fetch_subreddit_posts", new=fake_fetch):
            pushed = await run_reddit_monitor(["stocks"], adapter, MarketOnlyProcessor())

        self.assertEqual(pushed, 1)
        self.assertEqual(len(adapter.cards), 1)
        self.assertIn("NVDA", adapter.cards[0][1]["title"])
        async with get_session_factory()() as session:
            post_count = await session.scalar(select(func.count()).select_from(SocialPost))
            pushed_count = await session.scalar(
                select(func.count()).select_from(SocialPost).where(SocialPost.is_pushed.is_(True))
            )
            reddit_posts = (
                (await session.execute(select(SocialPost).order_by(SocialPost.tweet_id.asc())))
                .scalars()
                .all()
            )

        self.assertEqual(post_count, 2)
        self.assertEqual(pushed_count, 1)
        self.assertEqual(
            [post.tweet_id for post in reddit_posts], ["reddit:stocks:aaa", "reddit:stocks:bbb"]
        )


if __name__ == "__main__":
    unittest.main()
