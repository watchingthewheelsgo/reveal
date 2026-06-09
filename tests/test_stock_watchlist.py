import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import AlertDelivery, InteractionThread
from server.events.feed import get_event_detail_for_thread
from server.stock.watchlist import (
    add_stock_watch,
    format_stock_watch_list,
    get_manual_stock_watch_tickers,
    get_stock_watch_list_payload,
    remove_stock_watch,
    run_stock_watch_price_cycle,
)


class DummyAdapter:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str) -> None:
        self.messages.append((chat_id, text))


class TelegramCardAdapter(DummyAdapter):
    def __init__(self):
        super().__init__()
        self.cards: list[tuple[str, dict]] = []

    async def send_card_returning_id(self, chat_id: str, card: dict) -> str | None:
        self.cards.append((chat_id, card))
        return f"card-{len(self.cards)}"


class StockWatchlistTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_add_list_and_remove_stock_watch(self):
        with patch("server.stock.watchlist.get_current_price", return_value=100.0):
            added = await add_stock_watch("nvda", "chat-1", platform="telegram")

        self.assertEqual(added["ticker"], "NVDA")
        self.assertEqual(added["last_price"], 100.0)
        self.assertEqual(await get_manual_stock_watch_tickers(), ["NVDA"])

        payload = await get_stock_watch_list_payload("chat-1")
        self.assertEqual(payload["count"], 1)
        self.assertIn("NVDA", format_stock_watch_list(payload))

        removed = await remove_stock_watch("NVDA", "chat-1")
        self.assertEqual(removed["removed"], 1)
        self.assertEqual(await get_manual_stock_watch_tickers(), [])

    async def test_price_cycle_alerts_when_move_exceeds_threshold(self):
        with patch("server.stock.watchlist.get_current_price", return_value=100.0):
            await add_stock_watch("AAPL", "chat-1", platform="telegram", threshold_pct=5.0)

        adapter = DummyAdapter()
        with patch("server.stock.watchlist.get_current_price", return_value=106.0):
            alerts = await run_stock_watch_price_cycle({"telegram": adapter})

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["ticker"], "AAPL")
        self.assertEqual(adapter.messages[0][0], "chat-1")
        self.assertIn("AAPL", adapter.messages[0][1])
        self.assertIn("6.0%", adapter.messages[0][1])

    async def test_price_cycle_updates_baseline_without_alert_below_threshold(self):
        with patch("server.stock.watchlist.get_current_price", return_value=100.0):
            await add_stock_watch("MSFT", "chat-1", platform="telegram", threshold_pct=5.0)

        adapter = DummyAdapter()
        with patch("server.stock.watchlist.get_current_price", return_value=103.0):
            alerts = await run_stock_watch_price_cycle({"telegram": adapter})

        self.assertEqual(alerts, [])
        self.assertEqual(adapter.messages, [])
        payload = await get_stock_watch_list_payload("chat-1")
        self.assertEqual(payload["items"][0]["last_price"], 103.0)

    async def test_each_stock_watch_alert_gets_its_own_thread(self):
        with patch("server.stock.watchlist.get_current_price", return_value=100.0):
            await add_stock_watch("AAPL", "chat-1", platform="telegram", threshold_pct=5.0)

        adapter = TelegramCardAdapter()
        with patch("server.stock.watchlist.get_current_price", return_value=106.0):
            first_alerts = await run_stock_watch_price_cycle({"telegram": adapter})
        with patch("server.stock.watchlist.get_current_price", return_value=112.0):
            second_alerts = await run_stock_watch_price_cycle({"telegram": adapter})

        self.assertEqual(len(first_alerts), 1)
        self.assertEqual(len(second_alerts), 1)
        self.assertEqual(len(adapter.cards), 2)

        session_factory = get_session_factory()
        async with session_factory() as session:
            threads = (
                (
                    await session.execute(
                        select(InteractionThread)
                        .where(InteractionThread.source_type == "stock_watch")
                        .order_by(InteractionThread.id)
                    )
                )
                .scalars()
                .all()
            )
            deliveries = (
                (
                    await session.execute(
                        select(AlertDelivery)
                        .where(AlertDelivery.event_type == "stock_watch")
                        .order_by(AlertDelivery.id)
                    )
                )
                .scalars()
                .all()
            )

        self.assertEqual(len(threads), 2)
        self.assertEqual(len(deliveries), 2)
        self.assertNotEqual(threads[0].source_key, threads[1].source_key)
        self.assertEqual(threads[0].root_message_id, "card-1")
        self.assertEqual(threads[1].root_message_id, "card-2")
        self.assertEqual(
            {delivery.thread_id for delivery in deliveries},
            {thread.id for thread in threads},
        )

        first_detail = await get_event_detail_for_thread("stock_watch", None, threads[0].id)
        second_detail = await get_event_detail_for_thread("stock_watch", None, threads[1].id)
        self.assertIsNotNone(first_detail)
        self.assertIsNotNone(second_detail)
        assert first_detail is not None
        assert second_detail is not None
        self.assertIn("106.00", first_detail["record"]["event_key"])
        self.assertIn("112.00", second_detail["record"]["event_key"])


if __name__ == "__main__":
    unittest.main()
