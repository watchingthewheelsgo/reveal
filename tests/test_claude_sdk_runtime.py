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
        self.original_claude_agent_auth_token = (
            settings_module.global_settings.claude_agent_auth_token
        )
        self.original_claude_agent_effort = settings_module.global_settings.claude_agent_effort
        settings_module.global_settings.openai_api_key = "deepseek-key"
        settings_module.global_settings.claude_agent_auth_token = ""
        settings_module.global_settings.claude_agent_effort = "max"

    async def asyncTearDown(self):
        settings_module.global_settings.openai_api_key = self.original_openai_api_key
        settings_module.global_settings.claude_agent_auth_token = (
            self.original_claude_agent_auth_token
        )
        settings_module.global_settings.claude_agent_effort = self.original_claude_agent_effort

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
        self.assertEqual(options.allowed_tools, ["WebSearch", "WebFetch"])
        self.assertEqual(
            options.disallowed_tools, ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
        )
        self.assertTrue(options.strict_mcp_config)
        self.assertEqual(options.mcp_servers, {})
        self.assertEqual(options.setting_sources, [])
        self.assertEqual(options.extra_args, {"bare": None})
        self.assertEqual(options.effort, "max")
        self.assertEqual(options.resume, "old-session")
        self.assertEqual(options.env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(options.env["ANTHROPIC_AUTH_TOKEN"], "deepseek-key")

    async def test_run_agent_requires_token(self):
        settings_module.global_settings.openai_api_key = ""
        settings_module.global_settings.claude_agent_auth_token = ""

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
