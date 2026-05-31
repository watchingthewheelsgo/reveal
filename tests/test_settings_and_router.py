import asyncio
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from config.settings import Settings
from server.bot.base import BotAdapter, BotContext, CommandRouter


class SettingsTest(unittest.TestCase):
    def build_settings(self) -> Settings:
        return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    def test_twitter_accounts_accept_comma_separated_values(self):
        with patch.dict(os.environ, {"TWITTER_ACCOUNTS": "@elonmusk, naval"}, clear=False):
            settings = self.build_settings()

        self.assertEqual(settings.twitter_accounts, ["elonmusk", "naval"])

    def test_twitter_accounts_accept_json_values(self):
        with patch.dict(
            os.environ,
            {"TWITTER_ACCOUNTS": '["@elonmusk", "naval", "naval"]'},
            clear=False,
        ):
            settings = self.build_settings()

        self.assertEqual(settings.twitter_accounts, ["elonmusk", "naval"])

    def test_invalid_schedule_time_fails_fast(self):
        with patch.dict(os.environ, {"DAILY_PICK_TIME": "8am"}, clear=False):
            with self.assertRaises(ValidationError):
                self.build_settings()


class DummyAdapter(BotAdapter):
    def __init__(self, authorized: bool):
        self.authorized = authorized
        self.messages: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))

    async def send_card(self, chat_id: str, card: dict) -> None:
        self.messages.append((chat_id, str(card)))

    def register_command(self, command: str, handler) -> None:
        return None

    async def push_to_admin(self, text: str) -> None:
        self.messages.append(("admin", text))

    def is_authorized(self, ctx: BotContext) -> bool:
        return self.authorized


class CommandRouterTest(unittest.TestCase):
    def test_unauthorized_command_is_blocked(self):
        adapter = DummyAdapter(authorized=False)
        router = CommandRouter(adapter)
        called = False

        async def handler(ctx: BotContext):
            nonlocal called
            called = True

        router.register("status", handler)
        ctx = BotContext(chat_id="chat-1", user_id="user-1", text="/status", command="status")

        asyncio.run(router.handle(ctx))

        self.assertFalse(called)
        self.assertEqual(adapter.messages, [("chat-1", "未授权访问。")])


if __name__ == "__main__":
    unittest.main()
