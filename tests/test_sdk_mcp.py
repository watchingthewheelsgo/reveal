import unittest
from typing import Any, cast

from server.research.sdk_mcp import _build_tools


class SdkMcpAdapterTest(unittest.TestCase):
    def test_build_tools_exposes_reveal_capabilities(self):
        tools = {item.name: item for item in _build_tools()}

        self.assertIn("twitter_watch_list", tools)
        self.assertIn("twitter_watch_add", tools)
        self.assertIn("twitter_search", tools)
        self.assertIn("stock_quote", tools)

    def test_build_tools_preserves_required_and_optional_parameters(self):
        tools = {item.name: item for item in _build_tools()}
        stock_quote_schema = cast(dict[str, Any], tools["stock_quote"].input_schema)
        watch_add_schema = cast(dict[str, Any], tools["twitter_watch_add"].input_schema)
        twitter_search_schema = cast(dict[str, Any], tools["twitter_search"].input_schema)

        self.assertEqual(stock_quote_schema["required"], ["ticker"])
        self.assertEqual(watch_add_schema["required"], ["username"])
        self.assertEqual(
            watch_add_schema["properties"]["backfill_limit"]["default"],
            10,
        )
        self.assertEqual(twitter_search_schema["required"], ["query"])
        self.assertNotIn("username", twitter_search_schema.get("required", []))


if __name__ == "__main__":
    unittest.main()
