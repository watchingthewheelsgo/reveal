"""Reusable trading journal capability implementations."""

from typing import Any

from server.db.models import Trade

VALID_PERIODS = ("today", "week", "month", "year", "all")


async def get_trading_journal_payload(period: str = "today") -> dict[str, Any]:
    """Return serialized trades for a period."""
    from server.journal.service import get_trades_for_period

    normalized = normalize_period(period, default="today")
    trades = await get_trades_for_period(normalized)
    return {
        "period": normalized,
        "count": len(trades),
        "trades": [trade_payload(trade) for trade in trades],
    }


async def get_pnl_summary_payload(period: str = "month") -> dict[str, Any]:
    """Return a JSON-safe P&L summary."""
    from server.journal.service import get_pnl_summary

    normalized = normalize_period(period, default="month")
    summary = await get_pnl_summary(normalized)
    payload = dict(summary)
    payload["best_trade"] = (
        trade_payload(summary["best_trade"]) if summary.get("best_trade") else None
    )
    payload["worst_trade"] = (
        trade_payload(summary["worst_trade"]) if summary.get("worst_trade") else None
    )
    return payload


def normalize_period(period: str, default: str) -> str:
    normalized = (period or default).lower().strip()
    return normalized if normalized in VALID_PERIODS else default


def trade_payload(trade: Trade) -> dict[str, Any]:
    return {
        "id": trade.id,
        "trade_date": trade.trade_date.isoformat() if trade.trade_date else None,
        "ticker": trade.ticker,
        "direction": trade.direction,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "quantity": trade.quantity,
        "strategy": trade.strategy,
        "entry_reason": trade.entry_reason,
        "exit_reason": trade.exit_reason,
        "pnl": trade.pnl,
        "tags": trade.tags or [],
        "review": trade.review,
        "created_at": trade.created_at.isoformat() if trade.created_at else None,
        "updated_at": trade.updated_at.isoformat() if trade.updated_at else None,
    }
