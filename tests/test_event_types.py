import unittest
from datetime import datetime

from server.events.types import (
    AlertCandidate,
    EventItem,
    event_item_id,
    normalize_tickers,
)


class EventTypesTest(unittest.TestCase):
    def test_event_item_id_is_stable(self):
        self.assertEqual(event_item_id("twitter", 42), "twitter:42")
        self.assertEqual(event_item_id("market_mover", "abc"), "market_mover:abc")

    def test_event_item_serializes_with_id_and_stable_key(self):
        item = EventItem(
            source_type="market_mover",
            source_id=17,
            title="NVDA 异动",
            summary="盘前成交放大",
            tickers=("NVDA",),
            priority="warning",
            sentiment="bullish",
            occurred_at=datetime(2026, 6, 9, 9, 30),
            event_key="longbridge:NVDA:spike",
        )

        payload = item.to_dict()

        self.assertEqual(payload["id"], "market_mover:17")
        self.assertEqual(payload["stable_key"], "longbridge:NVDA:spike")
        self.assertEqual(payload["occurred_at"], "2026-06-09T09:30:00")
        self.assertEqual(payload["tickers"], ("NVDA",))

    def test_event_item_uses_id_as_default_stable_key(self):
        item = EventItem(source_type="regulatory", source_id=3, title="8-K filed")

        self.assertEqual(item.stable_key, "regulatory:3")

    def test_alert_candidate_serializes(self):
        candidate = AlertCandidate(
            event_key="stock-watch:NVDA:chat-1",
            event_type="stock_watch",
            title="NVDA 股票观察提醒",
            summary="较上次检查上涨 5.2%",
            severity="warning",
            target_chats=("chat-1",),
            payload={"ticker": "NVDA"},
        )

        payload = candidate.to_dict()

        self.assertEqual(payload["dedupe_policy"], "once_per_chat")
        self.assertEqual(payload["target_chats"], ("chat-1",))
        self.assertEqual(payload["payload"]["ticker"], "NVDA")

    def test_normalize_tickers_dedupes_and_strips_cash_tags(self):
        self.assertEqual(normalize_tickers(["nvda", "$NVDA", " mrvl ", ""]), ("NVDA", "MRVL"))


if __name__ == "__main__":
    unittest.main()
