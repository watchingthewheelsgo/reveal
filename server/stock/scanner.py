"""Daily stock scanner — scans watchlist, scores each candidate, picks the best one."""

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import StockPick
from server.stock.data import fetch_stock_data
from server.stock.scorer import score_stock

# Default watchlist — representative mid/large cap US stocks across sectors
DEFAULT_WATCHLIST = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "NFLX",
    "AMD",
    "CRM",
    "ADBE",
    "ORCL",
    "NOW",
    "INTU",
    "UBER",
    "JPM",
    "BAC",
    "GS",
    "V",
    "MA",
    "JNJ",
    "UNH",
    "PFE",
    "ABBV",
    "MRK",
    "XOM",
    "CVX",
    "COP",
    "HD",
    "NKE",
    "SBUX",
    "MCD",
    "COST",
    "WMT",
    "BA",
    "CAT",
    "GE",
    "RTX",
    "LMT",
    "PLTR",
    "SNOW",
    "DDOG",
    "CRWD",
    "ZS",
]


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().scheduler_timezone)).date()


async def run_daily_pick(watchlist: list[str] | None = None) -> dict | None:
    """Run the daily stock picking pipeline and return the top pick."""
    tickers = watchlist or DEFAULT_WATCHLIST
    logger.info(f"Running daily pick across {len(tickers)} stocks...")

    results = []
    for ticker in tickers:
        data = await fetch_stock_data(ticker, period="6mo")
        if data is None:
            continue
        try:
            scored = await score_stock(data)
            results.append(scored)
        except Exception as e:
            logger.warning(f"Scoring failed for {ticker}: {e}")

        # Rate limit: pause briefly between tickers
        await asyncio.sleep(0.3)

    if not results:
        logger.error("No stocks scored successfully")
        return None

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    top = results[0]
    logger.info(f"Top pick: {top['ticker']} (score={top['composite_score']})")

    # Save to database
    try:
        today = _today()
        session_factory = get_session_factory()
        async with session_factory() as session:
            from sqlalchemy import select

            existing = await session.execute(
                select(StockPick).where(
                    StockPick.pick_date == today,
                    StockPick.ticker == top["ticker"],
                    StockPick.status == "active",
                )
            )
            if existing.scalar_one_or_none():
                logger.info(f"Pick already exists for {top['ticker']} on {today}")
                return top

            pick = StockPick(
                pick_date=today,
                ticker=top["ticker"],
                pick_price=top["price"],
                scores={
                    "composite": top["composite_score"],
                    **{k: v["score"] for k, v in top["factors"].items()},
                },
                factors_detail=top["factors"],
                reason=_build_reason(top),
            )
            session.add(pick)
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to save pick: {e}")

    return top


def _build_reason(scored: dict) -> str:
    """Build a human-readable reason for the pick."""
    lines = []
    factor_names = {
        "technical": "技术面",
        "fundamental": "基本面",
        "news_sentiment": "新闻情绪",
        "sector": "板块强度",
    }
    for factor, detail in scored["factors"].items():
        name = factor_names.get(factor, factor)
        lines.append(f"{name}({detail['score']:.2f}): {detail['reason']}")
    return "\n".join(lines)


def format_pick_message(pick: dict) -> str:
    """Format a stock pick as a readable message."""
    lines = [
        f"📈 *今日精选：{pick['ticker']} — {pick.get('name', '')}*",
        "",
        f"💰 当前价格：${pick['price']:.2f}",
        f"📊 综合评分：{pick['composite_score']:.3f}",
        "",
        "*评分明细：*",
    ]
    factor_names = {
        "technical": "技术面",
        "fundamental": "基本面",
        "news_sentiment": "新闻情绪",
        "sector": "板块强度",
    }
    for key, detail in pick["factors"].items():
        emoji = "🟢" if detail["score"] >= 0.7 else "🟡" if detail["score"] >= 0.5 else "🔴"
        lines.append(
            f"  {emoji} {factor_names.get(key, key)} "
            f"({detail['weight'] * 100:.0f}%): {detail['score']:.2f}"
        )
        if detail.get("reason"):
            lines.append(f"      _{detail['reason']}_")

    lines.append("")
    lines.append(f"📝 追踪 30 天 /track {pick['ticker']}")

    return "\n".join(lines)
