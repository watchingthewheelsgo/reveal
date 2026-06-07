import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.settings import get_settings
from server.alerts.market_movers import (
    format_market_mover_alert,
    get_recent_market_movers,
    normalize_market_mover_event,
    persist_new_market_mover_events,
    run_market_mover_alert_cycle,
)
from server.db import engine as db_engine


class DummyAdminAdapter:
    def __init__(self):
        self.messages: list[str] = []

    async def push_to_admin(self, text: str) -> None:
        self.messages.append(text)


class MarketMoversTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        await db_engine.init_db()

        settings = get_settings()
        self.original = {
            "longbridge_enabled": settings.longbridge_enabled,
            "longbridge_oauth_token_path": settings.longbridge_oauth_token_path,
            "longbridge_movers_enabled": settings.longbridge_movers_enabled,
            "longbridge_movers_push_limit": settings.longbridge_movers_push_limit,
        }
        settings.longbridge_enabled = True
        settings.longbridge_oauth_token_path = "/tmp/reveal-longbridge-token.json"
        settings.longbridge_movers_enabled = True
        settings.longbridge_movers_push_limit = 10

    async def asyncTearDown(self):
        settings = get_settings()
        for key, value in self.original.items():
            setattr(settings, key, value)
        await db_engine.close_db()
        self.tmpdir.cleanup()

    def test_normalize_market_mover_event(self):
        event = normalize_market_mover_event(
            {
                "id": "266169017",
                "counter_id": "ST/US/VIAV",
                "alert_name": "大笔买入",
                "alert_time": "1780688999",
                "alert_type": 5,
                "change_values": ["2,563 股"],
                "emotion": 1,
                "name": "唯亚威系统服务",
            },
            "US",
        )

        self.assertEqual(event["event_id"], "266169017")
        self.assertEqual(event["ticker"], "VIAV")
        self.assertEqual(event["symbol"], "VIAV.US")
        self.assertEqual(event["direction"], "bullish")
        self.assertIn("VIAV", format_market_mover_alert(event))

    async def test_persist_new_market_mover_events_dedupes_by_event_id(self):
        event = normalize_market_mover_event(
            {
                "id": "event-1",
                "counter_id": "ST/US/AVGO",
                "alert_name": "大笔卖出",
                "alert_time": "1780688999",
                "alert_type": 6,
                "change_values": ["1,100 股"],
                "emotion": 2,
                "name": "博通",
            },
            "US",
        )

        first = await persist_new_market_mover_events([event])
        second = await persist_new_market_mover_events([event])
        recent = await get_recent_market_movers()

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["ticker"], "AVGO")

    async def test_run_market_mover_alert_cycle_pushes_new_events(self):
        event = normalize_market_mover_event(
            {
                "id": "event-2",
                "counter_id": "ST/US/NVDA",
                "alert_name": "股价急涨",
                "alert_time": "1780688999",
                "alert_type": 11,
                "change_values": ["5.0%"],
                "emotion": 1,
                "name": "英伟达",
            },
            "US",
        )
        adapter = DummyAdminAdapter()

        with patch("server.alerts.market_movers.check_market_movers", return_value=[event]):
            pushed = await run_market_mover_alert_cycle(adapter)
            pushed_again = await run_market_mover_alert_cycle(adapter)

        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed_again, [])
        self.assertEqual(len(adapter.messages), 1)
        self.assertIn("NVDA", adapter.messages[0])


if __name__ == "__main__":
    unittest.main()
