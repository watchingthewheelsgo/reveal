"""Context helpers for Reveal research tools."""

from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import desc, select

from server.db.engine import get_session_factory
from server.db.models import ResearchSession


async def build_portfolio_context() -> str:
    """Build a compact text snapshot of open positions for quick LLM answers."""
    try:
        from server.journal.service import get_trades_for_period
        from server.stock.data import get_current_price

        trades = await get_trades_for_period("all")
    except Exception:
        logger.exception("Portfolio context build failed while loading trades")
        return "持仓数据暂不可用。"

    open_positions = [trade for trade in trades if trade.exit_price is None]
    if not open_positions:
        return "当前无未平仓持仓。"

    lines = ["当前未平仓持仓:"]
    for trade in open_positions[:20]:
        current_price = trade.entry_price
        try:
            price = await get_current_price(trade.ticker)
            if price:
                current_price = price
        except Exception:
            logger.exception("Portfolio context price fetch failed for {}", trade.ticker)
        pnl = (current_price - trade.entry_price) * trade.quantity
        if trade.direction == "short":
            pnl = -pnl
        lines.append(
            "- "
            f"{trade.ticker} {trade.direction} "
            f"qty={trade.quantity} entry={trade.entry_price:.2f} "
            f"current={current_price:.2f} pnl={pnl:.2f}"
        )
    return "\n".join(lines)


async def build_ticker_context(ticker: str) -> str:
    """Build a compact internal-data context for a ticker."""
    ticker = ticker.upper().strip()
    sections: list[str] = [f"Ticker: {ticker}"]

    try:
        from server.stock.data import fetch_news, fetch_stock_data

        data = await fetch_stock_data(ticker, period="6mo")
        if data:
            sections.append(
                "行情/指标: "
                + _jsonish(
                    {
                        "price": data.get("current_price"),
                        "change_pct": data.get("change_pct"),
                        "rsi_14": data.get("rsi_14"),
                        "sma_20": data.get("sma_20"),
                        "sma_50": data.get("sma_50"),
                        "sma_200": data.get("sma_200"),
                        "pe_ratio": data.get("pe_ratio"),
                        "peg_ratio": data.get("peg_ratio"),
                        "sector": data.get("sector"),
                    }
                )
            )
        news = await fetch_news(ticker, limit=5)
        if news:
            sections.append("近期新闻:")
            for article in news[:5]:
                headline = article.get("headline") or article.get("title") or ""
                url = article.get("url") or ""
                sections.append(f"- {headline} {url}".strip())
    except Exception:
        logger.exception("Ticker context market/news fetch failed for {}", ticker)
        sections.append("行情或新闻数据暂不可用。")

    history = await find_past_research_by_ticker(ticker, limit=3)
    if history:
        sections.append("历史研究:")
        for item in history:
            answer = str(item.get("answer") or "")
            sections.append(f"- #{item.get('id')} {item.get('topic')}: {answer[:200]}")

    return "\n".join(sections)


async def find_past_research_by_ticker(ticker: str, limit: int = 5) -> list[dict[str, Any]]:
    """Find recent research sessions mentioning a ticker."""
    ticker = ticker.upper().strip()
    if not ticker:
        return []

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession)
            .where(ResearchSession.answer.isnot(None))
            .order_by(desc(ResearchSession.updated_at), desc(ResearchSession.created_at))
            .limit(max(limit * 5, limit))
        )
        sessions = result.scalars().all()

    matches: list[dict[str, Any]] = []
    for item in sessions:
        mentioned = [str(value).upper() for value in (item.mentioned_tickers or [])]
        searchable = " ".join(
            value
            for value in [
                item.source_query or "",
                item.topic or "",
                item.answer or "",
            ]
            if value
        ).upper()
        if ticker not in mentioned and ticker not in searchable:
            continue
        matches.append(
            {
                "id": item.id,
                "source_type": item.source_type,
                "source_query": item.source_query,
                "topic": item.topic,
                "mentioned_tickers": item.mentioned_tickers or [],
                "answer": item.answer or "",
                "updated_at": _isoformat(item.updated_at),
            }
        )
        if len(matches) >= limit:
            break
    return matches


def _jsonish(data: dict[str, Any]) -> str:
    parts = []
    for key, value in data.items():
        if value is not None:
            parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else "无可用数据"


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
