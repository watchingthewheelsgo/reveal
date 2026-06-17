"""
Price movement alerts — detects significant intraday price changes for watched tickers.
"""

from loguru import logger

from config.settings import get_settings
from server.events.types import PriceAlertEvent, normalize_event_severity
from server.stock.data import fetch_stock_data


async def check_price_alerts(tickers: list[str], threshold_pct: float = 3.0) -> list[dict]:
    """Check for significant price movements across watched tickers."""
    alerts = []
    critical_pct = get_settings().alert_price_critical_pct

    for ticker in tickers:
        try:
            data = await fetch_stock_data(ticker, period="1d")
            if data is None:
                continue

            change_pct = data.get("change_pct", 0)
            price = data.get("current_price", 0)

            if abs(change_pct) >= threshold_pct:
                direction = "📈 上涨" if change_pct > 0 else "📉 下跌"
                severity = "critical" if abs(change_pct) >= critical_pct else "warning"

                alerts.append(
                    {
                        "ticker": ticker,
                        "type": "价格异动",
                        "severity": severity,
                        "price": price,
                        "change_pct": change_pct,
                        "message": f"{direction} {abs(change_pct):.1f}%",
                        "detail": f"日内波动超过 {threshold_pct}% 阈值",
                    }
                )
        except Exception:
            logger.exception("Price alert check failed for {}", ticker)

    return alerts


def price_alert_event_from_payload(alert: dict) -> PriceAlertEvent:
    """Convert a price-alert payload into a typed runtime event."""

    ticker = str(alert.get("ticker") or "")
    change_pct = float(alert.get("change_pct") or 0.0)
    price = alert.get("price")
    return PriceAlertEvent(
        id=f"price_alert:{ticker}:{change_pct:.4f}",
        kind="market_price",
        source="price_alert",
        title=f"{ticker} {alert.get('type') or '价格异动'}".strip(),
        summary=str(alert.get("message") or ""),
        severity=normalize_event_severity(alert.get("severity")),
        tickers=[ticker] if ticker else [],
        ticker=ticker,
        current_price=float(price) if isinstance(price, int | float) else None,
        change_pct=change_pct,
        threshold_pct=None,
    )
