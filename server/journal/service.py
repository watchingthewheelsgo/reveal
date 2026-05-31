"""Trading journal CRUD service."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import Trade


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().scheduler_timezone)).date()


async def add_trade(
    ticker: str,
    direction: str,
    entry_price: float,
    quantity: int,
    strategy: str | None = None,
    entry_reason: str | None = None,
    emotions_before: str | None = None,
    tags: list[str] | None = None,
) -> Trade:
    """Record a new trade entry."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        trade = Trade(
            trade_date=_today(),
            ticker=ticker.upper(),
            direction=direction.lower(),
            entry_price=entry_price,
            quantity=quantity,
            strategy=strategy,
            entry_reason=entry_reason,
            emotions_before=emotions_before,
            tags=tags,
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        return trade


async def close_trade(
    ticker: str,
    exit_price: float,
    exit_reason: str | None = None,
    emotions_after: str | None = None,
    review: str | None = None,
) -> Trade | None:
    """Close an open trade for a ticker (most recent open position)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(Trade)
            .where(and_(Trade.ticker == ticker.upper(), Trade.exit_price.is_(None)))
            .order_by(Trade.trade_date.desc())
            .limit(1)
        )
        trade = result.scalar_one_or_none()
        if trade is None:
            return None

        trade.exit_price = exit_price
        trade.exit_reason = exit_reason
        trade.emotions_after = emotions_after
        trade.review = review
        entry_price = float(trade.entry_price)
        quantity = int(trade.quantity)
        pnl = (exit_price - entry_price) * quantity
        if trade.direction == "short":
            pnl = -pnl
        trade.pnl = pnl

        await session.commit()
        await session.refresh(trade)
        return trade


async def add_note(ticker: str, note: str) -> Trade | None:
    """Add a review note to the most recent trade for a ticker."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(Trade)
            .where(Trade.ticker == ticker.upper())
            .order_by(Trade.trade_date.desc())
            .limit(1)
        )
        trade = result.scalar_one_or_none()
        if trade is None:
            return None

        existing = trade.review or ""
        trade.review = f"{existing}\n{note}".strip()
        await session.commit()
        await session.refresh(trade)
        return trade


async def get_trades_for_period(period: str = "today") -> Sequence[Trade]:
    """Get trades for a given period."""
    today = _today()
    session_factory = get_session_factory()

    if period == "today":
        start = today
    elif period == "week":
        start = today - timedelta(days=today.weekday())  # Monday
    elif period == "month":
        start = today.replace(day=1)
    elif period == "year":
        start = today.replace(month=1, day=1)
    elif period == "all":
        start = date(2000, 1, 1)
    else:
        start = today

    async with session_factory() as session:
        result = await session.execute(
            select(Trade).where(Trade.trade_date >= start).order_by(Trade.trade_date.desc())
        )
        return result.scalars().all()


async def get_pnl_summary(period: str = "month") -> dict:
    """Get P&L summary for a period."""
    trades = await get_trades_for_period(period)

    closed = [(t, cast(float, t.pnl)) for t in trades if t.pnl is not None]
    open_positions = [t for t in trades if t.pnl is None]

    total_pnl = sum(pnl for _, pnl in closed)
    wins = [(t, pnl) for t, pnl in closed if pnl > 0]
    losses = [(t, pnl) for t, pnl in closed if pnl <= 0]

    return {
        "period": period,
        "total_trades": len(trades),
        "closed_trades": len(closed),
        "open_positions": len(open_positions),
        "total_pnl": round(total_pnl, 2),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(closed), 3) if closed else 0,
        "avg_win": round(sum(pnl for _, pnl in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(pnl for _, pnl in losses) / len(losses), 2) if losses else 0,
        "best_trade": max(closed, key=lambda item: item[1])[0] if closed else None,
        "worst_trade": min(closed, key=lambda item: item[1])[0] if closed else None,
    }


def format_journal(trades: Sequence[Trade], period: str = "today") -> str:
    """Format trades for display."""
    if not trades:
        return f"📝 *{period}* 暂无交易记录"

    lines = [f"📝 *交易日记 — {period}*", ""]
    closed = [(t, cast(float, t.pnl)) for t in trades if t.pnl is not None]
    open_pos = [t for t in trades if t.pnl is None]

    if open_pos:
        lines.append("*当前持仓:*")
        for t in open_pos:
            direction = "📈" if t.direction == "long" else "📉"
            lines.append(
                f"  {direction} {t.ticker} x{t.quantity} @ ${t.entry_price:.2f} ({t.trade_date})"
            )
        lines.append("")

    if closed:
        lines.append("*已平仓:*")
        total_pnl = 0.0
        for t, pnl in closed:
            emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            total_pnl += pnl
            lines.append(
                f"  {emoji} {t.ticker} {t.direction} | "
                f"入场 ${t.entry_price:.2f} → 出场 ${t.exit_price:.2f} | "
                f"PnL: ${pnl:.2f} | {t.trade_date}"
            )
            if t.review:
                lines.append(f"      💭 {t.review[:100]}")

        lines.append("")
        lines.append(f"*合计 PnL: ${total_pnl:.2f}*")

    return "\n".join(lines)


def format_pnl(summary: dict) -> str:
    """Format P&L summary for display."""
    return f"""💰 *盈亏汇总 — {summary["period"]}*

已平仓: {summary["closed_trades"]} 笔 | 持仓中: {summary["open_positions"]} 笔
总盈亏: ${summary["total_pnl"]:+.2f}
胜率: {summary["win_rate"] * 100:.1f}% ({summary["win_count"]}W / {summary["loss_count"]}L)
平均盈利: ${summary["avg_win"]:+.2f}
平均亏损: ${summary["avg_loss"]:+.2f}
"""
