import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.settings import get_settings
from server.alerts.market_movers import (
    format_market_mover_alert,
    get_recent_market_movers,
    market_mover_event_from_payload,
    normalize_market_mover_event,
    persist_new_market_mover_events,
    run_market_mover_alert_cycle,
)
from server.db import engine as db_engine
from server.events.types import MarketMoverSignalEvent


def _top_mover_raw(
    code: str,
    *,
    change: str = "0.0523",
    post_id: str = "post-1",
    timestamp: str = "1780688999",
    title: str = "关联新闻标题",
) -> dict:
    return {
        "alert_reason": "波动超 20 日均值",
        "alert_type": 11,
        "post": {
            "id": post_id,
            "title": title,
            "description_html": f"<p>{title}</p>",
            "published_at": timestamp,
            "web_url": f"https://longbridge.cn/news/{post_id}",
            "post_source": {"name": "Longbridge News"},
        },
        "stock": {
            "change": change,
            "code": code,
            "full_name": f"{code} Inc.",
            "last_done": "123.450",
            "market": "US",
            "name": code,
            "symbol": f"{code}.US",
        },
        "timestamp": timestamp,
    }


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
            _top_mover_raw("VIAV", title="VIAV 发布重大进展"),
            "US",
        )

        self.assertEqual(event["source"], "longbridge_top_mover")
        self.assertEqual(
            event["event_id"],
            "longbridge:top-mover:VIAV.US:11:post-1:0.0523",
        )
        self.assertEqual(event["ticker"], "VIAV")
        self.assertEqual(event["symbol"], "VIAV.US")
        self.assertEqual(event["direction"], "bullish")
        self.assertEqual(event["change_text"], "+5.23%")
        self.assertEqual(event["price"], 123.45)
        self.assertEqual(event["detail"], "VIAV 发布重大进展")
        self.assertEqual(event["news_source"], "Longbridge News")
        self.assertIn("VIAV", format_market_mover_alert(event))

        typed = market_mover_event_from_payload(event)
        self.assertIsInstance(typed, MarketMoverSignalEvent)
        self.assertEqual(typed.kind, "market_mover")
        self.assertEqual(typed.source, "longbridge_top_mover")
        self.assertEqual(typed.ticker, "VIAV")
        self.assertEqual(typed.tickers, ["VIAV"])

    async def test_persist_new_market_mover_events_dedupes_by_event_id(self):
        event = normalize_market_mover_event(
            _top_mover_raw("AVGO", change="-0.0312", post_id="event-1", title="AVGO 下跌"),
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
            _top_mover_raw("NVDA", change="0.061", post_id="event-2", title="NVDA 异动新闻"),
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
