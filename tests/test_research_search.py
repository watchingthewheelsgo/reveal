import unittest
from unittest.mock import AsyncMock, Mock, patch

from server.research.search import SearXNGSearchProvider


class ResearchSearchTest(unittest.IsolatedAsyncioTestCase):
    async def test_searxng_provider_parses_json_results(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {
                    "title": "Result A",
                    "url": "https://example.com/a",
                    "content": "Snippet A",
                },
                {
                    "title": "Result B",
                    "url": "https://example.com/b",
                    "content": "Snippet B",
                },
            ]
        }
        client = AsyncMock()
        client.get.return_value = response

        with patch("httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__.return_value = client
            results = await SearXNGSearchProvider("https://searx.local").search("ai", 1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].query, "ai")
        self.assertEqual(results[0].title, "Result A")
        self.assertEqual(results[0].url, "https://example.com/a")
        self.assertEqual(results[0].snippet, "Snippet A")
        client.get.assert_awaited_once()
        _, kwargs = client.get.call_args
        self.assertEqual(kwargs["params"]["format"], "json")


if __name__ == "__main__":
    unittest.main()
