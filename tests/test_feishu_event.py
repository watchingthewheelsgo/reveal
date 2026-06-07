import asyncio
import hashlib
import json
import unittest
from types import SimpleNamespace
from typing import Any, cast

from server.bot.base import CommandRouter
from server.bot.feishu import FeishuBot, _should_send_as_markdown_card


class DummyFeishuResponse:
    code = 0
    msg = "ok"
    data = SimpleNamespace(message_id="msg-1")

    def success(self) -> bool:
        return True


class DummyFeishuMessageApi:
    def __init__(self):
        self.created = []
        self.replied = []

    def create(self, request):
        self.created.append(request)
        return DummyFeishuResponse()

    def reply(self, request):
        self.replied.append(request)
        return DummyFeishuResponse()


class FeishuEventTest(unittest.TestCase):
    def test_url_verification_returns_challenge(self):
        bot = FeishuBot()

        response = asyncio.run(
            bot.handle_event({"type": "url_verification", "challenge": "challenge-token"})
        )

        self.assertEqual(response, {"challenge": "challenge-token"})

    def test_message_event_dispatches_to_router(self):
        bot = FeishuBot()
        bot.admin_chat_ids = ["chat-1"]
        router = CommandRouter(bot)
        bot.set_router(router)
        called: list[list[str]] = []

        async def handler(ctx):
            called.append(ctx.args)

        router.register("status", handler)
        body = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "user-1"}},
                "message": {
                    "chat_id": "chat-1",
                    "content": '{"text": "/status verbose"}',
                },
            },
        }

        response = asyncio.run(bot.handle_event(body))

        self.assertEqual(response, {"status": "ok"})
        self.assertEqual(called, [["verbose"]])

    def test_signature_verification_matches_feishu_formula(self):
        bot = FeishuBot()
        bot.verification_token = "token"
        timestamp = "1710000000"
        nonce = "nonce"
        encrypt = ""
        signature = hashlib.sha256(f"{timestamp}{nonce}{encrypt}token".encode()).hexdigest()

        self.assertTrue(bot.verify_signature(timestamp, nonce, encrypt, signature))
        self.assertFalse(bot.verify_signature(timestamp, nonce, encrypt, "bad"))

    def test_thread_text_replies_use_interactive_markdown_cards(self):
        bot = FeishuBot()
        message_api = DummyFeishuMessageApi()
        bot.client = cast(
            Any, SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=message_api)))
        )

        message_id = bot._reply_in_thread_sync("root-msg", "**结论**\n- 继续观察 NVDA")

        self.assertEqual(message_id, "msg-1")
        request = message_api.replied[0]
        self.assertEqual(request.request_body.msg_type, "interactive")
        self.assertTrue(request.request_body.reply_in_thread)
        payload = json.loads(request.request_body.content)
        self.assertEqual(payload["elements"][0]["text"]["tag"], "lark_md")
        self.assertIn("**结论**", payload["elements"][0]["text"]["content"])

    def test_markdown_detection_keeps_simple_status_as_text(self):
        self.assertFalse(_should_send_as_markdown_card("正在检查告警"))
        self.assertTrue(_should_send_as_markdown_card("**结论**: 继续观察"))
        self.assertTrue(_should_send_as_markdown_card("第一行\n第二行"))


if __name__ == "__main__":
    unittest.main()
