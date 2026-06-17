"""
Volume anomaly alerts — detects unusual trading volume for watched tickers.
"""

from loguru import logger

from config.settings import get_settings
from server.events.types import VolumeAlertEvent, normalize_event_severity
from server.stock.data import fetch_stock_data


async def check_volume_alerts(tickers: list[str], threshold_ratio: float = 2.5) -> list[dict]:
    """Check for volume anomalies across watched tickers."""
    alerts = []
    critical_ratio = get_settings().alert_volume_critical_ratio

    for ticker in tickers:
        try:
            data = await fetch_stock_data(ticker, period="1mo")
            if data is None:
                continue

            vol_ratio = data.get("volume_ratio", 1.0)

            if vol_ratio >= threshold_ratio:
                severity = "critical" if vol_ratio >= critical_ratio else "warning"

                alerts.append(
                    {
                        "ticker": ticker,
                        "type": "成交量异常",
                        "severity": severity,
                        "price": data.get("current_price", 0),
                        "vol_ratio": vol_ratio,
                        "message": f"成交量突增 {vol_ratio:.1f}x 均量",
                        "detail": (
                            f"当前成交量是近20日均量的 {vol_ratio:.1f} 倍，"
                            "可能存在机构参与或重大事件"
                        ),
                    }
                )
        except Exception:
            logger.exception("Volume alert check failed for {}", ticker)

    return alerts


def volume_alert_event_from_payload(alert: dict) -> VolumeAlertEvent:
    """Convert a volume-alert payload into a typed runtime event."""

    ticker = str(alert.get("ticker") or "")
    volume_ratio = float(alert.get("vol_ratio") or alert.get("volume_ratio") or 0.0)
    price = alert.get("price")
    return VolumeAlertEvent(
        id=f"volume_alert:{ticker}:{volume_ratio:.4f}",
        kind="market_price",
        source="volume_alert",
        title=f"{ticker} {alert.get('type') or '成交量异常'}".strip(),
        summary=str(alert.get("message") or ""),
        severity=normalize_event_severity(alert.get("severity")),
        tickers=[ticker] if ticker else [],
        ticker=ticker,
        price=float(price) if isinstance(price, int | float) else None,
        volume_ratio=volume_ratio,
        threshold_ratio=None,
    )
