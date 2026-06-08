import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from server.db import engine as db_engine
from server.db.engine import get_session_factory
from server.db.models import AlertDelivery, MarketMoverEvent, RegulatoryEvent, SocialPost
from server.events.feed import get_event_detail, list_event_items


class EventFeedTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-feed-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        await db_engine.init_db()

    async def asyncTearDown(self):
        await db_engine.close_db()
        self.tmpdir.cleanup()

    async def test_event_feed_projects_multiple_source_tables(self):
        session_factory = get_session_factory()
        now = datetime.now(UTC).replace(tzinfo=None)
        async with session_factory() as session:
            session.add(
                SocialPost(
                    username="alice",
                    tweet_id="1",
                    content="NVDA supply chain update",
                    summary="NVDA update",
                    posted_at=now,
                    mentioned_tickers=["NVDA"],
                )
            )
            session.add(
                RegulatoryEvent(
                    source="sec",
                    event_id="sec:abc",
                    ticker="MRVL",
                    event_type="SEC Filing",
                    severity="critical",
                    title="MRVL 8-K filed",
                    detail="Material event",
                    event_date=now,
                )
            )
            session.add(
                MarketMoverEvent(
                    source="longbridge_anomaly",
                    event_id="lb:1",
                    market="US",
                    symbol="TSLA.US",
                    ticker="TSLA",
                    event_type="大幅拉升",
                    direction="bullish",
                    change_text="+5%",
                    event_time=now,
                )
            )
            session.add(
                AlertDelivery(
                    event_type="stock_watch",
                    event_key="stock_watch:1:100:106",
                    platform="telegram",
                    chat_id="chat-1",
                    status="sent",
                    severity="warning",
                    payload={"ticker": "AAPL", "message": "AAPL moved"},
                )
            )
            await session.commit()

        items = await list_event_items(limit=20)
        source_types = {item.source_type for item in items}

        self.assertIn("twitter", source_types)
        self.assertIn("regulatory", source_types)
        self.assertIn("market_mover", source_types)
        self.assertIn("stock_watch", source_types)

    async def test_event_detail_returns_source_record(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            event = RegulatoryEvent(
                source="sec",
                event_id="sec:def",
                ticker="NVDA",
                event_type="SEC Filing",
                severity="warning",
                title="NVDA 10-Q filed",
            )
            session.add(event)
            await session.flush()
            event_id = event.id
            await session.commit()

        detail = await get_event_detail("regulatory", event_id)

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["event"]["source_type"], "regulatory")
        self.assertEqual(detail["record"]["event_id"], "sec:def")

    async def test_event_detail_returns_delivery_record(self):
        session_factory = get_session_factory()
        async with session_factory() as session:
            delivery = AlertDelivery(
                event_type="stock_watch",
                event_key="stock_watch:1:100:106",
                platform="telegram",
                chat_id="chat-1",
                status="sent",
                severity="warning",
                payload={"ticker": "AAPL", "message": "AAPL moved"},
            )
            session.add(delivery)
            await session.flush()
            delivery_id = delivery.id
            await session.commit()

        detail = await get_event_detail("stock_watch", delivery_id)

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["event"]["source_type"], "stock_watch")
        self.assertEqual(detail["record"]["event_key"], "stock_watch:1:100:106")


if __name__ == "__main__":
    unittest.main()
