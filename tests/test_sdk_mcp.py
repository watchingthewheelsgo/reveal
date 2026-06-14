import json
import unittest
from typing import Any, cast
from unittest.mock import patch

from server.mcp import market_skill_catalog
from server.research.sdk_mcp import _build_tools


class SdkMcpAdapterTest(unittest.TestCase):
    def test_build_tools_exposes_reveal_capabilities(self):
        tools = {item.name: item for item in _build_tools()}

        self.assertIn("twitter_watch_list", tools)
        self.assertIn("twitter_watch_add", tools)
        self.assertIn("twitter_search", tools)
        self.assertIn("stock_quote", tools)
        self.assertIn("portfolio_holding_add", tools)
        self.assertIn("portfolio_holding_remove", tools)
        self.assertIn("stock_watch_add", tools)
        self.assertIn("market_skill_catalog", tools)
        self.assertIn("scheduled_task_create", tools)
        self.assertIn("scheduled_task_list", tools)
        self.assertIn("scheduled_task_cancel", tools)
        self.assertIn("market_movers_check", tools)
        self.assertIn("market_movers_recent", tools)

    def test_build_tools_preserves_required_and_optional_parameters(self):
        tools = {item.name: item for item in _build_tools()}
        stock_quote_schema = cast(dict[str, Any], tools["stock_quote"].input_schema)
        watch_add_schema = cast(dict[str, Any], tools["twitter_watch_add"].input_schema)
        twitter_search_schema = cast(dict[str, Any], tools["twitter_search"].input_schema)
        stock_watch_add_schema = cast(dict[str, Any], tools["stock_watch_add"].input_schema)
        holding_add_schema = cast(dict[str, Any], tools["portfolio_holding_add"].input_schema)
        market_movers_check_schema = cast(dict[str, Any], tools["market_movers_check"].input_schema)

        self.assertEqual(stock_quote_schema["required"], ["ticker"])
        self.assertEqual(watch_add_schema["required"], ["username"])
        self.assertEqual(
            watch_add_schema["properties"]["backfill_limit"]["default"],
            10,
        )
        self.assertEqual(twitter_search_schema["required"], ["query"])
        self.assertNotIn("username", twitter_search_schema.get("required", []))
        self.assertEqual(stock_watch_add_schema["required"], ["ticker", "chat_id"])
        self.assertEqual(stock_watch_add_schema["properties"]["threshold_pct"]["default"], 5.0)
        self.assertEqual(holding_add_schema["required"], ["ticker"])
        self.assertEqual(holding_add_schema["properties"]["note"]["default"], "")
        self.assertEqual(market_movers_check_schema["properties"]["count"]["default"], 50)


class MarketSkillCatalogMcpTest(unittest.IsolatedAsyncioTestCase):
    async def test_market_skill_catalog_does_not_require_database(self):
        with patch("server.mcp._ensure_database", side_effect=RuntimeError("db unavailable")):
            payload = json.loads(await market_skill_catalog())

        self.assertIn("skills", payload)
        skill_ids = {skill["id"] for skill in payload["skills"]}
        self.assertIn("macro_policy", skill_ids)


if __name__ == "__main__":
    unittest.main()
