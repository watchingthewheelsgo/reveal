import unittest

from server.capabilities.planner import capability_for_command, plan_from_command_route
from server.capabilities.registry import (
    agent_allowed_tools,
    agent_mcp_tool_names,
    format_agent_tool_catalog,
    format_capability_catalog,
    format_command_help,
    list_capabilities,
    list_external_services,
)


class CapabilityRegistryTest(unittest.TestCase):
    def test_agent_tools_are_registered_capabilities(self):
        tools = set(agent_mcp_tool_names())

        self.assertIn("mcp__reveal__stock_quote", tools)
        self.assertIn("mcp__reveal__technical_analysis", tools)
        self.assertIn("mcp__reveal__stock_news", tools)
        self.assertIn("mcp__reveal__portfolio", tools)
        self.assertIn("mcp__reveal__research_history", tools)
        self.assertIn("mcp__reveal__stock_score", tools)
        self.assertIn("mcp__reveal__stock_watch_add", tools)
        self.assertIn("mcp__reveal__stock_watch_remove", tools)
        self.assertIn("mcp__reveal__system_status", tools)
        self.assertIn("mcp__reveal__capability_catalog", tools)
        self.assertIn("mcp__reveal__twitter_watch_list", tools)
        self.assertIn("mcp__reveal__twitter_latest", tools)
        self.assertIn("mcp__reveal__twitter_search", tools)
        self.assertIn("mcp__reveal__trading_journal", tools)
        self.assertIn("mcp__reveal__pnl_summary", tools)
        self.assertLessEqual(tools, set(agent_allowed_tools()))

    def test_registered_commands_are_visible_in_help(self):
        help_text = format_command_help()

        for command in (
            "/tools",
            "/quote",
            "/technical",
            "/news",
            "/portfolio",
            "/history",
            "/stock",
            "/research",
            "/x",
        ):
            self.assertIn(command, help_text)

    def test_capability_catalog_explains_entrypoint_layers(self):
        catalog = format_capability_catalog()
        capability_ids = {cap.id for cap in list_capabilities()}

        self.assertIn("核心实现函数", catalog)
        self.assertIn("自然语言", catalog)
        self.assertIn("Agent MCP", catalog)
        self.assertIn("External services", catalog)
        self.assertIn("stock.quote", capability_ids)
        self.assertIn("stock.watch", capability_ids)
        self.assertIn("research.ticker", capability_ids)

    def test_agent_catalog_exposes_capabilities_and_service_backing(self):
        catalog = format_agent_tool_catalog()

        self.assertIn("Reveal system capabilities", catalog)
        self.assertIn("mcp__reveal__twitter_watch_list", catalog)
        self.assertIn("mcp__reveal__stock_watch_add", catalog)
        self.assertIn("mcp__reveal__system_status", catalog)
        self.assertIn("social.x_graphql", catalog)
        self.assertIn("WebSearch", catalog)

    def test_external_services_are_registered(self):
        services = {service.id for service in list_external_services()}

        self.assertIn("bot.feishu", services)
        self.assertIn("llm.deepseek_agent", services)
        self.assertIn("market.finnhub", services)
        self.assertIn("social.vxtwitter", services)


class CapabilityPlannerTest(unittest.TestCase):
    def test_command_route_compiles_to_planned_action(self):
        plan = plan_from_command_route(
            {"command": "quote", "args": ["NVDA"]},
            "NVDA 现在多少钱",
        )

        self.assertEqual(plan.capability_id, "stock.quote")
        self.assertEqual(plan.command, "quote")
        self.assertEqual(plan.args, ["NVDA"])
        self.assertFalse(plan.needs_confirmation)

    def test_command_lookup_uses_capability_registry(self):
        cap = capability_for_command("research")

        self.assertIsNotNone(cap)
        assert cap is not None
        self.assertEqual(cap.id, "research.ticker")


if __name__ == "__main__":
    unittest.main()
