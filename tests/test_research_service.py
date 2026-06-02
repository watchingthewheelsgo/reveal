import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import ConversationMessage, ResearchSession, SocialPost
from server.research.claude_sdk_runtime import AgentRunResult, AgentRuntimeError
from server.research.service import (
    ResearchError,
    handle_topic_message,
    run_deep_research,
    start_topic,
)


class ResearchServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        db_engine.global_settings.openai_api_key = "test-key"
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

    async def test_deep_latest_uses_agent_sdk_runtime(self):
        post_id = await self.create_post()
        calls: list[tuple[str, str | None]] = []

        async def fake_run_agent(
            prompt: str,
            resume: str | None = None,
            on_progress=None,
        ) -> AgentRunResult:
            calls.append((prompt, resume))
            return AgentRunResult("agent answer", "agent-session-1")

        with patch("server.research.service.run_agent", new=fake_run_agent):
            run = await run_deep_research("chat-1", "latest", "AI infra")

        self.assertIsNotNone(run.post)
        self.assertEqual(run.post.id, post_id)  # type: ignore[union-attr]
        self.assertEqual(run.answer, "agent answer")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0][1])
        self.assertIn("WebSearch", calls[0][0])
        self.assertIn("AI infra", calls[0][0])

        session_factory = get_session_factory()
        async with session_factory() as session:
            research_session = (
                await session.execute(
                    select(ResearchSession).where(ResearchSession.id == run.session_id)
                )
            ).scalar_one()

        self.assertEqual(research_session.status, "active")
        self.assertEqual(research_session.agent_runtime, "claude_sdk")
        self.assertEqual(research_session.agent_session_id, "agent-session-1")

    async def test_active_topic_accepts_plain_followup_message(self):
        post_id = await self.create_post()
        topic = await start_topic("chat-1", str(post_id), "AI infra")
        calls: list[tuple[str, str | None]] = []

        async def fake_run_agent(
            prompt: str,
            resume: str | None = None,
            on_progress=None,
        ) -> AgentRunResult:
            calls.append((prompt, resume))
            return AgentRunResult("LLM answer", "agent-session-2")

        with patch("server.research.service.run_agent", new=fake_run_agent):
            answer = await handle_topic_message("chat-1", "这个对 NVDA 有什么影响？")

        self.assertEqual(answer, "LLM answer")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0][1])
        self.assertIn("NVDA", calls[0][0])

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
            research_session = (
                await session.execute(select(ResearchSession).where(ResearchSession.id == topic.id))
            ).scalar_one()

        self.assertGreaterEqual(len(messages), 3)
        self.assertEqual(messages[-2].role, "user")
        self.assertEqual(messages[-1].role, "assistant")
        self.assertEqual(research_session.agent_session_id, "agent-session-2")

    async def test_deep_research_failure_does_not_replace_active_topic(self):
        post_id = await self.create_post()
        topic = await start_topic("chat-1", str(post_id), "AI infra")

        async def fake_run_agent(
            prompt: str,
            resume: str | None = None,
            on_progress=None,
        ) -> AgentRunResult:
            raise AgentRuntimeError("authentication failed", "研究 Agent 认证失败")

        with patch("server.research.service.run_agent", new=fake_run_agent):
            with self.assertRaises(ResearchError):
                await run_deep_research("chat-1", "latest", "AI infra")

        session_factory = get_session_factory()
        async with session_factory() as session:
            sessions = (
                (
                    await session.execute(
                        select(ResearchSession).where(ResearchSession.chat_id == "chat-1")
                    )
                )
                .scalars()
                .all()
            )

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].id, topic.id)
        self.assertEqual(sessions[0].status, "active")

    async def test_topic_message_rebuilds_context_when_agent_resume_fails(self):
        post_id = await self.create_post()
        topic = await start_topic("chat-1", str(post_id), "AI infra")
        await self.set_agent_session_id(topic.id, "stale-session")
        calls: list[tuple[str, str | None]] = []

        async def fake_run_agent(
            prompt: str,
            resume: str | None = None,
            on_progress=None,
        ) -> AgentRunResult:
            calls.append((prompt, resume))
            if resume == "stale-session":
                raise AgentRuntimeError(
                    "No conversation found with session ID stale-session",
                    "研究 Agent 会话恢复失败",
                )
            return AgentRunResult("rebuilt answer", "fresh-session")

        with patch("server.research.service.run_agent", new=fake_run_agent):
            answer = await handle_topic_message("chat-1", "继续分析供应链影响")

        self.assertEqual(answer, "rebuilt answer")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "stale-session")
        self.assertIsNone(calls[1][1])
        self.assertIn("上一个 Agent 会话无法恢复", calls[1][0])
        self.assertIn("已基于 @alice 的更新开启研究线程", calls[1][0])

        session_factory = get_session_factory()
        async with session_factory() as session:
            research_session = (
                await session.execute(select(ResearchSession).where(ResearchSession.id == topic.id))
            ).scalar_one()

        self.assertEqual(research_session.agent_session_id, "fresh-session")

    async def set_agent_session_id(self, session_id: int, agent_session_id: str) -> None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            research_session = (
                await session.execute(
                    select(ResearchSession).where(ResearchSession.id == session_id)
                )
            ).scalar_one()
            research_session.agent_session_id = agent_session_id
            await session.commit()


if __name__ == "__main__":
    unittest.main()
