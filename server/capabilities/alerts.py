"""Reusable alert capability implementations."""

from typing import Any

from config.settings import get_settings


async def get_alert_status_payload() -> dict[str, Any]:
    """Return current alert configuration and watched tickers."""
    from server.alerts.engine import get_active_tickers_for_alert
    from server.alerts.market_movers import get_market_mover_status_payload
    from server.alerts.regulatory import get_regulatory_alert_status_payload

    settings = get_settings()
    return {
        "enabled": settings.alert_enabled,
        "interval_minutes": settings.alert_interval_minutes,
        "price_pct": settings.alert_price_pct,
        "volume_ratio": settings.alert_volume_ratio,
        "active_tickers": await get_active_tickers_for_alert(),
        "regulatory": await get_regulatory_alert_status_payload(),
        "market_movers": await get_market_mover_status_payload(),
    }
