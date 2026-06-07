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


if __name__ == "__main__":
    unittest.main()
