import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from server.alerts.engine import run_alert_cycle
from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import AlertDelivery, InteractionThread


class FeishuAdminAdapter:
    def __init__(self):
        self.admin_chat_ids = ["chat-1"]
        self.cards: list[tuple[str, dict]] = []

    async def send_card_returning_id(self, chat_id: str, card: dict) -> str | None:
        self.cards.append((chat_id, card))
        return f"card-{chat_id}"

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        raise AssertionError("structured alert should be sent as a card")


class AlertEngineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-alert-engine-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        await db_engine.init_db()

    async def asyncTearDown(self):
        await db_engine.close_db()
        self.tmpdir.cleanup()

    async def test_intraday_alert_cycle_creates_interaction_thread(self):
        adapter = FeishuAdminAdapter()
        price_alert = {
            "ticker": "NVDA",
            "type": "价格异动",
            "severity": "warning",
            "price": 125.5,
            "change_pct": 5.2,
            "message": "📈 上涨 5.2%",
            "detail": "日内波动超过 5% 阈值",
        }

        with (
            patch("server.alerts.engine._is_market_hours", return_value=True),
            patch("server.journal.service.get_trades_for_period", return_value=[]),
            patch("server.stock.tracker.get_active_tickers", return_value=["NVDA"]),
            patch("server.stock.watchlist.get_manual_stock_watch_tickers", return_value=[]),
            patch("server.alerts.engine.check_price_alerts", return_value=[price_alert]),
            patch("server.alerts.engine.check_volume_alerts", return_value=[]),
            patch("server.alerts.engine.check_news_alerts", return_value=[]),
        ):
            await run_alert_cycle(adapter)

        self.assertEqual(len(adapter.cards), 1)
        self.assertEqual(adapter.cards[0][0], "chat-1")
        self.assertEqual(adapter.cards[0][1]["title"], "Reveal · 市场事件")

        session_factory = get_session_factory()
        async with session_factory() as session:
            delivery = (
                await session.execute(
                    select(AlertDelivery).where(AlertDelivery.event_type == "price")
                )
            ).scalar_one()
            thread = await session.get(InteractionThread, delivery.thread_id)

        self.assertEqual(delivery.status, "sent")
        self.assertEqual(delivery.message_id, "card-chat-1")
        self.assertEqual(delivery.platform, "feishu")
        self.assertIsNotNone(thread)
        assert thread is not None
        self.assertEqual(thread.source_type, "price")
        self.assertEqual(thread.source_key, delivery.event_key)
        self.assertEqual(thread.root_message_id, "card-chat-1")


if __name__ == "__main__":
    unittest.main()
