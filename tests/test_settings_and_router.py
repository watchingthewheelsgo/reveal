import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from config.settings import Settings
from server.bot.base import BotAdapter, BotContext, CommandRouter
from server.commands import cmd_research


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

    def test_agent_runtime_defaults_to_claude_sdk(self):
        settings = self.build_settings()

        self.assertEqual(settings.agent_runtime, "claude_sdk")
        self.assertEqual(settings.claude_agent_base_url, "https://api.deepseek.com/anthropic")
        self.assertEqual(settings.claude_agent_model, "deepseek-v4-pro[1m]")
        self.assertEqual(settings.get_agent_base_url(), "https://api.deepseek.com/anthropic")
        self.assertEqual(settings.get_agent_model(), "deepseek-v4-pro[1m]")

    def test_anthropic_agent_env_overrides_legacy_settings(self):
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_BASE_URL": "https://example.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": "native-token",
                "ANTHROPIC_MODEL": "native-model",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "native-opus",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "native-sonnet",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "native-haiku",
                "CLAUDE_AGENT_BASE_URL": "https://legacy.example.com",
                "CLAUDE_AGENT_AUTH_TOKEN": "legacy-token",
                "CLAUDE_AGENT_MODEL": "legacy-model",
                "CLAUDE_AGENT_SMALL_MODEL": "legacy-small",
            },
            clear=False,
        ):
            settings = self.build_settings()

        self.assertEqual(settings.get_agent_base_url(), "https://example.com/anthropic")
        self.assertEqual(settings.get_agent_auth_token(), "native-token")
        self.assertEqual(settings.get_agent_model(), "native-model")
        self.assertEqual(settings.get_agent_opus_model(), "native-opus")
        self.assertEqual(settings.get_agent_sonnet_model(), "native-sonnet")
        self.assertEqual(settings.get_agent_haiku_model(), "native-haiku")

    def test_unknown_agent_runtime_is_rejected(self):
        with patch.dict(os.environ, {"AGENT_RUNTIME": "custom"}, clear=False):
            with self.assertRaises(ValidationError):
                self.build_settings()

    def test_agent_max_turns_must_be_positive(self):
        with patch.dict(os.environ, {"CLAUDE_AGENT_MAX_TURNS": "0"}, clear=False):
            with self.assertRaises(ValidationError):
                self.build_settings()

    def test_agent_effort_must_be_known_value(self):
        with patch.dict(os.environ, {"CLAUDE_AGENT_EFFORT": "extreme"}, clear=False):
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


class ResearchCommandTest(unittest.TestCase):
    def test_research_command_starts_topic(self):
        adapter = DummyAdapter(authorized=True)
        ctx = BotContext(
            chat_id="chat-1",
            user_id="user-1",
            text="/research 42 AI 基建",
            command="research",
            args=["42", "AI", "基建"],
        )
        topic = SimpleNamespace(id=7, source_id=42)

        with patch(
            "server.research.service.start_topic", new=AsyncMock(return_value=topic)
        ) as mock:
            asyncio.run(cmd_research(ctx, adapter))

        mock.assert_awaited_once_with("chat-1", "42", "AI 基建")
        self.assertEqual(len(adapter.messages), 1)
        self.assertIn("已建立研究话题 #7", adapter.messages[0][1])
        self.assertIn("/deep 42", adapter.messages[0][1])


if __name__ == "__main__":
    unittest.main()
