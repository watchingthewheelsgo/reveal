"""Reusable alert capability implementations."""

from typing import Any

from config.settings import get_settings


async def get_alert_status_payload() -> dict[str, Any]:
    """Return current alert configuration and watched tickers."""
    from server.alerts.engine import get_active_tickers_for_alert

    settings = get_settings()
    return {
        "enabled": settings.alert_enabled,
        "interval_minutes": settings.alert_interval_minutes,
        "price_pct": settings.alert_price_pct,
        "volume_ratio": settings.alert_volume_ratio,
        "active_tickers": await get_active_tickers_for_alert(),
    }
