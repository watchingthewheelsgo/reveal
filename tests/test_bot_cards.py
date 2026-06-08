import unittest

from server.bot.cards import (
    EventCardData,
    StockAlertCardData,
    event_alert_card,
    report_card,
    research_result_card,
    research_status_card,
    stock_watch_alert_card,
)


class BotCardsTest(unittest.TestCase):
    def test_research_status_card_uses_state_title(self):
        card = research_status_card("正在研究 NVDA", state="running")

        self.assertEqual(card["title"], "Reveal · 研究中")
        self.assertEqual(card["header"]["template"], "blue")
        self.assertIn("正在研究 NVDA", str(card["elements"]))

    def test_research_result_card_keeps_metadata_in_note(self):
        card = research_result_card("## 结论\n\n继续观察。", step_count=3, elapsed_seconds=12.5)

        self.assertEqual(card["title"], "Reveal · 研究结果")
        self.assertEqual(card["header"]["template"], "green")
        self.assertEqual(card["elements"][0]["tag"], "note")
        self.assertIn("3 个工具步骤", str(card["elements"][0]))
        self.assertIn("继续回复本话题即可追问", str(card["elements"][0]))
        self.assertIn("## 结论", str(card["elements"]))

    def test_event_alert_card_has_event_metadata(self):
        card = event_alert_card(
            EventCardData(
                title="SEC 8-K",
                summary="公司提交重大事项公告。",
                source="sec",
                event_id="sec:abc",
                priority="critical",
                sentiment="bearish",
                url="https://example.com/filing",
            )
        )

        rendered = str(card["elements"])

        self.assertEqual(card["title"], "Reveal · 市场事件")
        self.assertEqual(card["header"]["template"], "red")
        self.assertIn("SEC 8-K", rendered)
        self.assertIn("事件: sec:abc", rendered)
        self.assertIn("打开原文", rendered)

    def test_stock_watch_alert_card_formats_prices(self):
        card = stock_watch_alert_card(
            StockAlertCardData(
                ticker="nvda",
                message="NVDA 较上次检查上涨 5.2%",
                previous_price=100.0,
                current_price=105.2,
                change_pct=5.2,
                threshold_pct=5.0,
            )
        )

        rendered = str(card["elements"])

        self.assertEqual(card["title"], "Reveal · 股票异动")
        self.assertIn("NVDA 股票观察提醒", rendered)
        self.assertIn("$100.00 -> $105.20", rendered)
        self.assertIn("变化: +5.2%", rendered)

    def test_report_card_can_include_footer(self):
        card = report_card("Reveal · 每日简报", "今日重点。", footer="自动生成")

        self.assertEqual(card["title"], "Reveal · 每日简报")
        self.assertEqual(card["elements"][-1]["tag"], "note")
        self.assertIn("今日重点。", str(card["elements"]))


if __name__ == "__main__":
    unittest.main()
