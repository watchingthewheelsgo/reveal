import tempfile
import unittest
from pathlib import Path

from server.capabilities.market import format_portfolio, get_portfolio_payload
from server.db import engine as db_engine
from server.journal.service import get_trades_for_period
from server.portfolio.markers import (
    add_portfolio_holding_marker,
    get_portfolio_holding_markers,
    remove_portfolio_holding_marker,
)


class PortfolioMarkerTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_holding_marker_appears_in_portfolio_without_numbers(self):
        added = await add_portfolio_holding_marker("nvda", note="后续相关消息提醒我")

        self.assertTrue(added["created"])
        self.assertTrue(added["portfolio_marker"])

        payload = await get_portfolio_payload()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["ticker"], "NVDA")
        self.assertTrue(payload[0]["portfolio_marker"])
        self.assertIsNone(payload[0]["quantity"])
        self.assertIsNone(payload[0]["entry_price"])

        text = format_portfolio(payload)
        self.assertIn("NVDA 持仓关注标记（不代表真实交易，不记录数量/成本）", text)
        self.assertNotIn("qty=0", text)
        self.assertNotIn("entry=$0.00", text)

        trades = await get_trades_for_period("all")
        self.assertEqual(trades, [])

    async def test_remove_holding_marker_does_not_create_trade(self):
        await add_portfolio_holding_marker("nvda")
        removed = await remove_portfolio_holding_marker("NVDA")

        self.assertTrue(removed["removed"])
        payload = await get_portfolio_payload()
        self.assertEqual(payload, [])

        trades = await get_trades_for_period("all")
        self.assertEqual(trades, [])

        markers = await get_portfolio_holding_markers()
        self.assertEqual(markers, [])


if __name__ == "__main__":
    unittest.main()
