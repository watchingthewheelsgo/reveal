"""Company news alert conversion helpers.

The old Finnhub headline urgency scanner is intentionally disabled. News
alerting should be re-enabled through the Agent/event pipeline with source
dedupe and an explicit relevance verdict.
"""

from server.events.types import NewsEvent, normalize_event_severity


async def check_news_alerts(tickers: list[str]) -> list[dict]:
    """Return no alerts until Agent-based news relevance is available."""
    del tickers
    return []


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
