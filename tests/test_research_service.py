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
    get_or_start_topic_for_post,
    handle_topic_message,
    run_agent_session_message,
    run_deep_research,
    start_agent_session,
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

    async def create_post(
        self,
        tweet_id: str = "101",
        username: str = "alice",
        content: str = "AI infra update https://example.com/report",
        summary: str | None = None,
        topics: list[str] | None = None,
        mentioned_tickers: list[str] | None = None,
        urgency: str | None = None,
        is_noteworthy: bool = False,
    ) -> int:
        session_factory = get_session_factory()
        async with session_factory() as session:
            post = SocialPost(
                username=username,
                tweet_id=tweet_id,
                tweet_url=f"https://x.com/{username}/status/{tweet_id}",
                content=content,
                summary=summary,
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
                mentioned_tickers=mentioned_tickers,
                topics=topics,
                urgency=urgency,
                is_noteworthy=is_noteworthy,
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

    async def test_tweet_research_prompt_includes_typed_event_and_market_skills(self):
        post_id = await self.create_post(
            content="Trump tariff headline could hit chip supply chains and market risk.",
            summary="特朗普关税新闻可能影响芯片供应链。",
            topics=["tariff", "policy"],
            mentioned_tickers=["NVDA"],
            urgency="high",
            is_noteworthy=True,
        )
        calls: list[tuple[str, str | None]] = []

        async def fake_run_agent(
            prompt: str,
            resume: str | None = None,
            on_progress=None,
        ) -> AgentRunResult:
            calls.append((prompt, resume))
            return AgentRunResult("agent answer", "agent-session-market-skill")

        with patch("server.research.service.run_agent", new=fake_run_agent):
            await run_deep_research("chat-1", str(post_id), "impact")

        self.assertEqual(len(calls), 1)
        prompt = calls[0][0]
        self.assertIn("canonical_event", prompt)
        self.assertIn("source_specific_fields", prompt)
        self.assertIn("tweet_id", prompt)
        self.assertIn("Market skills to consider", prompt)
        self.assertIn("macro_policy", prompt)
        self.assertIn("bear_case", prompt)
        self.assertIn("facts", prompt.lower())

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

    async def test_top_level_agent_session_is_isolated_from_active_topic(self):
        post_id = await self.create_post()
        topic = await start_topic("chat-1", str(post_id), "AI infra")
        agent_session = await start_agent_session("chat-1", "加上 @aleabitoreddit")
        calls: list[tuple[str, str | None]] = []

        async def fake_run_agent(
            prompt: str,
            resume: str | None = None,
            on_progress=None,
        ) -> AgentRunResult:
            calls.append((prompt, resume))
            return AgentRunResult("watch list updated", "agent-session-top")

        with patch("server.research.service.run_agent", new=fake_run_agent):
            answer = await run_agent_session_message(agent_session, "加上 @aleabitoreddit")

        self.assertEqual(answer, "watch list updated")
        self.assertEqual(len(calls), 1)
        self.assertIn("Twitter watch list", calls[0][0])

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
            topic_after = (
                await session.execute(select(ResearchSession).where(ResearchSession.id == topic.id))
            ).scalar_one()
            agent_after = (
                await session.execute(
                    select(ResearchSession).where(ResearchSession.id == agent_session.id)
                )
            ).scalar_one()

        self.assertEqual({item.status for item in sessions}, {"active"})
        self.assertEqual(topic_after.source_type, "twitter")
        self.assertEqual(agent_after.source_type, "agent")
        self.assertEqual(agent_after.agent_session_id, "agent-session-top")

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

    async def test_topic_message_can_target_source_bound_session(self):
        first_post_id = await self.create_post(
            tweet_id="101",
            username="alice",
            content="First thread about NVDA supply chain",
        )
        second_post_id = await self.create_post(
            tweet_id="202",
            username="bob",
            content="Second thread about TSLA delivery data",
        )
        first_topic = await start_topic("chat-1", str(first_post_id), "first")
        second_topic = await get_or_start_topic_for_post("chat-1", str(second_post_id), "second")
        calls: list[tuple[str, str | None]] = []

        async def fake_run_agent(
            prompt: str,
            resume: str | None = None,
            on_progress=None,
        ) -> AgentRunResult:
            calls.append((prompt, resume))
            return AgentRunResult("first topic answer", "agent-session-first")

        with patch("server.research.service.run_agent", new=fake_run_agent):
            answer = await handle_topic_message(
                "chat-1",
                "继续这个 thread",
                session_id=first_topic.id,
            )

        self.assertEqual(answer, "first topic answer")
        self.assertEqual(len(calls), 1)
        self.assertIn("First thread about NVDA", calls[0][0])
        self.assertNotIn("Second thread about TSLA", calls[0][0])

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
            messages = (
                (
                    await session.execute(
                        select(ConversationMessage).where(
                            ConversationMessage.session_id == first_topic.id
                        )
                    )
                )
                .scalars()
                .all()
            )

        self.assertEqual(first_topic.status, "active")
        self.assertEqual(second_topic.status, "active")
        self.assertEqual({item.status for item in sessions}, {"active"})
        self.assertEqual(messages[-2].role, "user")
        self.assertEqual(messages[-1].role, "assistant")

    async def test_topic_message_rebuilds_context_on_generic_resume_runtime_failure(self):
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
                    "Agent SDK execution failed.",
                    "研究 Agent 执行失败，请稍后重试。",
                )
            return AgentRunResult("rebuilt after generic failure", "fresh-session")

        with patch("server.research.service.run_agent", new=fake_run_agent):
            answer = await handle_topic_message("chat-1", "继续分析")

        self.assertEqual(answer, "rebuilt after generic failure")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "stale-session")
        self.assertIsNone(calls[1][1])

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
