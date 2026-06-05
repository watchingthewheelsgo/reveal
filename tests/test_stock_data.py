import unittest
from unittest.mock import patch

from config import settings as settings_module
from server.stock import data as stock_data


class StockDataProviderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_finnhub_api_key = settings_module.global_settings.finnhub_api_key
        settings_module.global_settings.finnhub_api_key = "finnhub-key"

    async def asyncTearDown(self):
        settings_module.global_settings.finnhub_api_key = self.original_finnhub_api_key

    async def test_fetch_stock_data_uses_finnhub_when_configured(self):
        closes = [float(i) for i in range(1, 31)]
        volumes = [1000.0 for _ in closes]

        class FakeResponse:
            def __init__(self, payload):
                self.status_code = 200
                self._payload = payload

            def json(self):
                return self._payload

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, params):
                if url.endswith("/stock/candle"):
                    return FakeResponse({"s": "ok", "c": closes, "v": volumes})
                if url.endswith("/stock/profile2"):
                    return FakeResponse(
                        {
                            "name": "SiveCo",
                            "finnhubIndustry": "Technology",
                            "marketCapitalization": 10,
                        }
                    )
                if url.endswith("/stock/metric"):
                    return FakeResponse(
                        {
                            "metric": {
                                "52WeekHigh": 40,
                                "52WeekLow": 5,
                                "beta": 1.2,
                                "revenueGrowthTTMYoy": 20,
                            }
                        }
                    )
                raise AssertionError(url)

        with (
            patch("httpx.AsyncClient", return_value=FakeClient()),
            patch(
                "server.stock.data.fetch_quote_finnhub",
                return_value={"price": 30, "change_pct": 1.5, "prev_close": 29},
            ),
            patch("server.stock.data._fetch_stock_data_sync") as yfinance_fetch,
        ):
            result = await stock_data.fetch_stock_data("SIVE")

        yfinance_fetch.assert_not_called()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["source"], "finnhub")
        self.assertEqual(result["current_price"], 30)
        self.assertEqual(result["revenue_growth"], 0.2)
        self.assertIsNotNone(result["sma_20"])
