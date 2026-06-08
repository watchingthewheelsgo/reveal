"""
Alert engine — coordinates price, volume, and news checks for watched tickers.
Runs every 30 minutes during market hours (9:30 AM – 4:00 PM ET).
"""

from datetime import datetime, time

import pytz
from loguru import logger

from config.settings import get_settings
from server.alerts.news_alert import check_news_alerts
from server.alerts.price_alert import check_price_alerts
from server.alerts.volume_alert import check_volume_alerts


def _is_market_hours() -> bool:
    """Check if US market is open (Mon–Fri, 9:30 AM – 4:00 PM ET)."""
    tz = pytz.timezone(get_settings().scheduler_timezone)
    now = datetime.now(tz)
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    market_open = time(9, 30)
    market_close = time(16, 0)
    return market_open <= now.time() <= market_close


async def run_alert_cycle(adapter=None):
    """Run one full alert cycle across all watched tickers."""
    if not _is_market_hours():
        logger.debug("Outside market hours, skipping alerts")
        return

    from server.journal.service import get_trades_for_period
    from server.stock.tracker import get_active_tickers
    from server.stock.watchlist import get_manual_stock_watch_tickers

    settings = get_settings()

    # Collect tickers to watch: open positions + active tracking
    tickers: set[str] = set()

    open_trades = await get_trades_for_period("all")
    for t in open_trades:
        if t.exit_price is None:  # open position
            tickers.add(t.ticker)

    tracked = await get_active_tickers()
    tickers.update(tracked)
    manual_watch = await get_manual_stock_watch_tickers()
    tickers.update(manual_watch)

    if not tickers:
        logger.debug("No watched tickers for alert cycle")
        return

    tickers_list = sorted(tickers)
    logger.info(f"Alert cycle checking {len(tickers_list)} tickers: {tickers_list}")

    alerts: list[dict] = []

    # Run all checks in parallel
    price_alerts = await check_price_alerts(tickers_list, threshold_pct=settings.alert_price_pct)
    volume_alerts = await check_volume_alerts(
        tickers_list, threshold_ratio=settings.alert_volume_ratio
    )
    news_alerts = await check_news_alerts(tickers_list)

    alerts.extend(price_alerts)
    alerts.extend(volume_alerts)
    alerts.extend(news_alerts)

    # Deduplicate by ticker (keep highest severity)
    seen: dict[str, dict] = {}
    severity_order = {"critical": 3, "warning": 2, "info": 1}
    for a in alerts:
        key = a["ticker"]
        if key not in seen or severity_order.get(a.get("severity", "info"), 0) > severity_order.get(
            seen[key].get("severity", "info"), 0
        ):
            seen[key] = a

    if not seen:
        logger.debug("No alerts triggered")
        return

    # Push alerts
    for ticker, alert in seen.items():
        text = _format_alert(alert)
        if adapter:
            from server.delivery.service import send_alert_to_admin
            from server.events.types import AlertCandidate
            from server.stock.watchlist import platform_for_adapter

            platform = platform_for_adapter(adapter)
            await send_alert_to_admin(
                adapter,
                AlertCandidate(
                    event_key=_alert_event_key(alert),
                    event_type=_alert_event_type(alert),
                    source_id=ticker,
                    title=f"{alert.get('type', '告警')} — {ticker}",
                    summary=str(alert.get("message") or ""),
                    severity=str(alert.get("severity") or "info"),
                    payload=alert,
                ),
                text=text,
                platform=platform,
                reason="intraday alert cycle",
            )

    logger.info(f"Alert cycle complete: {len(seen)} alerts pushed")


async def get_active_tickers_for_alert() -> list[str]:
    """Get the set of tickers that should trigger alerts."""
    from server.journal.service import get_trades_for_period
    from server.stock.tracker import get_active_tickers
    from server.stock.watchlist import get_manual_stock_watch_tickers

    tickers: set[str] = set()
    open_trades = await get_trades_for_period("all")
    for t in open_trades:
        if t.exit_price is None:
            tickers.add(t.ticker)
    tracked = await get_active_tickers()
    tickers.update(tracked)
    manual_watch = await get_manual_stock_watch_tickers()
    tickers.update(manual_watch)
    return sorted(tickers)


def _format_alert(alert: dict) -> str:
    """Format an alert as a readable message."""
    severity_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
    emoji = severity_emoji.get(alert.get("severity", "info"), "🔵")

    lines = [
        f"{emoji} *{alert['type']} — {alert['ticker']}*",
        "",
        alert["message"],
    ]

    if alert.get("detail"):
        lines.append(f"_{alert['detail']}_")

    if alert.get("price"):
        lines.append(f"💰 现价: ${alert['price']:.2f}")

    return "\n".join(lines)


def _alert_event_key(alert: dict) -> str:
    ticker = str(alert.get("ticker") or "UNKNOWN").upper()
    alert_type = str(alert.get("type") or "alert")
    message = str(alert.get("message") or "")
    return f"alert:{alert_type}:{ticker}:{message}"


def _alert_event_type(alert: dict) -> str:
    alert_type = str(alert.get("type") or "").lower()
    if "价格" in alert_type or "price" in alert_type:
        return "price"
    if "成交量" in alert_type or "volume" in alert_type:
        return "volume"
    if "新闻" in alert_type or "news" in alert_type:
        return "news"
    return "news"
