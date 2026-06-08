"""Longbridge market mover alerts for unusual stock activity."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import desc, select

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import MarketMoverEvent
from server.stock.longbridge import (
    fetch_quote_anomalies,
    longbridge_ticker_from_symbol,
    normalize_longbridge_symbol,
)


async def run_market_mover_alert_cycle(adapter=None) -> list[dict[str, Any]]:
    """Fetch Longbridge anomalies, dedupe them, and push newly seen events."""
    settings = get_settings()
    if not settings.is_longbridge_configured() or not settings.longbridge_movers_enabled:
        logger.debug("Longbridge movers disabled or not configured")
        return []

    events = await check_market_movers(
        market=settings.longbridge_movers_market,
        count=settings.longbridge_movers_count,
    )
    new_events = await persist_new_market_mover_events(events, mark_pushed=bool(adapter))
    pushed = new_events[: settings.longbridge_movers_push_limit]
    if adapter:
        for event in pushed:
            from server.delivery.service import send_alert_to_admin
            from server.events.types import AlertCandidate
            from server.interactions.threading import get_or_create_thread_for_source
            from server.stock.watchlist import platform_for_adapter

            platform = platform_for_adapter(adapter)
            source_id = event.get("db_id")

            async def thread_for_chat(chat_id: str) -> int | None:
                if not isinstance(source_id, int):
                    return None
                thread = await get_or_create_thread_for_source(
                    chat_id=chat_id,
                    platform=platform,
                    source_type="market_mover",
                    source_id=source_id,
                    source_key=str(event.get("event_id") or ""),
                )
                return thread.id

            await send_alert_to_admin(
                adapter,
                AlertCandidate(
                    event_key=f"market_mover:{event.get('event_id')}",
                    event_type="market_mover",
                    source_id=source_id,
                    title=f"{event.get('ticker')} {event.get('event_type')}",
                    summary=str(event.get("change_text") or event.get("detail") or ""),
                    severity="warning",
                    payload=event,
                ),
                text=format_market_mover_alert(event),
                platform=platform,
                thread_factory=thread_for_chat,
                reason="longbridge market mover",
            )

    if len(new_events) > len(pushed) and adapter:
        await adapter.push_to_admin(
            f"📈 Longbridge 异动还有 {len(new_events) - len(pushed)} 条新事件未展开，"
            "可用 /movers recent 查看。"
        )

    logger.info(
        "Longbridge mover cycle complete: fetched={} new={} pushed={}",
        len(events),
        len(new_events),
        len(pushed),
    )
    return pushed


async def check_market_movers(
    market: str | None = None,
    count: int | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Return normalized Longbridge anomaly events without writing push state."""
    settings = get_settings()
    selected_market = (market or settings.longbridge_movers_market).upper()
    selected_count = count or settings.longbridge_movers_count
    data = await fetch_quote_anomalies(selected_market, selected_count, symbol=symbol)
    changes = data.get("changes") or []
    if not isinstance(changes, list):
        return []
    return [
        normalize_market_mover_event(item, selected_market)
        for item in changes
        if isinstance(item, dict)
    ]


async def persist_new_market_mover_events(
    events: list[dict[str, Any]],
    mark_pushed: bool = False,
) -> list[dict[str, Any]]:
    """Persist only unseen market mover events and return the inserted payloads."""
    session_factory = get_session_factory()
    new_events: list[dict[str, Any]] = []
    now = _utcnow()
    async with session_factory() as session:
        for event in events:
            event_id = event["event_id"]
            result = await session.execute(
                select(MarketMoverEvent.id).where(MarketMoverEvent.event_id == event_id)
            )
            if result.scalar_one_or_none():
                continue
            row = MarketMoverEvent(
                source=event["source"],
                event_id=event_id,
                market=event["market"],
                symbol=event["symbol"],
                ticker=event["ticker"],
                event_type=event["event_type"],
                direction=event.get("direction"),
                price=event.get("price"),
                change_text=event.get("change_text"),
                detail=event.get("detail"),
                event_time=event.get("event_time"),
                raw_json=event.get("raw"),
                pushed_at=now if mark_pushed else None,
            )
            session.add(row)
            await session.flush()
            event["db_id"] = row.id
            new_events.append(event)
        await session.commit()
    return new_events


async def get_recent_market_movers(limit: int = 10) -> list[dict[str, Any]]:
    """Return recently persisted market mover events."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(MarketMoverEvent)
            .order_by(desc(MarketMoverEvent.event_time), desc(MarketMoverEvent.first_seen_at))
            .limit(limit)
        )
        rows = result.scalars().all()
    return [market_mover_payload(row) for row in rows]


async def get_market_mover_status_payload() -> dict[str, Any]:
    settings = get_settings()
    return {
        "enabled": settings.longbridge_enabled,
        "configured": settings.is_longbridge_configured(),
        "api_base": settings.longbridge_api_base,
        "token_path_configured": bool(settings.longbridge_oauth_token_path),
        "movers_enabled": settings.longbridge_movers_enabled,
        "market": settings.longbridge_movers_market,
        "interval_seconds": settings.longbridge_movers_interval_seconds,
        "count": settings.longbridge_movers_count,
        "push_limit": settings.longbridge_movers_push_limit,
    }


def normalize_market_mover_event(raw: dict[str, Any], market: str) -> dict[str, Any]:
    counter_id = str(raw.get("counter_id") or "")
    symbol = normalize_longbridge_symbol(counter_id, market)
    ticker = longbridge_ticker_from_symbol(symbol)
    alert_time = _parse_longbridge_ts(raw.get("alert_time"))
    alert_name = str(raw.get("alert_name") or "异动")
    alert_type = str(raw.get("alert_type") or "")
    source_id = str(raw.get("id") or "")
    event_id = source_id or (
        f"longbridge:anomaly:{symbol}:{alert_type}:{raw.get('alert_time') or ''}:"
        f"{','.join(str(v) for v in raw.get('change_values') or [])}"
    )
    values = [str(item) for item in raw.get("change_values") or [] if str(item).strip()]
    direction = _emotion_to_direction(raw.get("emotion"), alert_name)
    return {
        "source": "longbridge_anomaly",
        "event_id": event_id,
        "market": market.upper(),
        "symbol": symbol,
        "ticker": ticker,
        "event_type": alert_name,
        "direction": direction,
        "price": None,
        "change_text": ", ".join(values) if values else None,
        "detail": str(raw.get("name") or ticker),
        "event_time": alert_time,
        "raw": raw,
    }


def market_mover_payload(row: MarketMoverEvent) -> dict[str, Any]:
    return {
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
        "event_time": row.event_time.isoformat() if row.event_time else None,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "pushed_at": row.pushed_at.isoformat() if row.pushed_at else None,
    }


def format_market_mover_alert(event: dict[str, Any]) -> str:
    direction = event.get("direction")
    emoji = "🟢" if direction == "bullish" else "🔴" if direction == "bearish" else "📈"
    lines = [
        f"{emoji} *Longbridge 异动 — {event['ticker']}*",
        "",
        f"类型: {event['event_type']}",
    ]
    if event.get("detail"):
        lines.append(f"标的: {event['detail']} ({event['symbol']})")
    if event.get("change_text"):
        lines.append(f"变化: {event['change_text']}")
    if event.get("event_time"):
        event_time = event["event_time"]
        if isinstance(event_time, datetime):
            lines.append(f"时间: {event_time.isoformat()}")
        else:
            lines.append(f"时间: {event_time}")
    lines.append("来源: Longbridge anomaly")
    return "\n".join(lines)


def format_market_mover_list(events: list[dict[str, Any]]) -> str:
    if not events:
        return "暂无 Longbridge 异动记录。"
    lines = ["*Longbridge 最近异动*", ""]
    for event in events:
        values = f" | {event['change_text']}" if event.get("change_text") else ""
        lines.append(f"`{event['ticker']}` {event['event_type']}{values}")
    return "\n".join(lines)


def _emotion_to_direction(emotion: Any, alert_name: str) -> str | None:
    if str(emotion) == "1":
        return "bullish"
    if str(emotion) == "2":
        return "bearish"
    if "买" in alert_name:
        return "bullish"
    if "卖" in alert_name or "跌" in alert_name:
        return "bearish"
    return None


def _parse_longbridge_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(int(value), UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
