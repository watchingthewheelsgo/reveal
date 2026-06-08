import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from server.bot.base import BotContext
from server.commands import handle_plain_message
from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import RegulatoryEvent, ResearchSession
from server.interactions.threading import (
    attach_research_session,
    bind_message_to_thread,
    create_agent_thread,
    get_or_create_thread_for_source,
    resolve_thread_by_message,
)


class InteractionThreadingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-thread-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        await db_engine.init_db()

    async def asyncTearDown(self):
        await db_engine.close_db()
        self.tmpdir.cleanup()

    async def test_create_bind_and_resolve_thread(self):
        thread = await get_or_create_thread_for_source(
            chat_id="chat-1",
            platform="feishu",
            source_type="twitter",
            source_id=12,
            root_message_id="msg-root",
        )
        await bind_message_to_thread(
            chat_id="chat-1",
            message_id="msg-root",
            thread_id=thread.id,
            platform="feishu",
            role="root",
            source_type="twitter",
            source_id=12,
        )

        resolved = await resolve_thread_by_message("chat-1", "msg-root")

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.id, thread.id)
        self.assertEqual(resolved.source_type, "twitter")

    async def test_agent_thread_can_attach_research_session(self):
        thread = await create_agent_thread(
            chat_id="chat-1",
            platform="telegram",
            root_message_id="user-msg",
        )

        await attach_research_session(thread.id, 42)
        resolved = await get_or_create_thread_for_source(
            chat_id="chat-1",
            platform="telegram",
            source_type="agent",
            source_id=None,
            root_message_id="user-msg",
        )

        self.assertEqual(resolved.id, thread.id)
        self.assertEqual(resolved.research_session_id, 42)

    async def test_reply_to_regulatory_thread_creates_event_topic(self):
        session_factory = get_session_factory()
        now = datetime.now(UTC).replace(tzinfo=None)
        async with session_factory() as session:
            event = RegulatoryEvent(
                source="sec",
                event_id="sec:abc",
                ticker="NVDA",
                event_type="SEC Filing",
                severity="warning",
                title="NVDA 8-K filed",
                detail="Material event",
                event_date=now,
            )
            session.add(event)
            await session.flush()
            event_id = event.id
            await session.commit()

        thread = await get_or_create_thread_for_source(
            chat_id="chat-1",
            platform="feishu",
            source_type="regulatory",
            source_id=event_id,
            root_message_id="msg-root",
        )
        await bind_message_to_thread(
            chat_id="chat-1",
            message_id="msg-root",
            thread_id=thread.id,
            platform="feishu",
            role="source",
            source_type="regulatory",
            source_id=event_id,
        )

        captured: dict[str, int | str | None] = {}

        async def noop():
            return None

        def fake_job(chat_id, text, adapter_arg, reply_to="", session_id=None, thread_id=None):
            captured.update(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "reply_to": reply_to,
                    "session_id": session_id,
                    "thread_id": thread_id,
                }
            )
            return noop()

        def fake_spawn(coro, label: str) -> None:
            captured["label"] = label
            coro.close()

        ctx = BotContext(
            chat_id="chat-1",
            user_id="user-1",
            text="这个对 NVDA 有什么影响？",
            message_id="msg-reply",
            reply_to_message_id="msg-root",
        )
        adapter = DummyAdapter()

        with (
            patch("server.commands._run_topic_message_job", new=fake_job),
            patch("server.commands._spawn_background_task", new=fake_spawn),
        ):
            await handle_plain_message(ctx, adapter)

        self.assertEqual(captured["label"], "thread source message")
        self.assertEqual(captured["thread_id"], thread.id)
        self.assertIsNotNone(captured["session_id"])
        async with session_factory() as session:
            topic = (
                await session.execute(
                    select(ResearchSession).where(ResearchSession.id == captured["session_id"])
                )
            ).scalar_one()
        self.assertEqual(topic.source_type, "regulatory")
        self.assertEqual(topic.source_id, event_id)
        self.assertIn("NVDA 8-K filed", topic.source_query or "")


class DummyAdapter:
    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
