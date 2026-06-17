import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from server.briefing import generate_daily_briefing


class DailyBriefingTest(unittest.IsolatedAsyncioTestCase):
    async def test_daily_briefing_is_concise_action_summary(self):
        with (
            patch("server.briefing._today", return_value=date(2026, 6, 17)),
            patch(
                "server.briefing._market_context_lines",
                new=AsyncMock(return_value=["- SPY $550.00 (+0.30%)", "- VIX 18.0，低波动"]),
            ),
            patch("server.briefing._open_trades", new=AsyncMock(return_value=[])),
            patch(
                "server.briefing._position_lines",
                new=AsyncMock(return_value=["- NVDA x10: $140.00, 浮盈亏 $+120.00"]),
            ),
            patch(
                "server.briefing._recent_signal_lines",
                new=AsyncMock(return_value=["- @source [NVDA]: 新的 AI 基建订单"]),
            ),
            patch(
                "server.briefing._yesterday_pnl_line",
                new=AsyncMock(return_value="昨日无平仓交易。"),
            ),
            patch(
                "server.briefing._recent_research_lines",
                new=AsyncMock(return_value=["- 1天前研究 [NVDA]: AI 订单影响"]),
            ),
            patch(
                "server.briefing._watched_ticker_line",
                new=AsyncMock(return_value="- 关注标的: NVDA, TSLA。"),
            ),
        ):
            text = await generate_daily_briefing()

        self.assertIn("*Reveal · 今日简报 — 2026-06-17*", text)
        self.assertIn("*今日重点*", text)
        self.assertIn("*持仓 / 关注*", text)
        self.assertIn("*最新市场信号*", text)
        self.assertIn("*复盘*", text)
        self.assertNotIn("追踪中的标的", text)
        self.assertNotIn("财报日历接入需 Alpha Vantage", text)
        self.assertLess(len(text), 900)


if __name__ == "__main__":
    unittest.main()
