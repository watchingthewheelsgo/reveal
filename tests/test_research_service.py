import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import ConversationMessage, ResearchSession, ResearchSource, SocialPost
from server.research.service import handle_topic_message, run_deep_research, start_topic


class DummyLLM:
    async def chat(self, messages, **kwargs) -> str:
        return "LLM answer"


class ResearchServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        db_engine.global_settings.search_provider = "none"
        await db_engine.init_db()

    async def asyncTearDown(self):
        await db_engine.close_db()
        self.tmpdir.cleanup()

    async def create_post(self) -> int:
        session_factory = get_session_factory()
        async with session_factory() as session:
            post = SocialPost(
                username="alice",
                tweet_id="101",
                tweet_url="https://x.com/alice/status/101",
                content="AI infra update https://example.com/report",
                links=["https://example.com/report"],
                referenced_tweets=[
                    {
                        "type": "quote",
                        "url": "https://x.com/bob/status/88",
                        "text": "quoted context",
                    }
                ],
                media=[],
                raw_json={},
                posted_at=datetime.now(UTC),
                is_pushed=True,
            )
            session.add(post)
            await session.flush()
            post_id = post.id
            await session.commit()
            return post_id

    async def test_deep_latest_creates_active_session_and_sources(self):
        post_id = await self.create_post()

        with patch("server.research.service.get_llm_client", return_value=None):
            run = await run_deep_research("chat-1", "latest", "AI infra")

        self.assertEqual(run.post.id, post_id)
        self.assertIn("LLM 未配置", run.answer)

        session_factory = get_session_factory()
        async with session_factory() as session:
            research_session = (
                await session.execute(
                    select(ResearchSession).where(ResearchSession.id == run.session_id)
                )
            ).scalar_one()
            sources = (
                (
                    await session.execute(
                        select(ResearchSource).where(ResearchSource.session_id == run.session_id)
                    )
                )
                .scalars()
                .all()
            )

        self.assertEqual(research_session.status, "active")
        self.assertGreaterEqual(len(sources), 3)

    async def test_active_topic_accepts_plain_followup_message(self):
        post_id = await self.create_post()
        topic = await start_topic("chat-1", str(post_id), "AI infra")

        with patch("server.research.service.get_llm_client", return_value=DummyLLM()):
            answer = await handle_topic_message("chat-1", "这个对 NVDA 有什么影响？")

        self.assertEqual(answer, "LLM answer")

        session_factory = get_session_factory()
        async with session_factory() as session:
            messages = (
                (
                    await session.execute(
                        select(ConversationMessage).where(
                            ConversationMessage.session_id == topic.id
                        )
                    )
                )
                .scalars()
                .all()
            )

        self.assertGreaterEqual(len(messages), 3)
        self.assertEqual(messages[-2].role, "user")
        self.assertEqual(messages[-1].role, "assistant")


if __name__ == "__main__":
    unittest.main()
