import unittest
from unittest.mock import patch

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from config import settings as settings_module
from server.research.claude_sdk_runtime import (
    AgentConfigurationError,
    AgentRuntimeError,
    _looks_like_pseudo_tool_call_answer,
    run_agent,
)


class ClaudeSdkRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_openai_api_key = settings_module.global_settings.openai_api_key
        self.original_agent_effort = settings_module.global_settings.agent_effort
        self.original_agent_max_turns = settings_module.global_settings.agent_max_turns
        self.original_anthropic_base_url = settings_module.global_settings.anthropic_base_url
        self.original_anthropic_auth_token = settings_module.global_settings.anthropic_auth_token
        self.original_anthropic_model = settings_module.global_settings.anthropic_model
        self.original_anthropic_default_haiku_model = (
            settings_module.global_settings.anthropic_default_haiku_model
        )
        settings_module.global_settings.openai_api_key = "deepseek-key"
        settings_module.global_settings.agent_effort = "max"
        settings_module.global_settings.agent_max_turns = 20
        settings_module.global_settings.anthropic_base_url = "https://api.deepseek.com/anthropic"
        settings_module.global_settings.anthropic_auth_token = ""
        settings_module.global_settings.anthropic_model = "deepseek-v4-pro[1m]"
        settings_module.global_settings.anthropic_default_haiku_model = "deepseek-v4-flash"

    async def asyncTearDown(self):
        settings_module.global_settings.openai_api_key = self.original_openai_api_key
        settings_module.global_settings.agent_effort = self.original_agent_effort
        settings_module.global_settings.agent_max_turns = self.original_agent_max_turns
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
        self.assertEqual(options.extra_args, {})
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
        self.assertEqual(options.max_turns, 20)
        self.assertEqual(options.env["ANTHROPIC_BASE_URL"], "https://example.com/anthropic")
        self.assertEqual(options.env["ANTHROPIC_AUTH_TOKEN"], "native-token")
        self.assertEqual(options.env["ANTHROPIC_MODEL"], "native-model")
        self.assertEqual(options.env["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "native-haiku")

    async def test_run_agent_records_plan_trace(self):
        async def fake_query(prompt, options):
            yield AssistantMessage(
                content=[
                    ToolUseBlock(
                        id="tool-1",
                        name="mcp__reveal__stock_quote",
                        input={"ticker": "NVDA"},
                    )
                ],
                model="test-model",
            )
            yield AssistantMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="tool-1",
                        content='{"ticker":"NVDA","price":100}',
                    )
                ],
                model="test-model",
            )
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
            result = await run_agent("research NVDA")

        self.assertEqual(result.plan.status, "complete")
        self.assertEqual(result.plan.final_answer, "agent answer")
        self.assertEqual(result.plan.steps[0].tool_name, "mcp__reveal__stock_quote")
        self.assertEqual(result.plan.steps[0].input, {"ticker": "NVDA"})
        self.assertIn("NVDA", result.plan.steps[0].observation or "")

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

    async def test_run_agent_returns_partial_answer_when_max_turns_reached(self):
        async def fake_query(prompt, options):
            yield SystemMessage(subtype="init", data={"session_id": "init-session"})
            yield AssistantMessage(
                content=[TextBlock(text="已找到 SEC 文件和价格异动线索。")],
                model="test-model",
            )
            yield AssistantMessage(
                content=[TextBlock(text="当前证据指向盘前消息驱动。")],
                model="test-model",
            )
            yield ResultMessage(
                subtype="error",
                duration_ms=1,
                duration_api_ms=1,
                is_error=True,
                num_turns=20,
                session_id="result-session",
                result="Claude Code returned an error result: Reached maximum number of turns (20)",
            )

        with patch("server.research.claude_sdk_runtime.query", new=fake_query):
            result = await run_agent("research this")

        self.assertEqual(result.agent_session_id, "result-session")
        self.assertIn("阶段性总结", result.answer)
        self.assertIn("已找到 SEC 文件和价格异动线索", result.answer)
        self.assertIn("当前证据指向盘前消息驱动", result.answer)

    async def test_run_agent_summarizes_tool_observations_when_max_turns_reached(self):
        async def fake_query(prompt, options):
            yield AssistantMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="tool-1",
                        content="NVDA 盘前上涨 6.2%，SEC 8-K 显示新增重大客户合同。",
                    )
                ],
                model="test-model",
            )
            yield ResultMessage(
                subtype="error",
                duration_ms=1,
                duration_api_ms=1,
                is_error=True,
                num_turns=20,
                session_id="result-session",
                result="Reached maximum number of turns (20)",
            )

        with patch("server.research.claude_sdk_runtime.query", new=fake_query):
            result = await run_agent("research this")

        self.assertIn("阶段性总结", result.answer)
        self.assertIn("已获取的信息片段", result.answer)
        self.assertIn("NVDA 盘前上涨 6.2%", result.answer)

    async def test_run_agent_returns_partial_answer_when_max_turns_exception_is_raised(self):
        async def fake_query(prompt, options):
            yield SystemMessage(subtype="init", data={"session_id": "init-session"})
            yield AssistantMessage(
                content=[TextBlock(text="已完成 NVDA 新闻和报价检查。")],
                model="test-model",
            )
            raise Exception(
                "Claude Code returned an error result: Reached maximum number of turns (8)"
            )

        with patch("server.research.claude_sdk_runtime.query", new=fake_query):
            result = await run_agent("research this")

        self.assertEqual(result.agent_session_id, "init-session")
        self.assertIn("阶段性总结", result.answer)
        self.assertIn("已完成 NVDA 新闻和报价检查", result.answer)

    async def test_run_agent_rejects_bracket_pseudo_reveal_tool_text(self):
        calls = 0

        async def fake_query(prompt, options):
            nonlocal calls
            calls += 1
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="result-session",
                result=(
                    "xbot让我帮你查看 Twitter 关注列表。\n[调用 mcp__reveal__twitter_watch_list] {}"
                ),
            )

        with patch("server.research.claude_sdk_runtime.query", new=fake_query):
            with self.assertRaises(AgentRuntimeError) as ctx:
                await run_agent("我的关注列表里有谁")

        self.assertEqual(calls, 2)
        self.assertIn("没有真正执行工具调用", ctx.exception.user_message)


class PseudoToolCallDetectionTest(unittest.TestCase):
    def test_detects_xml_function_call_text(self):
        answer = """
好的，让我先加关注 @aleabitoreddit，然后拉取最近推文。
<function_calls>
<invoke name="mcp__reveal__twitter_watch_add">
<parameter name="username">aleabitoreddit</parameter>
</invoke>
</function_calls>
"""

        self.assertTrue(_looks_like_pseudo_tool_call_answer(answer))

    def test_detects_chinese_bracket_tool_call_text(self):
        answer = "xbot让我帮你查看 Twitter 关注列表。\n[调用 mcp__reveal__twitter_watch_list] {}"

        self.assertTrue(_looks_like_pseudo_tool_call_answer(answer))


if __name__ == "__main__":
    unittest.main()
