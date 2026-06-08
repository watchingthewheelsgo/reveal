"""Unified event feed projection across source-specific records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import desc, or_, select

from server.db.engine import get_session_factory
from server.db.models import (
    AlertDelivery,
    InteractionThread,
    MarketMoverEvent,
    RegulatoryEvent,
    ResearchSession,
    SocialPost,
)
from server.events.types import EventItem, normalize_tickers


async def list_event_items(
    *,
    limit: int = 80,
    source_type: str | None = None,
    ticker: str | None = None,
    q: str | None = None,
) -> list[EventItem]:
    limit = max(1, min(200, limit))
    items: list[EventItem] = []
    session_factory = get_session_factory()
    async with session_factory() as session:
        if source_type in {None, "", "twitter"}:
            items.extend(await _social_events(session, limit=limit, ticker=ticker, q=q))
        if source_type in {None, "", "regulatory"}:
            items.extend(await _regulatory_events(session, limit=limit, ticker=ticker, q=q))
        if source_type in {None, "", "market_mover"}:
            items.extend(await _market_mover_events(session, limit=limit, ticker=ticker, q=q))
        if source_type in {None, "", "stock_watch", "price", "volume", "news"}:
            items.extend(
                await _delivery_events(
                    session,
                    limit=limit,
                    source_type=source_type,
                    ticker=ticker,
                    q=q,
                )
            )

        await _hydrate_event_context(session, items)

    return sorted(
        items,
        key=lambda item: item.occurred_at or item.created_at or datetime.min,
        reverse=True,
    )[:limit]


async def get_event_detail(source_type: str, source_id: int) -> dict[str, Any] | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        if source_type == "twitter":
            record = await session.get(SocialPost, source_id)
            if record is None:
                return None
            item = _social_event_item(record)
            await _hydrate_event_context(session, [item])
            return {"event": item.to_dict(), "record": _social_record(record)}
        if source_type == "regulatory":
            record = await session.get(RegulatoryEvent, source_id)
            if record is None:
                return None
            item = _regulatory_event_item(record)
            await _hydrate_event_context(session, [item])
            return {"event": item.to_dict(), "record": _regulatory_record(record)}
        if source_type == "market_mover":
            record = await session.get(MarketMoverEvent, source_id)
            if record is None:
                return None
            item = _market_mover_event_item(record)
            await _hydrate_event_context(session, [item])
            return {"event": item.to_dict(), "record": _market_mover_record(record)}
        if source_type in {"stock_watch", "price", "volume", "news"}:
            record = await session.get(AlertDelivery, source_id)
            if record is None or record.event_type != source_type:
                return None
            item = _delivery_event_item(record)
            await _hydrate_event_context(session, [item])
            return {"event": item.to_dict(), "record": _delivery_record(record)}
    return None


async def get_event_detail_for_thread(
    source_type: str,
    source_id: int | None,
    thread_id: int | None,
) -> dict[str, Any] | None:
    if source_type in {"stock_watch", "price", "volume", "news"} and thread_id is not None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(AlertDelivery)
                .where(
                    AlertDelivery.thread_id == thread_id,
                    AlertDelivery.event_type == source_type,
                )
                .order_by(desc(AlertDelivery.updated_at), desc(AlertDelivery.id))
                .limit(1)
            )
            delivery = result.scalar_one_or_none()
            if delivery is None:
                return None
            item = _delivery_event_item(delivery)
            await _hydrate_event_context(session, [item])
            return {"event": item.to_dict(), "record": _delivery_record(delivery)}
    if source_id is None:
        return None
    return await get_event_detail(source_type, source_id)


async def _delivery_events(
    session,
    *,
    limit: int,
    source_type: str | None,
    ticker: str | None,
    q: str | None,
) -> list[EventItem]:
    statement = select(AlertDelivery)
    delivery_types = {"stock_watch", "price", "volume", "news"}
    if source_type in delivery_types:
        statement = statement.where(AlertDelivery.event_type == source_type)
    else:
        statement = statement.where(AlertDelivery.event_type.in_(delivery_types))
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(AlertDelivery.event_key.ilike(pattern), AlertDelivery.reason.ilike(pattern))
        )
    result = await session.execute(
        statement.order_by(desc(AlertDelivery.created_at), desc(AlertDelivery.id)).limit(limit)
    )
    rows = result.scalars().all()
    items = [_delivery_event_item(row) for row in rows]
    if ticker:
        normalized = ticker.upper().lstrip("$")
        items = [item for item in items if normalized in item.tickers]
    return items


async def _social_events(
    session,
    *,
    limit: int,
    ticker: str | None,
    q: str | None,
) -> list[EventItem]:
    statement = select(SocialPost)
    if ticker:
        normalized = ticker.upper().lstrip("$")
        statement = statement.where(SocialPost.mentioned_tickers.contains([normalized]))
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(SocialPost.content.ilike(pattern), SocialPost.summary.ilike(pattern))
        )
    result = await session.execute(
        statement.order_by(desc(SocialPost.posted_at), desc(SocialPost.id)).limit(limit)
    )
    return [_social_event_item(row) for row in result.scalars().all()]


async def _regulatory_events(
    session, *, limit: int, ticker: str | None, q: str | None
) -> list[EventItem]:
    statement = select(RegulatoryEvent)
    if ticker:
        statement = statement.where(RegulatoryEvent.ticker == ticker.upper().lstrip("$"))
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(RegulatoryEvent.title.ilike(pattern), RegulatoryEvent.detail.ilike(pattern))
        )
    result = await session.execute(
        statement.order_by(desc(RegulatoryEvent.event_date), desc(RegulatoryEvent.id)).limit(limit)
    )
    return [_regulatory_event_item(row) for row in result.scalars().all()]


async def _market_mover_events(
    session, *, limit: int, ticker: str | None, q: str | None
) -> list[EventItem]:
    statement = select(MarketMoverEvent)
    if ticker:
        statement = statement.where(MarketMoverEvent.ticker == ticker.upper().lstrip("$"))
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(MarketMoverEvent.event_type.ilike(pattern), MarketMoverEvent.detail.ilike(pattern))
        )
    result = await session.execute(
        statement.order_by(desc(MarketMoverEvent.event_time), desc(MarketMoverEvent.id)).limit(
            limit
        )
    )
    return [_market_mover_event_item(row) for row in result.scalars().all()]


async def _hydrate_event_context(session, items: list[EventItem]) -> None:
    for index, item in enumerate(items):
        thread = await _latest_thread(session, item.source_type, _int_or_none(item.source_id))
        if thread is None and item.thread_id is not None:
            thread = await _thread_by_id(session, item.thread_id)
        research = await _latest_research(session, item.source_type, _int_or_none(item.source_id))
        delivery = await _latest_delivery(session, item.stable_key)
        items[index] = EventItem(
            source_type=item.source_type,
            source_id=item.source_id,
            title=item.title,
            summary=item.summary,
            body=item.body,
            tickers=item.tickers,
            priority=item.priority,
            sentiment=item.sentiment,
            occurred_at=item.occurred_at,
            created_at=item.created_at,
            url=item.url,
            raw_ref=item.raw_ref,
            has_research=research is not None or bool(thread and thread.research_session_id),
            thread_id=thread.id if thread else item.thread_id,
            delivery_status=delivery.status if delivery else "none",
            event_key=item.event_key,
            metadata=item.metadata,
        )


async def _latest_thread(
    session,
    source_type: str,
    source_id: int | None,
) -> InteractionThread | None:
    if source_id is None:
        return None
    result = await session.execute(
        select(InteractionThread)
        .where(
            InteractionThread.source_type == source_type,
            InteractionThread.source_id == source_id,
        )
        .order_by(desc(InteractionThread.last_activity_at), desc(InteractionThread.id))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _thread_by_id(session, thread_id: int) -> InteractionThread | None:
    return await session.get(InteractionThread, thread_id)


async def _latest_research(
    session,
    source_type: str,
    source_id: int | None,
) -> ResearchSession | None:
    if source_id is None:
        return None
    result = await session.execute(
        select(ResearchSession)
        .where(ResearchSession.source_type == source_type, ResearchSession.source_id == source_id)
        .order_by(desc(ResearchSession.updated_at), desc(ResearchSession.id))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _latest_delivery(session, event_key: str) -> AlertDelivery | None:
    result = await session.execute(
        select(AlertDelivery)
        .where(AlertDelivery.event_key == event_key)
        .order_by(desc(AlertDelivery.updated_at), desc(AlertDelivery.id))
        .limit(1)
    )
    return result.scalar_one_or_none()


def _social_event_item(row: SocialPost) -> EventItem:
    return EventItem(
        source_type="twitter",
        source_id=row.id,
        event_key=f"twitter:{row.id}",
        title=f"Twitter Update · @{row.username}",
        summary=row.summary or row.attention_reason or _compact(row.content, 240),
        body=row.content,
        tickers=normalize_tickers(row.mentioned_tickers),
        priority=_priority(row.urgency),
        sentiment=_sentiment(row.sentiment),
        occurred_at=row.posted_at,
        created_at=row.created_at,
        url=row.tweet_url,
        raw_ref=f"social_posts:{row.id}",
        metadata={
            "username": row.username,
            "tweet_id": row.tweet_id,
            "is_noteworthy": row.is_noteworthy,
            "topics": row.topics or [],
        },
    )


def _regulatory_event_item(row: RegulatoryEvent) -> EventItem:
    return EventItem(
        source_type="regulatory",
        source_id=row.id,
        event_key=f"regulatory:{row.event_id}",
        title=row.title,
        summary=row.detail or row.title,
        body=row.detail or "",
        tickers=normalize_tickers([row.ticker] if row.ticker else []),
        priority=_priority(row.severity),
        sentiment="unknown",
        occurred_at=row.event_date,
        created_at=row.first_seen_at,
        url=row.url,
        raw_ref=f"regulatory_events:{row.id}",
        metadata={"source": row.source, "event_id": row.event_id, "event_type": row.event_type},
    )


def _market_mover_event_item(row: MarketMoverEvent) -> EventItem:
    return EventItem(
        source_type="market_mover",
        source_id=row.id,
        event_key=f"market_mover:{row.event_id}",
        title=f"{row.ticker} {row.event_type}",
        summary=row.change_text or row.detail or row.event_type,
        body=row.detail or "",
        tickers=normalize_tickers([row.ticker]),
        priority="warning" if row.direction else "info",
        sentiment=_direction_sentiment(row.direction),
        occurred_at=row.event_time,
        created_at=row.first_seen_at,
        raw_ref=f"market_mover_events:{row.id}",
        metadata={
            "source": row.source,
            "event_id": row.event_id,
            "market": row.market,
            "symbol": row.symbol,
            "direction": row.direction,
            "price": row.price,
        },
    )


def _delivery_event_item(row: AlertDelivery) -> EventItem:
    payload = row.payload or {}
    ticker = str(payload.get("ticker") or row.event_source_id or "").upper()
    title = str(payload.get("title") or payload.get("type") or row.event_type)
    if ticker and ticker not in title:
        title = f"{title} — {ticker}"
    summary = str(payload.get("message") or row.reason or row.event_key)
    return EventItem(
        source_type=row.event_type,
        source_id=row.id,
        event_key=row.event_key,
        title=title,
        summary=summary,
        body=str(payload.get("detail") or ""),
        tickers=normalize_tickers([ticker] if ticker else []),
        priority=_priority(row.severity),
        sentiment="unknown",
        occurred_at=row.sent_at or row.created_at,
        created_at=row.created_at,
        raw_ref=f"alert_deliveries:{row.id}",
        thread_id=row.thread_id,
        delivery_status=row.status,
        metadata={
            "platform": row.platform,
            "chat_id": row.chat_id,
            "message_id": row.message_id,
            "event_source_id": row.event_source_id,
        },
    )


def _delivery_record(row: AlertDelivery) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "event_source_id": row.event_source_id,
        "event_key": row.event_key,
        "thread_id": row.thread_id,
        "platform": row.platform,
        "chat_id": row.chat_id,
        "message_id": row.message_id,
        "status": row.status,
        "reason": row.reason,
        "severity": row.severity,
        "payload": row.payload or {},
        "error": row.error,
        "created_at": _dt(row.created_at),
        "sent_at": _dt(row.sent_at),
        "updated_at": _dt(row.updated_at),
    }


def _social_record(row: SocialPost) -> dict[str, Any]:
    return {
        "id": row.id,
        "username": row.username,
        "tweet_id": row.tweet_id,
        "tweet_url": row.tweet_url,
        "content": row.content,
        "translated_content": row.translated_content,
        "summary": row.summary,
        "media": row.media or [],
        "links": row.links or [],
        "referenced_tweets": row.referenced_tweets or [],
        "raw_json": row.raw_json or {},
        "posted_at": _dt(row.posted_at),
        "created_at": _dt(row.created_at),
    }


def _regulatory_record(row: RegulatoryEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source,
        "event_id": row.event_id,
        "ticker": row.ticker,
        "event_type": row.event_type,
        "severity": row.severity,
        "title": row.title,
        "detail": row.detail,
        "url": row.url,
        "event_date": _dt(row.event_date),
        "first_seen_at": _dt(row.first_seen_at),
        "pushed_at": _dt(row.pushed_at),
        "raw_json": row.raw_json or {},
    }


def _market_mover_record(row: MarketMoverEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source,
        "event_id": row.event_id,
        "market": row.market,
        "symbol": row.symbol,
        "ticker": row.ticker,
        "event_type": row.event_type,
        "direction": row.direction,
        "price": row.price,
        "change_text": row.change_text,
        "detail": row.detail,
        "event_time": _dt(row.event_time),
        "first_seen_at": _dt(row.first_seen_at),
        "pushed_at": _dt(row.pushed_at),
        "raw_json": row.raw_json or {},
    }


def _priority(value: str | None) -> str:
    normalized = (value or "info").strip().lower()
    if normalized in {"critical", "high"}:
        return "critical"
    if normalized in {"warning", "medium"}:
        return "warning"
    if normalized in {"low"}:
        return "low"
    return "info"


def _sentiment(value: str | None) -> str:
    normalized = (value or "unknown").strip().lower()
    if normalized in {"bullish", "bearish", "neutral"}:
        return normalized
    return "unknown"


def _direction_sentiment(value: str | None) -> str:
    if value == "bullish":
        return "bullish"
    if value == "bearish":
        return "bearish"
    return "unknown"


def _int_or_none(value: int | str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compact(text: str | None, limit: int) -> str:
    value = " ".join((text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
