"""
Price movement alerts — detects significant intraday price changes for watched tickers.
"""

from loguru import logger

from server.stock.data import fetch_stock_data


async def check_price_alerts(tickers: list[str], threshold_pct: float = 3.0) -> list[dict]:
    """Check for significant price movements across watched tickers."""
    alerts = []

    for ticker in tickers:
        try:
            data = await fetch_stock_data(ticker, period="1d")
            if data is None:
                continue

            change_pct = data.get("change_pct", 0)
            price = data.get("current_price", 0)

            if abs(change_pct) >= threshold_pct:
                direction = "📈 上涨" if change_pct > 0 else "📉 下跌"
                severity = "critical" if abs(change_pct) >= 5 else "warning"

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
        except Exception as e:
            logger.debug(f"Price alert check failed for {ticker}: {e}")

    return alerts
