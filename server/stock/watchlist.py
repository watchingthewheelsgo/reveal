"""Manual stock watchlist with per-chat price movement alerts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select

from server.db.engine import get_session_factory
from server.db.models import StockWatch
from server.stock.data import get_current_price

DEFAULT_STOCK_WATCH_THRESHOLD_PCT = 5.0
STOCK_WATCH_INTERVAL_SECONDS = 300


def normalize_ticker(ticker: str) -> str:
    """Normalize and validate a US stock ticker-ish symbol."""
    normalized = ticker.strip().upper().lstrip("$")
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", normalized):
        raise ValueError("ticker must look like a US stock symbol")
    return normalized


def normalize_platform(platform: str | None) -> str:
    value = (platform or "auto").strip().lower()
    if value in {"telegram", "feishu", "auto"}:
        return value
    return "auto"


def platform_for_adapter(adapter: Any) -> str:
    name = adapter.__class__.__name__.lower()
    if "telegram" in name:
        return "telegram"
    if "feishu" in name:
        return "feishu"
    return "auto"


async def add_stock_watch(
    ticker: str,
    chat_id: str,
    platform: str = "auto",
    threshold_pct: float = DEFAULT_STOCK_WATCH_THRESHOLD_PCT,
) -> dict[str, Any]:
    """Add or reactivate a ticker in a chat-specific watchlist."""
    normalized_ticker = normalize_ticker(ticker)
    normalized_platform = normalize_platform(platform)
    if not chat_id.strip():
        raise ValueError("chat_id is required")
    if threshold_pct <= 0:
        raise ValueError("threshold_pct must be positive")

    now = _utcnow()
    current_price = await get_current_price(normalized_ticker)
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(StockWatch).where(
                StockWatch.ticker == normalized_ticker,
                StockWatch.chat_id == chat_id,
                StockWatch.platform == normalized_platform,
            )
        )
        watch = result.scalar_one_or_none()
        created = watch is None
        if watch is None:
            watch = StockWatch(
                ticker=normalized_ticker,
                chat_id=chat_id,
                platform=normalized_platform,
            )
            session.add(watch)

        watch.threshold_pct = threshold_pct
        watch.last_price = current_price
        watch.last_checked_at = now if current_price is not None else None
        watch.is_active = True
        watch.updated_at = now
        await session.commit()

    return stock_watch_payload(watch, created=created)


async def remove_stock_watch(
    ticker: str, chat_id: str, platform: str | None = None
) -> dict[str, Any]:
    """Deactivate a ticker for a chat. If platform is omitted, remove it across platforms."""
    normalized_ticker = normalize_ticker(ticker)
    normalized_platform = normalize_platform(platform) if platform else None

    session_factory = get_session_factory()
    removed = 0
    async with session_factory() as session:
        clauses = [
            StockWatch.ticker == normalized_ticker,
            StockWatch.chat_id == chat_id,
            StockWatch.is_active.is_(True),
        ]
        if normalized_platform:
            clauses.append(StockWatch.platform == normalized_platform)
        result = await session.execute(select(StockWatch).where(*clauses))
        for watch in result.scalars().all():
            watch.is_active = False
            watch.updated_at = _utcnow()
            removed += 1
        await session.commit()

    return {
        "ticker": normalized_ticker,
        "chat_id": chat_id,
        "platform": normalized_platform,
        "removed": removed,
        "message": (
            f"{normalized_ticker} 已移出股票观察列表"
            if removed
            else f"{normalized_ticker} 不在观察列表中"
        ),
    }


async def get_stock_watch_list_payload(chat_id: str | None = None) -> dict[str, Any]:
    """Return active stock watchlist entries."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        query = select(StockWatch).where(StockWatch.is_active.is_(True))
        if chat_id:
            query = query.where(StockWatch.chat_id == chat_id)
        query = query.order_by(StockWatch.ticker.asc(), StockWatch.platform.asc())
        result = await session.execute(query)
        items = result.scalars().all()
    return {
        "count": len(items),
        "items": [stock_watch_payload(item) for item in items],
    }


async def get_manual_stock_watch_tickers() -> list[str]:
    """Return all active manual stock watch tickers."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(StockWatch.ticker).where(StockWatch.is_active.is_(True))
        )
        rows = result.all()
    return sorted({row[0] for row in rows})


async def run_stock_watch_price_cycle(adapters: dict[str, Any]) -> list[dict[str, Any]]:
    """Check manual watchlist prices and send alerts to their originating chats."""
    session_factory = get_session_factory()
    alerts: list[dict[str, Any]] = []
    async with session_factory() as session:
        result = await session.execute(
            select(StockWatch)
            .where(StockWatch.is_active.is_(True))
            .order_by(StockWatch.ticker.asc())
        )
        watches = result.scalars().all()
        now = _utcnow()

        for watch in watches:
            try:
                current_price = await get_current_price(watch.ticker)
                if current_price is None or current_price <= 0:
                    continue

                alert = _build_price_alert(watch, current_price)
                watch.last_price = current_price
                watch.last_checked_at = now
                watch.updated_at = now

                if alert:
                    await _send_stock_watch_alert(watch, alert, adapters)
                    alerts.append(alert)
            except Exception:
                logger.exception("Stock watch price check failed for {}", watch.ticker)

        await session.commit()

    if alerts:
        logger.info("Stock watch price cycle pushed {} alerts", len(alerts))
    return alerts


def stock_watch_payload(watch: StockWatch, created: bool | None = None) -> dict[str, Any]:
    payload = {
        "ticker": watch.ticker,
        "chat_id": watch.chat_id,
        "platform": watch.platform,
        "threshold_pct": watch.threshold_pct,
        "last_price": watch.last_price,
        "last_checked_at": _dt(watch.last_checked_at),
        "active": watch.is_active,
    }
    if created is not None:
        payload["created"] = created
    return payload


def format_stock_watch_list(payload: dict[str, Any]) -> str:
    items = payload["items"]
    if not items:
        return "当前会话没有股票观察列表。"

    lines = ["*股票观察列表*", ""]
    for item in items:
        price = item["last_price"]
        price_text = f"${price:.2f}" if isinstance(price, int | float) else "暂无基准价"
        lines.append(
            f"`{item['ticker']}` {price_text} | "
            f"阈值 {item['threshold_pct']:.1f}% | {item['platform']}"
        )
    return "\n".join(lines)


def format_stock_watch_add_result(payload: dict[str, Any]) -> str:
    price = payload["last_price"]
    price_text = f"${price:.2f}" if isinstance(price, int | float) else "暂无基准价"
    action = "加入" if payload.get("created") else "更新"
    return (
        f"✅ 已{action}股票观察: {payload['ticker']}\n"
        f"基准价: {price_text}\n"
        f"触发阈值: {payload['threshold_pct']:.1f}%（每 5 分钟对比上一轮检查）"
    )


def _build_price_alert(watch: StockWatch, current_price: float) -> dict[str, Any] | None:
    previous_price = watch.last_price
    if previous_price is None or previous_price <= 0:
        return None

    change_pct = (current_price / previous_price - 1) * 100
    if abs(change_pct) < watch.threshold_pct:
        return None

    direction = "上涨" if change_pct > 0 else "下跌"
    severity = "critical" if abs(change_pct) >= watch.threshold_pct * 2 else "warning"
    return {
        "ticker": watch.ticker,
        "chat_id": watch.chat_id,
        "platform": watch.platform,
        "severity": severity,
        "previous_price": previous_price,
        "current_price": current_price,
        "change_pct": change_pct,
        "threshold_pct": watch.threshold_pct,
        "message": (
            f"{watch.ticker} 较上次检查{direction} {abs(change_pct):.1f}%: "
            f"${previous_price:.2f} → ${current_price:.2f}"
        ),
    }


async def _send_stock_watch_alert(
    watch: StockWatch, alert: dict[str, Any], adapters: dict[str, Any]
) -> None:
    text = format_stock_watch_alert(alert)
    targets: list[Any]
    if watch.platform == "auto":
        targets = [adapter for adapter in adapters.values() if adapter is not None]
    else:
        adapter = adapters.get(watch.platform)
        targets = [adapter] if adapter is not None else []

    for adapter in targets:
        try:
            await adapter.send_message(watch.chat_id, text)
        except Exception:
            logger.exception(
                "Stock watch alert push failed: ticker={} platform={} chat_id={}",
                watch.ticker,
                watch.platform,
                watch.chat_id,
            )


def format_stock_watch_alert(alert: dict[str, Any]) -> str:
    emoji = "🔴" if alert["severity"] == "critical" else "🟡"
    return "\n".join(
        [
            f"{emoji} *股票观察异动 — {alert['ticker']}*",
            "",
            alert["message"],
            f"触发阈值: {alert['threshold_pct']:.1f}%",
            "检查间隔: 5 分钟",
        ]
    )


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
