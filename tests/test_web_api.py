import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import JobRun, ResearchSession, SocialPost
from server.web import get_post, list_events, list_posts, list_system_jobs, list_system_modules


class WebApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-web-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        await db_engine.init_db()

    async def asyncTearDown(self):
        await db_engine.close_db()
        self.tmpdir.cleanup()

    async def test_post_list_and_detail_include_research_context(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            post = SocialPost(
                username="alice",
                tweet_id="200",
                tweet_url="https://x.com/alice/status/200",
                content="AI capex update with supplier notes",
                summary="AI capex summary",
                links=["https://example.com/report"],
                media=[{"url": "https://example.com/chart.png", "type": "image"}],
                referenced_tweets=[{"type": "quote", "url": "https://x.com/bob/status/1"}],
                raw_json={},
                is_noteworthy=True,
                attention_reason="High signal update",
                posted_at=datetime.now(UTC),
                is_pushed=True,
            )
            session.add(post)
            await session.flush()
            session.add(
                ResearchSession(
                    chat_id="web",
                    source_type="twitter",
                    source_id=post.id,
                    topic="AI capex",
                    status="active",
                    answer="Research answer",
                )
            )
            post_id = post.id
            await session.commit()

        posts_payload = await list_posts(limit=80, username=None, q=None)
        self.assertEqual(posts_payload["count"], 1)
        item = posts_payload["posts"][0]
        self.assertEqual(item["id"], post_id)
        self.assertEqual(item["link_count"], 1)
        self.assertEqual(item["media_count"], 1)
        self.assertEqual(item["reference_count"], 1)
        self.assertTrue(item["is_noteworthy"])
        self.assertEqual(item["attention_reason"], "High signal update")
        self.assertEqual(item["research"]["status"], "active")

        detail_payload = await get_post(post_id)
        self.assertEqual(detail_payload["post"]["tweet_url"], "https://x.com/alice/status/200")
        self.assertEqual(detail_payload["post"]["links"], ["https://example.com/report"])
        self.assertEqual(detail_payload["research_sessions"][0]["answer"], "Research answer")

        events_payload = await list_events(limit=80, source_type=None, ticker=None, q=None)
        self.assertEqual(events_payload["count"], 1)
        self.assertEqual(events_payload["events"][0]["source_type"], "twitter")
        self.assertTrue(events_payload["events"][0]["has_research"])

    async def test_system_modules_and_jobs_api(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(
                JobRun(
                    job_id="twitter_monitor",
                    module_id="twitter_monitor",
                    status="succeeded",
                    summary="ok",
                    metrics={"count": 1},
                )
            )
            await session.commit()

        modules = await list_system_modules()
        jobs = await list_system_jobs(limit=20, job_id=None)

        self.assertIn("data_sources", modules)
        self.assertIn("system_modules", modules)
        self.assertEqual(jobs["jobs"][0]["job_id"], "twitter_monitor")


if __name__ == "__main__":
    unittest.main()
