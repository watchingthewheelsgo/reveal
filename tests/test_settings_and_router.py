import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from config.settings import Settings
from server.bot.base import BotAdapter, BotContext, CommandRouter
from server.commands import cmd_research, handle_plain_message, register_all_commands


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

    def test_twitter_auth_tokens_accept_comma_separated_values(self):
        with patch.dict(
            os.environ,
            {"TWITTER_AUTH_TOKENS": " token-a,token-b,token-a "},
            clear=False,
        ):
            settings = self.build_settings()

        self.assertEqual(settings.twitter_auth_tokens, ["token-a", "token-b"])

    def test_regulatory_list_settings_accept_comma_separated_values(self):
        with patch.dict(
            os.environ,
            {
                "SEC_ALERT_FORMS": "8-K, 10-Q, 8-K",
                "SEC_ALERT_CRITICAL_FORMS": "S-1,F-1",
                "FDA_ALERT_CATEGORIES": "drug,device",
                "FDA_ALERT_WARNING_CLASSIFICATIONS": "Class II,Class III",
                "FDA_ALERT_KEYWORDS": "Pfizer, Moderna",
            },
            clear=False,
        ):
            settings = self.build_settings()

        self.assertEqual(settings.sec_alert_forms, ["8-K", "10-Q"])
        self.assertEqual(settings.sec_alert_critical_forms, ["S-1", "F-1"])
        self.assertEqual(settings.fda_alert_categories, ["drug", "device"])
        self.assertEqual(settings.fda_alert_warning_classifications, ["Class II", "Class III"])
        self.assertEqual(settings.fda_alert_keywords, ["Pfizer", "Moderna"])

    def test_alert_policy_thresholds_are_configurable(self):
        with patch.dict(
            os.environ,
            {
                "ALERT_PRICE_CRITICAL_PCT": "7.5",
                "ALERT_VOLUME_CRITICAL_RATIO": "6",
                "STOCK_WATCH_CRITICAL_MULTIPLIER": "3",
            },
            clear=False,
        ):
            settings = self.build_settings()

        self.assertEqual(settings.alert_price_critical_pct, 7.5)
        self.assertEqual(settings.alert_volume_critical_ratio, 6)
        self.assertEqual(settings.stock_watch_critical_multiplier, 3)

    def test_longbridge_oauth_settings_configure_market_movers(self):
        with patch.dict(
            os.environ,
            {
                "LONGBRIDGE_ENABLED": "true",
                "LONGBRIDGE_OAUTH_TOKEN_PATH": "/app/secrets/longbridge/reveal-oauth.json",
                "LONGBRIDGE_MOVERS_MARKET": "us",
                "LONGBRIDGE_MOVERS_INTERVAL_SECONDS": "300",
            },
            clear=False,
        ):
            settings = self.build_settings()

        self.assertTrue(settings.is_longbridge_configured())
        self.assertEqual(settings.longbridge_movers_market, "US")
        self.assertEqual(settings.longbridge_movers_interval_seconds, 300)

    def test_invalid_schedule_time_fails_fast(self):
        with patch.dict(os.environ, {"DAILY_BRIEFING_TIME": "8am"}, clear=False):
            with self.assertRaises(ValidationError):
                self.build_settings()

    def test_legacy_us_timezone_alias_is_normalized(self):
        with patch.dict(os.environ, {"SCHEDULER_TIMEZONE": "US/Eastern"}, clear=False):
            settings = self.build_settings()

        self.assertEqual(settings.scheduler_timezone, "America/New_York")

    def test_agent_runtime_defaults_to_claude_sdk(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = self.build_settings()

        self.assertEqual(settings.agent_runtime, "claude_sdk")
        self.assertEqual(settings.agent_effort, "max")
        self.assertEqual(settings.agent_max_turns, 20)
        self.assertEqual(settings.get_agent_base_url(), "https://api.deepseek.com/anthropic")
        self.assertEqual(settings.get_agent_model(), "deepseek-v4-pro[1m]")

    def test_anthropic_agent_env_configures_runtime(self):
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_BASE_URL": "https://example.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": "native-token",
                "ANTHROPIC_MODEL": "native-model",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "native-opus",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "native-sonnet",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "native-haiku",
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

    def test_deepseek_key_configures_lightweight_llm(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_BASE_URL": "https://example.com/v1",
                "DEEPSEEK_MODEL": "deepseek-chat-test",
                "ANTHROPIC_AUTH_TOKEN": "anthropic-key",
                "OPENAI_API_KEY": "legacy-key",
            },
            clear=False,
        ):
            settings = self.build_settings()

        self.assertEqual(settings.get_llm_auth_token(), "deepseek-key")
        self.assertEqual(settings.get_llm_base_url(), "https://example.com/v1")
        self.assertEqual(settings.get_llm_model(), "deepseek-chat-test")

    def test_anthropic_token_beats_legacy_openai_key_for_lightweight_llm(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "",
                "ANTHROPIC_AUTH_TOKEN": "anthropic-key",
                "OPENAI_API_KEY": "legacy-key",
            },
            clear=False,
        ):
            settings = self.build_settings()

        self.assertEqual(settings.get_llm_auth_token(), "anthropic-key")

    def test_legacy_openai_compatible_base_url_still_works(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_BASE_URL": "",
                "DEEPSEEK_MODEL": "",
                "OPENAI_BASE_URL": "https://legacy.example.com/v1",
                "OPENAI_MODEL": "legacy-model",
            },
            clear=False,
        ):
            settings = self.build_settings()

        self.assertEqual(settings.get_llm_base_url(), "https://legacy.example.com/v1")
        self.assertEqual(settings.get_llm_model(), "legacy-model")

    def test_unknown_agent_runtime_is_rejected(self):
        with patch.dict(os.environ, {"AGENT_RUNTIME": "custom"}, clear=False):
            with self.assertRaises(ValidationError):
                self.build_settings()

    def test_agent_max_turns_must_be_positive(self):
        with patch.dict(os.environ, {"AGENT_MAX_TURNS": "0"}, clear=False):
            with self.assertRaises(ValidationError):
                self.build_settings()

    def test_agent_effort_must_be_known_value(self):
        with patch.dict(os.environ, {"AGENT_EFFORT": "extreme"}, clear=False):
            with self.assertRaises(ValidationError):
                self.build_settings()


class DummyAdapter(BotAdapter):
    def __init__(self, authorized: bool):
        self.authorized = authorized
        self.messages: list[tuple[str, str]] = []
        self.registered_commands: list[str] = []

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))

    async def send_card(self, chat_id: str, card: dict) -> None:
        self.messages.append((chat_id, str(card)))

    def register_command(self, command: str, handler) -> None:
        self.registered_commands.append(command)

    async def push_to_admin(self, text: str) -> None:
        self.messages.append(("admin", text))

    def is_authorized(self, ctx: BotContext) -> bool:
        return self.authorized


class CommandRouterTest(unittest.TestCase):
    def test_register_all_commands_uses_slim_public_command_set(self):
        adapter = DummyAdapter(authorized=True)
        router = CommandRouter(adapter)

        register_all_commands(router, adapter)

        self.assertEqual(
            set(adapter.registered_commands),
            {
                "help",
                "status",
                "stock",
                "portfolio",
                "research",
                "topic",
                "x",
                "task",
                "alert",
                "movers",
            },
        )

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


class AgentFirstRoutingTest(unittest.TestCase):
    def test_plain_quote_request_routes_to_agent(self):
        adapter = DummyAdapter(authorized=True)
        ctx = BotContext(chat_id="chat-1", user_id="user-1", text="NVDA 现在多少钱")
        spawned: dict[str, str] = {}

        def fake_spawn(coro, label: str) -> None:
            spawned["label"] = label
            coro.close()

        with patch("server.commands._spawn_background_task", new=fake_spawn):
            asyncio.run(handle_plain_message(ctx, adapter))

        self.assertEqual(spawned, {"label": "agent message"})
        self.assertEqual(adapter.messages, [])

    def test_plain_portfolio_request_routes_to_agent(self):
        adapter = DummyAdapter(authorized=True)
        ctx = BotContext(chat_id="chat-1", user_id="user-1", text="我的持仓")
        spawned: dict[str, str] = {}

        def fake_spawn(coro, label: str) -> None:
            spawned["label"] = label
            coro.close()

        with patch("server.commands._spawn_background_task", new=fake_spawn):
            asyncio.run(handle_plain_message(ctx, adapter))

        self.assertEqual(spawned, {"label": "agent message"})
        self.assertEqual(adapter.messages, [])

    def test_plain_natural_language_routes_to_agent(self):
        adapter = DummyAdapter(authorized=True)
        ctx = BotContext(chat_id="chat-1", user_id="user-1", text="加上 @aleabitoreddit")
        spawned: dict[str, str] = {}

        def fake_spawn(coro, label: str) -> None:
            spawned["label"] = label
            coro.close()

        with patch("server.commands._spawn_background_task", new=fake_spawn):
            asyncio.run(handle_plain_message(ctx, adapter))

        self.assertEqual(spawned, {"label": "agent message"})

    def test_top_level_plain_message_anchors_agent_reply_to_source_message(self):
        adapter = DummyAdapter(authorized=True)
        ctx = BotContext(
            chat_id="chat-1",
            user_id="user-1",
            text="分析 NVDA",
            message_id="user-msg",
        )
        captured: dict[str, str] = {}

        async def noop():
            return None

        def fake_job(chat_id, text, adapter_arg, reply_to="", source_message_id=""):
            captured["chat_id"] = chat_id
            captured["text"] = text
            captured["reply_to"] = reply_to
            captured["source_message_id"] = source_message_id
            return noop()

        def fake_spawn(coro, label: str) -> None:
            captured["label"] = label
            coro.close()

        with (
            patch("server.commands._run_agent_message_job", new=fake_job),
            patch("server.commands._spawn_background_task", new=fake_spawn),
        ):
            asyncio.run(handle_plain_message(ctx, adapter))

        self.assertEqual(
            captured,
            {
                "chat_id": "chat-1",
                "text": "分析 NVDA",
                "reply_to": "user-msg",
                "source_message_id": "user-msg",
                "label": "agent message",
            },
        )

    def test_top_level_plain_message_starts_new_agent_even_with_active_topic(self):
        adapter = DummyAdapter(authorized=True)
        ctx = BotContext(chat_id="chat-1", user_id="user-1", text="继续分析这个")
        spawned: dict[str, str] = {}

        def fake_spawn(coro, label: str) -> None:
            spawned["label"] = label
            coro.close()

        with (
            patch(
                "server.research.service.get_active_topic",
                new=AsyncMock(return_value=SimpleNamespace(id=1)),
            ),
            patch("server.commands._spawn_background_task", new=fake_spawn),
        ):
            asyncio.run(handle_plain_message(ctx, adapter))

        self.assertEqual(spawned, {"label": "agent message"})

    def test_reply_to_bound_research_session_routes_to_same_session(self):
        adapter = DummyAdapter(authorized=True)
        ctx = BotContext(
            chat_id="chat-1",
            user_id="user-1",
            text="继续分析这个",
            reply_to_message_id="msg-root",
        )
        spawned: dict[str, str] = {}

        def fake_spawn(coro, label: str) -> None:
            spawned["label"] = label
            coro.close()

        with (
            patch(
                "server.bot.bindings.resolve_message_binding",
                new=AsyncMock(
                    return_value=SimpleNamespace(source_type="research_session", source_id=77)
                ),
            ),
            patch("server.commands._spawn_background_task", new=fake_spawn),
        ):
            asyncio.run(handle_plain_message(ctx, adapter))

        self.assertEqual(spawned, {"label": "bound agent session message"})


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
        self.assertIn("在这条消息下面回复", adapter.messages[0][1])
        self.assertIn("/topic summary", adapter.messages[0][1])
        self.assertNotIn("/deep", adapter.messages[0][1])


if __name__ == "__main__":
    unittest.main()
