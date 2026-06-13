"""
Breaking news alerts — detects fresh Finnhub headlines for watched tickers.
"""

from datetime import UTC

from loguru import logger

from server.events.types import NewsEvent, normalize_event_severity
from server.stock.data import fetch_news


async def check_news_alerts(tickers: list[str]) -> list[dict]:
    """Check for breaking news on watched tickers."""
    alerts = []

    for ticker in tickers:
        try:
            articles = await fetch_news(ticker, limit=5)
            if not articles:
                continue

            # Check for recent headlines (within last 2 hours)
            from datetime import datetime, timedelta

            cutoff = datetime.now(UTC) - timedelta(hours=2)
            recent = []
            for a in articles:
                try:
                    dt = datetime.fromisoformat(a.get("datetime", ""))
                    if dt > cutoff:
                        recent.append(a)
                except (ValueError, TypeError):
                    continue

            if not recent:
                continue

            # Check for urgency keywords
            urgent_keywords = [
                "breaking",
                "surge",
                "plunge",
                "crash",
                "rally",
                "acquisition",
                "merger",
                "lawsuit",
                "investigation",
                "recall",
                "fda",
                "earnings",
                "beat",
                "miss",
                "guidance",
                "layoff",
                "bankruptcy",
                "ceo",
            ]

            urgent = []
            for a in recent:
                text = (a.get("headline", "") + " " + a.get("summary", "")).lower()
                matches = [kw for kw in urgent_keywords if kw in text]
                if matches:
                    urgent.append((a, matches))

            if urgent:
                article, keywords = urgent[0]
                severity = (
                    "critical"
                    if any(k in ["crash", "plunge", "bankruptcy", "lawsuit"] for k in keywords)
                    else "warning"
                )

                alerts.append(
                    {
                        "ticker": ticker,
                        "type": "突发新闻",
                        "severity": severity,
                        "price": 0,
                        "message": f"📰 {article.get('headline', '')}",
                        "detail": (
                            f"关键词: {', '.join(keywords)} | "
                            f"来源: {article.get('source', '')} | "
                            f"{len(recent)} 条近期新闻"
                        ),
                    }
                )

        except Exception:
            logger.exception("News alert check failed for {}", ticker)

    return alerts


def news_event_from_alert_payload(alert: dict) -> NewsEvent:
    """Convert a news-alert payload into a typed runtime event."""

    ticker = str(alert.get("ticker") or "")
    headline = str(alert.get("message") or alert.get("title") or "")
    return NewsEvent(
        id=f"news_alert:{ticker}:{headline[:80]}",
        kind="news",
        source="finnhub_news",
        title=f"{ticker} 突发新闻".strip(),
        summary=headline,
        severity=normalize_event_severity(alert.get("severity")),
        tickers=[ticker] if ticker else [],
        ticker=ticker,
        headline=headline,
    )
