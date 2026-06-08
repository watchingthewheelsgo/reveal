import tempfile
import unittest
from pathlib import Path

from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import InteractionThread
from server.delivery.service import send_alert, send_alert_to_admin
from server.events.types import AlertCandidate
from server.interactions.threading import get_or_create_thread_for_source


class DummyAdapter:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))


class ReturnIdAdapter(DummyAdapter):
    async def send_message_returning_id(self, chat_id: str, text: str) -> str | None:
        await self.send_message(chat_id, text)
        return "msg-1"


class AdminReturnIdAdapter(DummyAdapter):
    def __init__(self):
        super().__init__()
        self.admin_chat_ids = ["chat-1", "chat-2"]

    async def send_message_returning_id(self, chat_id: str, text: str) -> str | None:
        await self.send_message(chat_id, text)
        return f"msg-{chat_id}"


class AlertDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-delivery-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        await db_engine.init_db()

    async def asyncTearDown(self):
        await db_engine.close_db()
        self.tmpdir.cleanup()

    async def test_send_alert_dedupes_per_event_platform_chat(self):
        adapter = DummyAdapter()
        candidate = AlertCandidate(
            event_key="stock_watch:1:100:106",
            event_type="stock_watch",
            title="AAPL alert",
            summary="moved",
            severity="warning",
        )

        first = await send_alert(
            adapter,
            candidate,
            chat_id="chat-1",
            text="AAPL moved",
            platform="telegram",
        )
        second = await send_alert(
            adapter,
            candidate,
            chat_id="chat-1",
            text="AAPL moved again",
            platform="telegram",
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.status, "sent")
        self.assertEqual(adapter.messages, [("chat-1", "AAPL moved")])

    async def test_send_alert_records_message_id_when_available(self):
        adapter = ReturnIdAdapter()
        candidate = AlertCandidate(
            event_key="regulatory:abc",
            event_type="regulatory",
            title="8-K",
            summary="filed",
        )

        delivery = await send_alert(
            adapter,
            candidate,
            chat_id="chat-1",
            text="8-K filed",
            platform="feishu",
        )

        self.assertEqual(delivery.message_id, "msg-1")
        self.assertEqual(delivery.status, "sent")

    async def test_repeat_allowed_creates_multiple_delivery_rows(self):
        adapter = DummyAdapter()
        candidate = AlertCandidate(
            event_key="news:NVDA:headline",
            event_type="news",
            title="NVDA news",
            summary="headline",
            dedupe_policy="repeat_allowed",
        )

        first = await send_alert(adapter, candidate, chat_id="chat-1", text="first")
        second = await send_alert(adapter, candidate, chat_id="chat-1", text="second")

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(len(adapter.messages), 2)

    async def test_send_alert_to_admin_uses_thread_factory_per_chat(self):
        adapter = AdminReturnIdAdapter()
        candidate = AlertCandidate(
            event_key="regulatory:abc",
            event_type="regulatory",
            source_id=123,
            title="8-K",
            summary="filed",
        )

        async def thread_for_chat(chat_id: str) -> int:
            thread = await get_or_create_thread_for_source(
                chat_id=chat_id,
                platform="feishu",
                source_type="regulatory",
                source_id=123,
            )
            return thread.id

        deliveries = await send_alert_to_admin(
            adapter,
            candidate,
            text="8-K filed",
            platform="feishu",
            thread_factory=thread_for_chat,
        )

        self.assertEqual(len(deliveries), 2)
        self.assertNotEqual(deliveries[0].thread_id, deliveries[1].thread_id)
        session_factory = get_session_factory()
        async with session_factory() as session:
            threads = [
                await session.get(InteractionThread, delivery.thread_id) for delivery in deliveries
            ]
        self.assertEqual({thread.chat_id for thread in threads if thread}, {"chat-1", "chat-2"})


if __name__ == "__main__":
    unittest.main()
