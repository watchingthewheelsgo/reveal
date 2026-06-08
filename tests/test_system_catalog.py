import unittest

from server.capabilities.registry import list_external_services
from server.system.catalog import (
    data_sources_for_module,
    get_data_source,
    get_system_catalog_payload,
    get_system_module,
    list_data_sources,
    list_system_modules,
    system_modules_for_data_source,
    validate_system_catalog,
)


class SystemCatalogTest(unittest.TestCase):
    def test_catalog_has_no_dangling_references(self):
        self.assertEqual(validate_system_catalog(), [])

    def test_data_sources_cover_registered_external_services(self):
        source_ids = {source.id for source in list_data_sources()}
        service_ids = {service.id for service in list_external_services()}

        self.assertEqual(source_ids, service_ids)

    def test_core_modules_are_registered(self):
        module_ids = {module.id for module in list_system_modules()}

        self.assertIn("twitter_monitor", module_ids)
        self.assertIn("regulatory_alerts", module_ids)
        self.assertIn("longbridge_market_movers", module_ids)
        self.assertIn("stock_watch_price", module_ids)
        self.assertIn("research_agent", module_ids)
        self.assertIn("daily_briefing", module_ids)
        self.assertIn("web_workbench", module_ids)

    def test_lookup_helpers_return_related_modules_and_sources(self):
        source = get_data_source("market.longbridge")
        module = get_system_module("longbridge_market_movers")

        self.assertIsNotNone(source)
        self.assertIsNotNone(module)
        assert source is not None
        assert module is not None
        self.assertIn("market_mover", source.event_types)
        self.assertIn("market.movers", module.capability_ids)
        self.assertIn(module, system_modules_for_data_source("market.longbridge"))
        self.assertIn(source, data_sources_for_module("longbridge_market_movers"))

    def test_payload_is_json_ready(self):
        payload = get_system_catalog_payload()

        self.assertIn("data_sources", payload)
        self.assertIn("system_modules", payload)
        self.assertIsInstance(payload["data_sources"][0], dict)
        self.assertIsInstance(payload["system_modules"][0], dict)


if __name__ == "__main__":
    unittest.main()
