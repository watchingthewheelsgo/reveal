import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from config import settings as settings_module
from server.alerts import regulatory
from server.events.types import FDARecallEvent, SECFilingEvent


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, routes):
        self.routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params=None):
        for suffix, response in self.routes.items():
            if url.endswith(suffix):
                return response
        raise AssertionError(url)


class RegulatoryAlertsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = settings_module.global_settings
        self.original_values = {
            "sec_user_agent": self.settings.sec_user_agent,
            "sec_alert_forms": list(self.settings.sec_alert_forms),
            "sec_alert_critical_forms": list(self.settings.sec_alert_critical_forms),
            "sec_alert_warning_forms": list(self.settings.sec_alert_warning_forms),
            "fda_alert_enabled": self.settings.fda_alert_enabled,
            "fda_base_url": self.settings.fda_base_url,
            "fda_alert_categories": list(self.settings.fda_alert_categories),
            "fda_alert_classifications": list(self.settings.fda_alert_classifications),
            "fda_alert_critical_classifications": list(
                self.settings.fda_alert_critical_classifications
            ),
            "fda_alert_warning_classifications": list(
                self.settings.fda_alert_warning_classifications
            ),
            "fda_alert_keywords": list(self.settings.fda_alert_keywords),
            "regulatory_alert_lookback_hours": self.settings.regulatory_alert_lookback_hours,
        }
        regulatory._SEC_COMPANY_CACHE = None

    async def asyncTearDown(self):
        for key, value in self.original_values.items():
            setattr(self.settings, key, value)
        regulatory._SEC_COMPANY_CACHE = None

    async def test_sec_filing_events_require_user_agent_and_filter_forms(self):
        now = datetime.now(UTC)
        self.settings.sec_user_agent = "RevealTest/0.1 contact@example.com"
        self.settings.sec_alert_forms = ["8-K"]
        self.settings.regulatory_alert_lookback_hours = 24
        routes = {
            "company_tickers.json": FakeResponse(
                {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "APPLE INC."}}
            ),
            "CIK0000320193.json": FakeResponse(
                {
                    "filings": {
                        "recent": {
                            "form": ["8-K", "10-Q"],
                            "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
                            "filingDate": [now.date().isoformat(), now.date().isoformat()],
                            "reportDate": [now.date().isoformat(), now.date().isoformat()],
                            "acceptanceDateTime": [
                                now.isoformat().replace("+00:00", "Z"),
                                now.isoformat().replace("+00:00", "Z"),
                            ],
                            "primaryDocument": ["aapl-8k.htm", "aapl-10q.htm"],
                            "primaryDocDescription": ["Current report", "Quarterly report"],
                        }
                    }
                }
            ),
        }

        with patch("httpx.AsyncClient", return_value=FakeClient(routes)):
            events = await regulatory.check_sec_filing_events(["AAPL"])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], "sec:0000320193:0000320193-26-000001")
        self.assertEqual(events[0]["severity"], "critical")
        self.assertIn("aapl-8k.htm", events[0]["url"])
        typed = regulatory.regulatory_event_from_payload(events[0])
        self.assertIsInstance(typed, SECFilingEvent)
        assert isinstance(typed, SECFilingEvent)
        self.assertEqual(typed.form, "8-K")
        self.assertEqual(typed.cik, "0000320193")
        self.assertEqual(typed.tickers, ["AAPL"])

    async def test_fda_enforcement_events_match_keywords_and_classification(self):
        self.settings.fda_alert_enabled = True
        self.settings.fda_base_url = "https://api.fda.gov"
        self.settings.fda_alert_categories = ["drug"]
        self.settings.fda_alert_classifications = ["Class I"]
        self.settings.fda_alert_keywords = ["Acme Pharma"]
        self.settings.regulatory_alert_lookback_hours = 48
        today = datetime.now(UTC).strftime("%Y%m%d")
        routes = {
            "drug/enforcement.json": FakeResponse(
                {
                    "results": [
                        {
                            "recall_number": "D-1234-2026",
                            "classification": "Class I",
                            "report_date": today,
                            "recalling_firm": "Acme Pharma LLC",
                            "product_description": "Acme injectable product",
                            "reason_for_recall": "Potential contamination",
                            "status": "Ongoing",
                        },
                        {
                            "recall_number": "D-9999-2026",
                            "classification": "Class III",
                            "report_date": today,
                            "recalling_firm": "Acme Pharma LLC",
                            "product_description": "Low severity",
                        },
                    ]
                }
            )
        }

        with patch("httpx.AsyncClient", return_value=FakeClient(routes)):
            events = await regulatory.check_fda_enforcement_events([])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], "fda:drug:D-1234-2026")
        self.assertEqual(events[0]["severity"], "critical")
        self.assertIn("Acme injectable product", events[0]["detail"])
        typed = regulatory.regulatory_event_from_payload(events[0])
        self.assertIsInstance(typed, FDARecallEvent)
        assert isinstance(typed, FDARecallEvent)
        self.assertEqual(typed.category, "drug")
        self.assertEqual(typed.recall_number, "D-1234-2026")
        self.assertEqual(typed.classification, "Class I")

    async def test_regulatory_severity_uses_configured_policy(self):
        self.settings.sec_alert_critical_forms = ["10-Q"]
        self.settings.sec_alert_warning_forms = ["8-K"]
        self.settings.fda_alert_critical_classifications = ["Class II"]
        self.settings.fda_alert_warning_classifications = ["Class I"]

        self.assertEqual(regulatory._sec_severity("10-Q"), "critical")
        self.assertEqual(regulatory._sec_severity("8-K"), "warning")
        self.assertEqual(regulatory._fda_severity("Class II"), "critical")
        self.assertEqual(regulatory._fda_severity("Class I"), "warning")


if __name__ == "__main__":
    unittest.main()
