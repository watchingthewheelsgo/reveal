"""
Volume anomaly alerts — detects unusual trading volume for watched tickers.
"""

from loguru import logger

from server.stock.data import fetch_stock_data


async def check_volume_alerts(tickers: list[str], threshold_ratio: float = 2.5) -> list[dict]:
    """Check for volume anomalies across watched tickers."""
    alerts = []

    for ticker in tickers:
        try:
            data = await fetch_stock_data(ticker, period="1mo")
            if data is None:
                continue

            vol_ratio = data.get("volume_ratio", 1.0)

            if vol_ratio >= threshold_ratio:
                severity = "critical" if vol_ratio >= 4.0 else "warning"

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
