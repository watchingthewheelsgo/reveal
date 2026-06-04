import unittest

from server.capabilities.planner import capability_for_command, plan_from_command_route
from server.capabilities.registry import (
    agent_allowed_tools,
    agent_mcp_tool_names,
    format_capability_catalog,
    format_command_help,
    list_capabilities,
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
            "/research",
            "/twatch",
        ):
            self.assertIn(command, help_text)

    def test_capability_catalog_explains_entrypoint_layers(self):
        catalog = format_capability_catalog()
        capability_ids = {cap.id for cap in list_capabilities()}

        self.assertIn("核心实现函数", catalog)
        self.assertIn("自然语言", catalog)
        self.assertIn("Agent tool", catalog)
        self.assertIn("stock.quote", capability_ids)
        self.assertIn("research.ticker", capability_ids)


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
