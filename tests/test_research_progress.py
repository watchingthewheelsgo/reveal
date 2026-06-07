import unittest
from typing import cast

from server.bot.base import BotAdapter
from server.research.progress import ResearchProgressReporter, _result_card


class FakeProgressAdapter:
    supports_message_edit = True

    def __init__(self):
        self.thread_cards = []
        self.messages = []
        self.sent_cards = []
        self.edits = []

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))

    async def send_card(self, chat_id: str, card: dict) -> None:
        self.sent_cards.append((chat_id, card))

    async def send_card_returning_id(self, chat_id: str, card: dict) -> str | None:
        self.sent_cards.append((chat_id, card))
        return "status-card"

    async def reply_card_in_thread(self, chat_id: str, message_id: str, card: dict) -> str | None:
        self.thread_cards.append((chat_id, message_id, card))
        return "result-card"

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        self.edits.append((chat_id, message_id, text))


class ResearchProgressReporterTest(unittest.IsolatedAsyncioTestCase):
    async def test_finish_replies_with_result_card(self):
        adapter = FakeProgressAdapter()
        reporter = ResearchProgressReporter(cast(BotAdapter, adapter), "chat-1", "root-msg")

        await reporter.start("Agent 处理中...")
        result_id = await reporter.finish("这是研究结论")

        self.assertEqual(result_id, "result-card")
        self.assertEqual(adapter.thread_cards[-1][1], "root-msg")
        result_card = adapter.thread_cards[-1][2]
        self.assertEqual(result_card["header"]["template"], "green")
        self.assertEqual(result_card["header"]["title"]["content"], "Reveal · 研究结果")
        self.assertEqual(result_card["elements"][0]["tag"], "note")
        self.assertIn("继续回复本话题即可追问", str(result_card["elements"][0]))
        self.assertIn("这是研究结论", str(result_card["elements"]))
        self.assertFalse(adapter.messages)

    async def test_result_card_structures_markdown_body(self):
        card = _result_card(
            "## 结论\n\n- NVDA 继续观察\n- 留意盘前成交量\n\n普通段落说明。",
            step_count=3,
            elapsed_seconds=12.3,
        )

        body = card["elements"]
        rendered = str(body)

        self.assertIn("**结论**", rendered)
        self.assertNotIn("## 结论", rendered)
        self.assertIn("• NVDA 继续观察", rendered)
        self.assertIn("• 留意盘前成交量", rendered)
        self.assertIn("普通段落说明。", rendered)

    async def test_result_card_renders_markdown_table(self):
        card = _result_card(
            "| Ticker | Move | Reason |\n"
            "| --- | --- | --- |\n"
            "| NVDA | +5.2% | 盘前成交放大 |\n"
            "| TSLA | -3.1% | 指引下调 |",
            step_count=2,
            elapsed_seconds=8.0,
        )

        rendered = str(card["elements"])

        self.assertIn("fields", rendered)
        self.assertIn("Ticker", rendered)
        self.assertIn("NVDA", rendered)
        self.assertIn("盘前成交放大", rendered)
        self.assertNotIn("| --- | --- | --- |", rendered)

    async def test_result_card_does_not_replace_long_body_with_truncation_notice(self):
        long_body = "\n\n".join(
            f"## 第 {index} 段\n\n这是第 {index} 段内容。" for index in range(30)
        )

        card = _result_card(long_body, step_count=9, elapsed_seconds=42.0)
        rendered = str(card["elements"])

        self.assertIn("**第 29 段**", rendered)
        self.assertIn("这是第 29 段内容。", rendered)
        self.assertNotIn("内容较长，已截断", rendered)


if __name__ == "__main__":
    unittest.main()
