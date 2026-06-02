import unittest
from unittest.mock import patch

from claude_agent_sdk import ResultMessage, SystemMessage

from config import settings as settings_module
from server.research.claude_sdk_runtime import (
    AgentConfigurationError,
    AgentRuntimeError,
    run_agent,
)


class ClaudeSdkRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_openai_api_key = settings_module.global_settings.openai_api_key
        self.original_agent_effort = settings_module.global_settings.agent_effort
        self.original_anthropic_base_url = settings_module.global_settings.anthropic_base_url
        self.original_anthropic_auth_token = settings_module.global_settings.anthropic_auth_token
        self.original_anthropic_model = settings_module.global_settings.anthropic_model
        self.original_anthropic_default_haiku_model = (
            settings_module.global_settings.anthropic_default_haiku_model
        )
        settings_module.global_settings.openai_api_key = "deepseek-key"
        settings_module.global_settings.agent_effort = "max"
        settings_module.global_settings.anthropic_base_url = "https://api.deepseek.com/anthropic"
        settings_module.global_settings.anthropic_auth_token = ""
        settings_module.global_settings.anthropic_model = "deepseek-v4-pro[1m]"
        settings_module.global_settings.anthropic_default_haiku_model = "deepseek-v4-flash"

    async def asyncTearDown(self):
        settings_module.global_settings.openai_api_key = self.original_openai_api_key
        settings_module.global_settings.agent_effort = self.original_agent_effort
        settings_module.global_settings.anthropic_base_url = self.original_anthropic_base_url
        settings_module.global_settings.anthropic_auth_token = self.original_anthropic_auth_token
        settings_module.global_settings.anthropic_model = self.original_anthropic_model
        settings_module.global_settings.anthropic_default_haiku_model = (
            self.original_anthropic_default_haiku_model
        )

    async def test_run_agent_configures_claude_code_for_web_only_research(self):
        captured = {}

        async def fake_query(prompt, options):
            captured["prompt"] = prompt
            captured["options"] = options
            yield SystemMessage(subtype="init", data={"session_id": "init-session"})
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="result-session",
                result="agent answer",
            )

        with patch("server.research.claude_sdk_runtime.query", new=fake_query):
            result = await run_agent("research this", resume="old-session")

        options = captured["options"]
        self.assertEqual(result.answer, "agent answer")
        self.assertEqual(result.agent_session_id, "result-session")
        self.assertEqual(captured["prompt"], "research this")
        self.assertEqual(options.tools, ["WebSearch", "WebFetch"])
        self.assertIn("WebSearch", options.allowed_tools)
        self.assertIn("WebFetch", options.allowed_tools)
        self.assertIn("mcp__reveal__stock_quote", options.allowed_tools)
        self.assertIn("mcp__reveal__technical_analysis", options.allowed_tools)
        self.assertEqual(
            options.disallowed_tools, ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
        )
        self.assertTrue(options.strict_mcp_config)
        self.assertIn("reveal", options.mcp_servers)
        self.assertEqual(options.setting_sources, [])
        self.assertEqual(options.extra_args, {"bare": None})
        self.assertEqual(options.effort, "max")
        self.assertEqual(options.resume, "old-session")
        self.assertEqual(options.env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(options.env["ANTHROPIC_AUTH_TOKEN"], "deepseek-key")

    async def test_run_agent_uses_native_anthropic_environment_names(self):
        settings_module.global_settings.openai_api_key = "openai-key"
        settings_module.global_settings.anthropic_base_url = "https://example.com/anthropic"
        settings_module.global_settings.anthropic_auth_token = "native-token"
        settings_module.global_settings.anthropic_model = "native-model"
        settings_module.global_settings.anthropic_default_haiku_model = "native-haiku"
        captured = {}

        async def fake_query(prompt, options):
            captured["options"] = options
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="result-session",
                result="agent answer",
            )

        with patch("server.research.claude_sdk_runtime.query", new=fake_query):
            await run_agent("research this")

        options = captured["options"]
        self.assertEqual(options.model, "native-model")
        self.assertEqual(options.env["ANTHROPIC_BASE_URL"], "https://example.com/anthropic")
        self.assertEqual(options.env["ANTHROPIC_AUTH_TOKEN"], "native-token")
        self.assertEqual(options.env["ANTHROPIC_MODEL"], "native-model")
        self.assertEqual(options.env["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "native-haiku")

    async def test_run_agent_requires_token(self):
        settings_module.global_settings.openai_api_key = ""
        settings_module.global_settings.anthropic_auth_token = ""

        with self.assertRaises(AgentConfigurationError) as ctx:
            await run_agent("research this")

        self.assertIn("未配置", ctx.exception.user_message)

    async def test_run_agent_maps_result_errors_to_user_message(self):
        async def fake_query(prompt, options):
            yield ResultMessage(
                subtype="error",
                duration_ms=1,
                duration_api_ms=1,
                is_error=True,
                num_turns=1,
                session_id="result-session",
                result="authentication failed",
            )

        with patch("server.research.claude_sdk_runtime.query", new=fake_query):
            with self.assertRaises(AgentRuntimeError) as ctx:
                await run_agent("research this")

        self.assertIn("认证失败", ctx.exception.user_message)


if __name__ == "__main__":
    unittest.main()
