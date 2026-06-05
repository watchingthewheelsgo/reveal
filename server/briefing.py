"""
Daily market briefing — integrated morning digest combining holdings, tracking,
Twitter signals, earnings calendar, and market context.
"""

from datetime import date, datetime, time, timedelta

from loguru import logger
from sqlalchemy import desc, select

from server.db.engine import get_session_factory


async def generate_daily_briefing() -> str:
    """Generate a comprehensive morning briefing."""
    today = date.today()
    lines = [
        f"*📋 每日简报 — {today}*",
        "",
        "*━━ 市场概览 ━━*",
    ]

    # Market context (SPY, VIX)
    try:
        from server.stock.data import fetch_stock_data

        spy = await fetch_stock_data("SPY", period="5d")
        if spy:
            change = spy.get("change_pct", 0)
            direction = "📈" if change > 0 else "📉"
            lines.append(f"{direction} SPY: ${spy.get('current_price', 0):.2f} ({change:+.2f}%)")
            rsi = spy.get("rsi_14")
            if rsi:
                status = "偏强" if rsi > 60 else "偏弱" if rsi < 40 else "中性"
                lines.append(f"   RSI(14): {rsi} ({status})")

        vix = await fetch_stock_data("^VIX", period="5d")
        if vix:
            vix_price = vix.get("current_price", 0)
            vix_status = "低波动" if vix_price < 20 else "高波动⚠️" if vix_price > 30 else "正常"
            lines.append(f"😰 VIX: {vix_price:.1f} ({vix_status})")
    except Exception:
        logger.exception("Market context fetch failed for daily briefing")
        lines.append("市场数据暂不可用")

    lines.append("")
    lines.append("*━━ 你的持仓 ━━*")

    # Open positions
    from server.journal.service import get_trades_for_period

    open_trades = [t for t in await get_trades_for_period("all") if t.exit_price is None]
    if open_trades:
        total_value = 0
        for t in open_trades:
            try:
                current = await _get_price(t.ticker)
            except Exception:
                logger.exception("Position price fetch failed for daily briefing: {}", t.ticker)
                current = t.entry_price
            unrealized = (current - t.entry_price) * t.quantity
            if t.direction == "short":
                unrealized = -unrealized
            total_value += current * t.quantity
            emoji = "🟢" if unrealized > 0 else "🔴"
            lines.append(
                f"{emoji} {t.ticker} x{t.quantity} | "
                f"入场 ${t.entry_price:.2f} → 现价 ${current:.2f} | "
                f"浮盈 ${unrealized:+.2f}"
            )
        lines.append(f"   *持仓市值: ${total_value:,.0f}*")
    else:
        lines.append("暂无持仓")

    lines.append("")
    lines.append("*━━ 追踪中的标的 ━━*")

    # Active tracking
    from server.stock.tracker import get_tracking_report

    report = await get_tracking_report()
    # Extract just the key data (first ~500 chars)
    lines.append(report[:600] if len(report) > 600 else report)

    lines.append("")
    lines.append("*━━ 近期 Twitter 信号 ━━*")

    # Recent Twitter signals
    try:
        from server.db.models import SocialPost

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(SocialPost)
                .where(SocialPost.is_pushed.is_(True))
                .order_by(desc(SocialPost.posted_at))
                .limit(3)
            )
            recent_posts = result.scalars().all()

            if recent_posts:
                for post in recent_posts:
                    preview = post.summary or post.content[:100]
                    lines.append(f"🐦 @{post.username}: {preview[:120]}")
            else:
                lines.append("暂无近期推文")
    except Exception:
        logger.exception("Recent Twitter signals fetch failed for daily briefing")
        lines.append("Twitter 数据暂不可用")

    lines.append("")
    lines.append("*━━ 昨日 P&L ━━*")

    # Yesterday's P&L
    yesterday = today - timedelta(days=1)
    yesterday_trades = [
        t
        for t in await get_trades_for_period("week")
        if t.trade_date == yesterday and t.pnl is not None
    ]
    if yesterday_trades:
        total_pnl = sum(t.pnl if t.pnl is not None else 0 for t in yesterday_trades)
        emoji = "🟢" if total_pnl > 0 else "🔴"
        lines.append(f"{emoji} 昨日已实现盈亏: ${total_pnl:+.2f} ({len(yesterday_trades)} 笔)")
    else:
        lines.append("昨日无平仓交易")

    lines.append("")
    lines.append("*━━ 研究回顾 ━━*")

    try:
        from server.db.models import ResearchSession

        session_factory = get_session_factory()
        async with session_factory() as session:
            seven_days_ago = today - timedelta(days=7)
            result = await session.execute(
                select(ResearchSession)
                .where(
                    ResearchSession.mentioned_tickers.isnot(None),
                    ResearchSession.answer.isnot(None),
                    ResearchSession.updated_at >= datetime.combine(seven_days_ago, time.min),
                )
                .order_by(desc(ResearchSession.updated_at))
                .limit(5)
            )
            recent_research = result.scalars().all()

        if recent_research:
            for rs in recent_research:
                tickers = rs.mentioned_tickers or []
                if not tickers:
                    continue
                days_ago = (today - rs.updated_at.date()).days if rs.updated_at else 0
                topic_preview = (rs.topic or rs.answer or "")[:60]
                ticker_str = ", ".join(str(t) for t in tickers[:3])
                lines.append(f"{days_ago}天前 [{ticker_str}]: {topic_preview}")
        else:
            lines.append("暂无近期研究记录")
    except Exception:
        logger.exception("Research recap fetch failed for daily briefing")
        lines.append("研究回顾数据暂不可用")

    lines.append("")
    lines.append("*━━ 今日事件 ━━*")

    # Earnings calendar (simplified — checks if any held/tracked tickers have earnings)
    watched = {t.ticker for t in open_trades}
    from server.stock.tracker import get_active_tickers

    watched.update(await get_active_tickers())
    if watched:
        lines.append(f"监控标的: {', '.join(sorted(watched)[:10])}")
        if len(watched) > 10:
            lines.append(f"  ... 及其他 {len(watched) - 10} 个")
    lines.append("财报日历接入需 Alpha Vantage 付费 API")

    return "\n".join(lines)


async def _get_price(ticker: str) -> float:
    """Quick price lookup."""
    try:
        from server.stock.data import get_current_price

        price = await get_current_price(ticker)
        return price or 0
    except Exception:
        logger.exception("Quick price lookup failed for {}", ticker)
        return 0
