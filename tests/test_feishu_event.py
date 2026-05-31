import asyncio
import hashlib
import unittest

from server.bot.base import CommandRouter
from server.bot.feishu import FeishuBot


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


if __name__ == "__main__":
    unittest.main()
