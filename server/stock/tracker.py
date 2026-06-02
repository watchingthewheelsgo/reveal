"""30-day tracking system — tracks picks daily and feeds back to adjust scoring weights."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import StockPick, TrackingLog
from server.stock.data import fetch_spy_return, get_current_price
from server.stock.scorer import get_weights, save_weights


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().scheduler_timezone)).date()


async def update_tracking():
    """Daily update: log current prices for all active picks."""
    today = _today()
    session_factory = get_session_factory()
    async with session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(StockPick).where(StockPick.status == "active"))
        active_picks = result.scalars().all()

        for pick in active_picks:
            existing_result = await session.execute(
                select(TrackingLog).where(
                    TrackingLog.pick_id == pick.id,
                    TrackingLog.log_date == today,
                )
            )
            if existing_result.scalar_one_or_none():
                logger.debug(f"Tracking log already exists for {pick.ticker} on {today}")
                continue

            current_price = await get_current_price(pick.ticker)
            if current_price is None:
                continue

            pnl_pct = (current_price / pick.pick_price - 1) * 100
            benchmark_pnl_pct = await fetch_spy_return(pick.pick_date.isoformat())
            log = TrackingLog(
                pick_id=pick.id,
                log_date=today,
                current_price=current_price,
                pnl_pct=round(pnl_pct, 2),
                benchmark_pnl_pct=round(benchmark_pnl_pct, 2)
                if benchmark_pnl_pct is not None
                else None,
            )
            session.add(log)

            # Check if 30 days elapsed
            days_elapsed = (today - pick.pick_date).days
            if days_elapsed >= 30:
                pick.status = "completed"
                logger.info(f"Pick {pick.ticker} (id={pick.id}) completed 30 days")

        await session.commit()

    logger.info(f"Updated tracking for {len(active_picks)} active picks")


async def apply_feedback():
    """Apply tracking feedback to adjust scoring weights.

    Compares pick performance vs SPY benchmark and adjusts weights:
    - If picks consistently beat SPY, keep weights
    - If a factor consistently underperforms, reduce its weight
    - Simple heuristic: weight adjustment proportional to excess return
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        from sqlalchemy import select

        # Get completed picks with tracking logs
        result = await session.execute(
            select(StockPick)
            .where(StockPick.status == "completed")
            .order_by(StockPick.pick_date.desc())
            .limit(5)
        )
        completed = result.scalars().all()

        if len(completed) < 3:
            logger.debug("Not enough completed picks for feedback (< 3)")
            return

        excess_returns = []
        for pick in completed:
            # Get final tracking log for this pick
            log_result = await session.execute(
                select(TrackingLog)
                .where(TrackingLog.pick_id == pick.id)
                .order_by(TrackingLog.log_date.desc())
                .limit(1)
            )
            last_log = log_result.scalar_one_or_none()
            if last_log and last_log.benchmark_pnl_pct is not None:
                excess = last_log.pnl_pct - last_log.benchmark_pnl_pct
                excess_returns.append(excess)

        if not excess_returns:
            return

        avg_excess = sum(excess_returns) / len(excess_returns)
        logger.info(f"Feedback: avg excess return vs SPY = {avg_excess:.2f}%")

        weights = await get_weights()

        # If positive excess, slightly increase technical weight (strongest signal)
        # If negative, diversify toward fundamental
        if avg_excess > 2:
            weights["technical"] = min(0.5, weights["technical"] + 0.02)
            weights["news_sentiment"] = max(0.1, weights["news_sentiment"] - 0.02)
        elif avg_excess < -2:
            weights["technical"] = max(0.25, weights["technical"] - 0.02)
            weights["fundamental"] = min(0.35, weights["fundamental"] + 0.02)

        # Normalize to sum to 1.0
        total = sum(weights.values())
        weights = {k: round(v / total, 4) for k, v in weights.items()}

        await save_weights(weights)
        logger.info(f"Adjusted weights: {weights}")


async def get_tracking_report(ticker: str | None = None) -> str:
    """Generate a tracking report for active/completed picks."""
    today = _today()
    session_factory = get_session_factory()
    async with session_factory() as session:
        from sqlalchemy import select

        if ticker:
            result = await session.execute(
                select(StockPick)
                .where(StockPick.ticker == ticker.upper())
                .order_by(StockPick.pick_date.desc())
                .limit(3)
            )
        else:
            result = await session.execute(
                select(StockPick)
                .where(StockPick.status == "active")
                .order_by(StockPick.pick_date.desc())
            )
        picks = result.scalars().all()

        if not picks:
            return "暂无追踪记录"

        lines = ["*📊 追踪报告*", ""]
        for pick in picks:
            log_result = await session.execute(
                select(TrackingLog)
                .where(TrackingLog.pick_id == pick.id)
                .order_by(TrackingLog.log_date.desc())
            )
            logs = log_result.scalars().all()

            days = (today - pick.pick_date).days
            latest_price = logs[0].current_price if logs else pick.pick_price
            pnl = ((latest_price / pick.pick_price) - 1) * 100

            emoji = "🟢" if pnl > 0 else "🔴"
            lines.append(f"{emoji} *{pick.ticker}* — {pick.pick_date} (第{days}天)")
            lines.append(
                f"   推荐价: ${pick.pick_price:.2f} → 现价: ${latest_price:.2f} ({pnl:+.2f}%)"
            )

            if logs:
                spy_pnl = logs[0].benchmark_pnl_pct
                if spy_pnl is not None:
                    vs_spy = pnl - spy_pnl
                    lines.append(f"   vs SPY: {vs_spy:+.2f}%")
            lines.append("")

        return "\n".join(lines)


async def get_active_tickers() -> list[str]:
    """Return tickers currently being tracked (active picks)."""
    from sqlalchemy import select

    from server.db.models import StockPick

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(StockPick.ticker).where(StockPick.status == "active"))
        rows = result.all()
        return sorted({r[0] for r in rows})
