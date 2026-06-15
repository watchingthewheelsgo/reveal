import unittest

from server.research.prompts import agent_allowed_tools, agent_system_prompt


class AgentToolAccessTest(unittest.TestCase):
    def test_agent_always_gets_full_tool_set_for_planning(self):
        tools = agent_allowed_tools()

        self.assertIn("WebSearch", tools)
        self.assertIn("WebFetch", tools)
        self.assertIn("mcp__reveal__stock_quote", tools)
        self.assertIn("mcp__reveal__twitter_watch_add", tools)
        self.assertIn("mcp__reveal__scheduled_task_create", tools)
        self.assertIn("mcp__reveal__portfolio_holding_add", tools)

    def test_prompt_requires_agent_planning_instead_of_keyword_routing(self):
        prompt = agent_system_prompt(agent_allowed_tools())

        self.assertIn("先基于用户意图制定 plan", prompt)
        self.assertIn("不要用关键词猜测任务类型", prompt)
        self.assertIn("不要建议用户改用 /research", prompt)


if __name__ == "__main__":
    unittest.main()
