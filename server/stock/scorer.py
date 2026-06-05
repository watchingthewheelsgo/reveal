"""
Multi-factor stock scoring system.
Weights are dynamically adjusted based on 30-day tracking feedback.
"""

from loguru import logger

from server.db.engine import get_session_factory
from server.db.models import ScoringWeights

DEFAULT_WEIGHTS = {
    "technical": 0.40,
    "fundamental": 0.25,
    "news_sentiment": 0.20,
    "sector": 0.15,
}


async def get_weights() -> dict[str, float]:
    """Load weights from DB, fall back to defaults."""
    weights = dict(DEFAULT_WEIGHTS)
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            from sqlalchemy import select

            result = await session.execute(select(ScoringWeights))
            rows = result.scalars().all()
            for row in rows:
                if row.factor in weights:
                    weights[row.factor] = row.weight
    except Exception:
        logger.exception("Failed to load scoring weights; using defaults")
    return weights


async def save_weights(weights: dict[str, float]):
    """Persist updated weights to DB."""
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            from sqlalchemy import select

            for factor, weight in weights.items():
                result = await session.execute(
                    select(ScoringWeights).where(ScoringWeights.factor == factor)
                )
                row = result.scalar_one_or_none()
                if row:
                    row.weight = weight
                else:
                    session.add(ScoringWeights(factor=factor, weight=weight))
            await session.commit()
    except Exception:
        logger.exception("Failed to save scoring weights")


def score_technical(data: dict) -> tuple[float, str]:
    """Score technical factors (0-1)."""
    reasons = []
    score = 0.5  # neutral start

    # RSI: 60-80 is good for trend (not overbought in strong trend)
    rsi = data.get("rsi_14")
    if rsi:
        if 60 <= rsi <= 80:
            score += 0.2
            reasons.append(f"RSI={rsi} 强劲趋势区间")
        elif 50 <= rsi < 60:
            score += 0.1
            reasons.append(f"RSI={rsi} 温和偏多")
        elif rsi > 80:
            score -= 0.1
            reasons.append(f"RSI={rsi} 超买需谨慎")

    # SMA alignment
    sma20 = data.get("sma_20")
    sma50 = data.get("sma_50")
    price = data.get("current_price")
    if sma20 and sma50 and price:
        if price > sma20 > sma50:
            score += 0.15
            reasons.append("均线多头排列 (Price>SMA20>SMA50)")
        elif price > sma20:
            score += 0.05
            reasons.append("价格在SMA20上方")

    # Volume anomaly
    vol_ratio = data.get("volume_ratio", 1.0)
    if vol_ratio > 2.0:
        score += 0.15
        reasons.append(f"放量{vol_ratio:.0f}x 机构参与可能")
    elif vol_ratio > 1.5:
        score += 0.08
        reasons.append(f"温和放量{vol_ratio:.1f}x")

    return min(score, 1.0), "; ".join(reasons) if reasons else "技术面中性"


def score_fundamental(data: dict) -> tuple[float, str]:
    """Score fundamental factors (0-1)."""
    reasons = []
    score = 0.5

    peg = data.get("peg_ratio")
    if peg:
        if peg < 1.0:
            score += 0.25
            reasons.append(f"PEG={peg:.2f} 低估增长")
        elif peg < 1.5:
            score += 0.1
            reasons.append(f"PEG={peg:.2f} 合理估值")

    rev_growth = data.get("revenue_growth")
    if rev_growth:
        if rev_growth > 0.25:
            score += 0.15
            reasons.append(f"营收增长{rev_growth * 100:.0f}% YoY")
        elif rev_growth > 0.1:
            score += 0.08
            reasons.append(f"营收增长{rev_growth * 100:.0f}% YoY")

    pe = data.get("pe_ratio")
    if pe and pe < 0:
        score -= 0.2
        reasons.append("负PE")

    return min(score, 1.0), "; ".join(reasons) if reasons else "基本面数据不足"


async def score_news_sentiment(ticker: str) -> tuple[float, str]:
    """Score based on news volume and simple sentiment (0-1)."""
    from server.stock.data import fetch_news

    articles = await fetch_news(ticker, limit=10)
    if not articles:
        return 0.5, "无近期新闻"

    # Simple heuristic: recent news volume is positive
    recent_count = len(articles)
    score = 0.5
    reasons = [f"近期{recent_count}篇新闻"]

    if recent_count >= 5:
        score += 0.15
        reasons.append("新闻活跃度高")
    elif recent_count >= 3:
        score += 0.08

    # Check for positive keywords
    positive_keywords = ["beat", "raise", "growth", "buyback", "upgrade", "breakthrough"]
    negative_keywords = ["miss", "cut", "downgrade", "layoff", "lawsuit", "investigation"]

    pos_count = 0
    neg_count = 0
    for a in articles:
        text = (a.get("headline", "") + " " + a.get("summary", "")).lower()
        pos_count += sum(1 for kw in positive_keywords if kw in text)
        neg_count += sum(1 for kw in negative_keywords if kw in text)

    if pos_count > neg_count:
        score += 0.1
        reasons.append("正面关键词占优")
    elif neg_count > pos_count:
        score -= 0.1
        reasons.append("负面关键词偏多")

    return min(score, 1.0), "; ".join(reasons)


def score_sector(data: dict, market_context: dict | None = None) -> tuple[float, str]:
    """Score sector strength (0-1)."""
    sector = data.get("sector", "Unknown")
    reasons = [f"板块: {sector}"]

    # Hot sectors (simplified — production version would use sector ETF relative strength)
    hot_sectors = [
        "Technology",
        "Communication Services",
        "Consumer Cyclical",
        "Energy",
    ]
    cold_sectors = ["Utilities", "Consumer Defensive", "Real Estate"]

    score = 0.5
    if sector in hot_sectors:
        score += 0.2
        reasons.append("热门板块")
    elif sector in cold_sectors:
        score -= 0.1
        reasons.append("防御性板块")

    return min(score, 1.0), "; ".join(reasons)


async def score_stock(data: dict) -> dict:
    """Run all scoring factors and return composite score."""
    weights = await get_weights()

    tech_score, tech_reason = score_technical(data)
    fund_score, fund_reason = score_fundamental(data)
    news_score, news_reason = await score_news_sentiment(data["ticker"])
    sector_score, sector_reason = score_sector(data)

    composite = (
        weights["technical"] * tech_score
        + weights["fundamental"] * fund_score
        + weights["news_sentiment"] * news_score
        + weights["sector"] * sector_score
    )

    return {
        "ticker": data["ticker"],
        "name": data.get("name", ""),
        "price": data.get("current_price", 0),
        "composite_score": round(composite, 3),
        "factors": {
            "technical": {
                "score": round(tech_score, 2),
                "weight": weights["technical"],
                "reason": tech_reason,
            },
            "fundamental": {
                "score": round(fund_score, 2),
                "weight": weights["fundamental"],
                "reason": fund_reason,
            },
            "news_sentiment": {
                "score": round(news_score, 2),
                "weight": weights["news_sentiment"],
                "reason": news_reason,
            },
            "sector": {
                "score": round(sector_score, 2),
                "weight": weights["sector"],
                "reason": sector_reason,
            },
        },
    }
