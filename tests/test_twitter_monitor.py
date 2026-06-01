import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from server.bot.base import BotAdapter
from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import SocialPost, TwitterState
from server.social.monitor import check_and_notify


class DummyAdapter(BotAdapter):
    def __init__(self):
        self.admin_chat_ids = ["admin"]
        self.messages: list[tuple[str, str]] = []
        self.cards: list[tuple[str, dict]] = []

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))

    async def send_card(self, chat_id: str, card: dict) -> None:
        self.cards.append((chat_id, card))

    def register_command(self, command: str, handler) -> None:
        return None

    async def push_to_admin(self, text: str) -> None:
        self.messages.append(("admin", text))


class DummyProcessor:
    def __init__(self):
        self.summary_input = ""

    async def translate(self, text: str) -> str:
        return f"译文: {text}"

    async def summarize(self, text: str) -> str:
        self.summary_input = text
        return "测试摘要"


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

    async def test_first_check_backfills_latest_ten_tweets(self):
        async def fake_fetch_user_tweets(username: str):
            return {
                "screen_name": username,
                "latest_tweets": [
                    {"tweetID": str(tweet_id), "date_epoch": tweet_id, "text": f"tweet {tweet_id}"}
                    for tweet_id in range(101, 113)
                ],
            }

        adapter = DummyAdapter()
        with patch("server.social.monitor.fetch_user_tweets", new=fake_fetch_user_tweets):
            posts = await check_and_notify("alice", adapter)

        self.assertEqual(len(posts), 10)
        self.assertEqual(len(adapter.cards), 1)
        self.assertEqual(adapter.cards[0][0], "admin")
        digest = "\n".join([adapter.cards[0][1]["title"], *adapter.cards[0][1]["sections"]])
        self.assertIn("已缓存最近 10 条更新", digest)
        self.assertIn("显示最近 5 条", digest)
        self.assertIn("tweet 112", digest)
        self.assertIn("tweet 108", digest)
        self.assertNotIn("tweet 103", digest)
        self.assertIn("/research", digest)
        self.assertIn("/deep", digest)

        session_factory = get_session_factory()
        async with session_factory() as session:
            state = (
                await session.execute(select(TwitterState).where(TwitterState.username == "alice"))
            ).scalar_one()
            post_count = await session.scalar(select(func.count()).select_from(SocialPost))
            old_post = (
                await session.execute(select(SocialPost).where(SocialPost.tweet_id == "101"))
            ).scalar_one_or_none()
            latest_post = (
                await session.execute(select(SocialPost).where(SocialPost.tweet_id == "112"))
            ).scalar_one()

        self.assertEqual(state.last_tweet_epoch, 112)
        self.assertEqual(post_count, 10)
        self.assertIsNone(old_post)
        self.assertTrue(latest_post.is_pushed)

    async def test_new_tweet_stores_and_pushes_rich_metadata(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(TwitterState(username="alice", last_tweet_epoch=100))
            await session.commit()

        async def fake_fetch_user_tweets(username: str):
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
        self.assertIn("有 1 条新更新", pushed)
        self.assertIn("https://x.com/alice/status/101", pushed)
        self.assertIn("1 link / 1 media / 1 ref", pushed)
        self.assertIn("/research", pushed)
        self.assertIn("/deep", pushed)
        self.assertIn("引用", pushed)
        self.assertIn("quoted context", processor.summary_input)

        async with session_factory() as session:
            post = (
                await session.execute(select(SocialPost).where(SocialPost.tweet_id == "101"))
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
        self.assertTrue(post.is_pushed)


if __name__ == "__main__":
    unittest.main()
