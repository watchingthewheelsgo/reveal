"""Longbridge top-mover alerts for unusual stock activity with correlated news."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import desc, select

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import MarketMoverEvent
from server.events.types import MarketMoverSignalEvent
from server.stock.longbridge import fetch_top_movers, longbridge_ticker_from_symbol


async def run_market_mover_alert_cycle(adapter=None) -> list[dict[str, Any]]:
    """Fetch Longbridge top movers, dedupe them, and push newly seen events."""
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
            await adapter.push_to_admin(format_market_mover_alert(event))

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
) -> list[dict[str, Any]]:
    """Return normalized Longbridge top-mover events without writing push state."""
    settings = get_settings()
    selected_market = (market or settings.longbridge_movers_market).upper()
    selected_count = count or settings.longbridge_movers_count
    data = await fetch_top_movers(selected_market, selected_count)
    events = data.get("events") or []
    if not isinstance(events, list):
        return []
    return [
        normalize_market_mover_event(item, selected_market)
        for item in events
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
    stock = _dict_field(raw, "stock")
    post = _dict_field(raw, "post")

    symbol = str(stock.get("symbol") or "")
    ticker = str(stock.get("code") or longbridge_ticker_from_symbol(symbol))
    if not symbol and ticker:
        symbol = f"{ticker}.{market.upper()}"

    alert_time = _parse_longbridge_ts(raw.get("timestamp") or post.get("published_at"))
    alert_name = str(raw.get("alert_reason") or "异动")
    alert_type = str(raw.get("alert_type") or "")
    change = _parse_float(stock.get("change"))
    price = _parse_float(stock.get("last_done"))
    post_id = str(post.get("id") or "").strip()
    event_id = (
        f"longbridge:top-mover:{symbol}:{alert_type}:"
        f"{post_id or raw.get('timestamp') or ''}:{stock.get('change') or ''}"
    )
    title = _post_title(post)
    direction = _change_to_direction(change)
    return {
        "source": "longbridge_top_mover",
        "event_id": event_id,
        "market": str(stock.get("market") or market).upper(),
        "symbol": symbol,
        "ticker": ticker,
        "event_type": alert_name,
        "direction": direction,
        "price": price,
        "change_text": _format_change_pct(change),
        "detail": title or str(stock.get("name") or stock.get("full_name") or ticker),
        "event_time": alert_time,
        "news_url": _post_url(post),
        "news_source": _post_source(post),
        "raw": raw,
    }


def market_mover_event_from_payload(event: dict[str, Any]) -> MarketMoverSignalEvent:
    """Convert a normalized Longbridge top-mover payload into a typed runtime event."""

    ticker = str(event.get("ticker") or "")
    event_type = str(event.get("event_type") or "异动")
    detail = str(event.get("detail") or ticker)
    direction = event.get("direction")
    event_time = event.get("event_time")
    title = f"{ticker} {event_type}".strip()
    change_text = event.get("change_text")
    summary_parts = [detail]
    if change_text:
        summary_parts.append(str(change_text))
    if direction:
        summary_parts.append(f"direction={direction}")
    return MarketMoverSignalEvent(
        id=str(event.get("event_id") or ""),
        kind="market_mover",
        source="longbridge_top_mover",
        title=title or event_type,
        summary=" | ".join(part for part in summary_parts if part),
        occurred_at=event_time if isinstance(event_time, datetime) else None,
        severity="medium",
        tickers=[ticker] if ticker else [],
        raw=event.get("raw") if isinstance(event.get("raw"), dict) else None,
        market=str(event.get("market") or ""),
        symbol=str(event.get("symbol") or ""),
        ticker=ticker,
        event_type=event_type,
        direction=str(direction) if direction else None,
        price=event.get("price") if isinstance(event.get("price"), int | float) else None,
        change_text=str(change_text) if change_text else None,
        detail=detail,
    )


def market_mover_event_from_record(row: MarketMoverEvent) -> MarketMoverSignalEvent:
    """Convert a persisted Longbridge top-mover row into a typed runtime event."""

    return market_mover_event_from_payload(market_mover_payload(row) | {"raw": row.raw_json})


def market_mover_payload(row: MarketMoverEvent) -> dict[str, Any]:
    raw = row.raw_json if isinstance(row.raw_json, dict) else {}
    post = _dict_field(raw, "post")
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
        "news_url": _post_url(post),
        "news_source": _post_source(post),
    }


def format_market_mover_alert(event: dict[str, Any]) -> str:
    direction = event.get("direction")
    emoji = "🟢" if direction == "bullish" else "🔴" if direction == "bearish" else "📈"
    lines = [
        f"{emoji} *Longbridge 异动 — {event['ticker']}*",
        "",
        f"原因: {event['event_type']}",
    ]
    if event.get("detail"):
        lines.append(f"新闻: {event['detail']}")
    if event.get("change_text"):
        lines.append(f"变化: {event['change_text']}")
    if event.get("price"):
        lines.append(f"价格: {event['price']}")
    if event.get("news_source"):
        lines.append(f"媒体: {event['news_source']}")
    if event.get("news_url"):
        lines.append(f"链接: {event['news_url']}")
    if event.get("event_time"):
        event_time = event["event_time"]
        if isinstance(event_time, datetime):
            lines.append(f"时间: {event_time.isoformat()}")
        else:
            lines.append(f"时间: {event_time}")
    lines.append("来源: Longbridge top movers")
    return "\n".join(lines)


def format_market_mover_list(events: list[dict[str, Any]]) -> str:
    if not events:
        return "暂无 Longbridge 异动记录。"
    lines = ["*Longbridge 最近异动*", ""]
    for event in events:
        values = f" | {event['change_text']}" if event.get("change_text") else ""
        detail = f" — {event['detail']}" if event.get("detail") else ""
        lines.append(f"`{event['ticker']}` {event['event_type']}{values}{detail}")
    return "\n".join(lines)


def _change_to_direction(change: float | None) -> str | None:
    if change is None:
        return None
    if change > 0:
        return "bullish"
    if change < 0:
        return "bearish"
    return None


def _dict_field(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _parse_longbridge_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(int(value), UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _parse_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_change_pct(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value * 100:+.2f}%"


def _post_title(post: dict[str, Any]) -> str:
    for value in (
        post.get("title"),
        _locale_value(post.get("title_locale")),
        _locale_value(post.get("desc_locale")),
        post.get("description_html"),
    ):
        text = _strip_html(str(value or ""))
        if text:
            return text
    return ""


def _locale_value(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("original", "cn", "zhCN", "en", "hk"):
        if text := value.get(key):
            return str(text)
    return ""


def _post_url(post: dict[str, Any]) -> str | None:
    for key in ("web_url", "source_url", "detail_url"):
        if value := post.get(key):
            return str(value)
    return None


def _post_source(post: dict[str, Any]) -> str | None:
    source = post.get("post_source")
    if isinstance(source, dict) and source.get("name"):
        return str(source["name"])
    return None


def _strip_html(text: str) -> str:
    return " ".join(
        text.replace("<br>", " ")
        .replace("<br/>", " ")
        .replace("<br />", " ")
        .replace("<p>", " ")
        .replace("</p>", " ")
        .split()
    )


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
