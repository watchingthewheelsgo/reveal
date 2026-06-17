"""Concise daily market briefing for the morning trading workflow."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import desc, or_, select

from config.settings import get_settings
from server.db.engine import get_session_factory

MAX_BRIEFING_POSITIONS = 3
MAX_BRIEFING_SIGNALS = 3
MAX_BRIEFING_RESEARCH = 2
MAX_BRIEFING_WATCHED = 8


async def generate_daily_briefing() -> str:
    """Generate a compact action-oriented morning briefing."""
    today = _today()
    market_lines = await _market_context_lines()
    open_trades = await _open_trades()
    position_lines = await _position_lines(open_trades)
    signal_lines = await _recent_signal_lines()
    pnl_line = await _yesterday_pnl_line(today)
    research_lines = await _recent_research_lines(today)
    watched_line = await _watched_ticker_line(open_trades)
    focus_lines = _focus_lines(market_lines, position_lines, signal_lines, pnl_line, watched_line)

    lines = [
        f"*Reveal · 今日简报 — {today.isoformat()}*",
        "",
        "*今日重点*",
        *(focus_lines or ["- 暂无需要立即处理的新增事项。"]),
        "",
        "*持仓 / 关注*",
        *(position_lines or ["- 暂无记录中的持仓。"]),
    ]
    if watched_line:
        lines.append(watched_line)

    lines.extend(
        [
            "",
            "*最新市场信号*",
            *(signal_lines or ["- 暂无新的重点信号。"]),
            "",
            "*复盘*",
            f"- {pnl_line}",
            *(research_lines or ["- 暂无近期研究回顾。"]),
            "",
            "*市场状态*",
            *market_lines,
        ]
    )
    return "\n".join(lines)


def _today():
    return datetime.now(ZoneInfo(get_settings().scheduler_timezone)).date()


async def _market_context_lines() -> list[str]:
    lines: list[str] = []
    try:
        from server.stock.data import fetch_stock_data

        spy = await fetch_stock_data("SPY", period="5d")
        if spy:
            change = spy.get("change_pct", 0)
            lines.append(f"- SPY ${spy.get('current_price', 0):.2f} ({change:+.2f}%)")
            rsi = spy.get("rsi_14")
            if rsi:
                status = "偏强" if rsi > 60 else "偏弱" if rsi < 40 else "中性"
                lines.append(f"- SPY RSI(14) {rsi}，{status}")

        vix = await fetch_stock_data("^VIX", period="5d")
        if vix:
            vix_price = vix.get("current_price", 0)
            vix_status = "低波动" if vix_price < 20 else "高波动" if vix_price > 30 else "正常"
            lines.append(f"- VIX {vix_price:.1f}，{vix_status}")
    except Exception:
        logger.exception("Market context fetch failed for daily briefing")
        lines.append("- 市场数据暂不可用")
    return lines or ["- 市场数据暂不可用"]


async def _open_trades() -> list:
    from server.journal.service import get_trades_for_period

    return [trade for trade in await get_trades_for_period("all") if trade.exit_price is None]


async def _position_lines(open_trades: list) -> list[str]:
    positions: list[tuple[float, str]] = []
    for trade in open_trades:
        try:
            current = await _get_price(trade.ticker)
        except Exception:
            logger.exception("Position price fetch failed for daily briefing: {}", trade.ticker)
            current = trade.entry_price
        unrealized = (current - trade.entry_price) * trade.quantity
        if trade.direction == "short":
            unrealized = -unrealized
        positions.append(
            (
                abs(unrealized),
                f"- {trade.ticker} x{trade.quantity}: ${current:.2f}, 浮盈亏 ${unrealized:+.2f}",
            )
        )

    positions.sort(key=lambda item: item[0], reverse=True)
    lines = [line for _, line in positions[:MAX_BRIEFING_POSITIONS]]
    if len(positions) > MAX_BRIEFING_POSITIONS:
        lines.append(f"- 另有 {len(positions) - MAX_BRIEFING_POSITIONS} 个持仓未展开。")
    return lines


async def _recent_signal_lines() -> list[str]:
    try:
        from server.db.models import SocialPost

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(SocialPost)
                .where(
                    SocialPost.is_pushed.is_(True),
                    or_(
                        SocialPost.is_noteworthy.is_(True),
                        SocialPost.urgency.in_(["high", "medium"]),
                    ),
                )
                .order_by(desc(SocialPost.posted_at))
                .limit(MAX_BRIEFING_SIGNALS)
            )
            recent_posts = result.scalars().all()

        lines: list[str] = []
        for post in recent_posts:
            tickers = ", ".join(str(ticker) for ticker in (post.mentioned_tickers or [])[:3])
            ticker_text = f" [{tickers}]" if tickers else ""
            preview = _compact(post.summary or post.attention_reason or post.content, 120)
            lines.append(f"- @{post.username}{ticker_text}: {preview}")
        return lines
    except Exception:
        logger.exception("Recent Twitter signals fetch failed for daily briefing")
        return ["- Twitter 数据暂不可用"]


async def _yesterday_pnl_line(today) -> str:
    from server.journal.service import get_trades_for_period

    yesterday = today - timedelta(days=1)
    yesterday_trades = [
        trade
        for trade in await get_trades_for_period("week")
        if trade.trade_date == yesterday and trade.pnl is not None
    ]
    if yesterday_trades:
        total_pnl = sum(trade.pnl if trade.pnl is not None else 0 for trade in yesterday_trades)
        return f"昨日已实现盈亏 ${total_pnl:+.2f}，{len(yesterday_trades)} 笔。"
    return "昨日无平仓交易。"


async def _recent_research_lines(today) -> list[str]:
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
                .limit(MAX_BRIEFING_RESEARCH)
            )
            recent_research = result.scalars().all()

        lines: list[str] = []
        if recent_research:
            for rs in recent_research:
                tickers = rs.mentioned_tickers or []
                if not tickers:
                    continue
                days_ago = (today - rs.updated_at.date()).days if rs.updated_at else 0
                topic_preview = (rs.topic or rs.answer or "")[:60]
                ticker_str = ", ".join(str(t) for t in tickers[:3])
                lines.append(f"- {days_ago}天前研究 [{ticker_str}]: {topic_preview}")
        return lines
    except Exception:
        logger.exception("Research recap fetch failed for daily briefing")
        return ["- 研究回顾数据暂不可用"]


async def _watched_ticker_line(open_trades: list) -> str:
    watched = {trade.ticker for trade in open_trades}
    from server.stock.tracker import get_active_tickers

    watched.update(await get_active_tickers())
    if not watched:
        return ""
    shown = sorted(watched)[:MAX_BRIEFING_WATCHED]
    suffix = f"，另有 {len(watched) - MAX_BRIEFING_WATCHED} 个" if len(watched) > len(shown) else ""
    return f"- 关注标的: {', '.join(shown)}{suffix}。"


def _focus_lines(
    market_lines: list[str],
    position_lines: list[str],
    signal_lines: list[str],
    pnl_line: str,
    watched_line: str,
) -> list[str]:
    focus: list[str] = []
    if market_lines:
        focus.append(f"- 市场: {market_lines[0].removeprefix('- ')}")
    if position_lines:
        focus.append(f"- 持仓: {position_lines[0].removeprefix('- ')}")
    if signal_lines:
        focus.append(f"- 信号: {signal_lines[0].removeprefix('- ')}")
    if watched_line:
        focus.append(f"- {watched_line.removeprefix('- ')}")
    focus.append(f"- {pnl_line}")
    return focus[:5]


async def _get_price(ticker: str) -> float:
    """Quick price lookup."""
    try:
        from server.stock.data import get_current_price

        price = await get_current_price(ticker)
        return price or 0
    except Exception:
        logger.exception("Quick price lookup failed for {}", ticker)
        return 0


def _compact(text: str | None, limit: int) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."
