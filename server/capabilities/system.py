"""Reusable system capability implementations."""

from dataclasses import asdict
from typing import Any

from sqlalchemy.exc import ArgumentError

from config.settings import get_settings
from server.capabilities.registry import (
    BUILTIN_AGENT_TOOLS,
    DISALLOWED_LOCAL_TOOLS,
    list_capabilities,
    list_external_services,
)


def get_capability_catalog_payload() -> dict[str, Any]:
    """Return the full capability/service catalog in a machine-readable shape."""
    return {
        "capabilities": [asdict(cap) for cap in list_capabilities()],
        "external_services": [asdict(service) for service in list_external_services()],
        "builtin_agent_tools": BUILTIN_AGENT_TOOLS,
        "disallowed_local_tools": DISALLOWED_LOCAL_TOOLS,
    }


def get_system_status_payload() -> dict[str, Any]:
    """Return runtime configuration status without leaking secrets."""
    from server.db import engine as db_engine
    from server.db.engine import normalize_database_url

    settings = get_settings()
    database_driver = ""
    database_host = ""
    database_name = ""
    try:
        url = normalize_database_url(settings.database_url)
        database_driver = url.drivername
        database_host = url.host or "local"
        database_name = url.database or ""
    except ArgumentError:
        database_driver = "invalid"

    return {
        "bots": {
            "telegram": {
                "configured": bool(settings.telegram_bot_token),
                "admin_chats": len(settings.get_telegram_admin_chat_ids()),
            },
            "feishu": {
                "configured": settings.is_feishu_configured(),
                "websocket_enabled": settings.feishu_enable_ws,
                "admin_chats": len(settings.get_feishu_admin_chat_ids()),
            },
        },
        "llm": {
            "lightweight_configured": settings.is_llm_configured(),
            "lightweight_base_url": settings.get_llm_base_url(),
            "lightweight_model": settings.get_llm_model(),
            "agent_configured": settings.is_agent_configured(),
            "agent_runtime": settings.agent_runtime,
            "agent_base_url": settings.get_agent_base_url(),
            "agent_model": settings.get_agent_model(),
            "agent_max_turns": settings.agent_max_turns,
            "agent_effort": settings.agent_effort,
        },
        "market_data": {
            "finnhub_configured": settings.is_finnhub_configured(),
            "finnhub_base_url": settings.finnhub_base_url,
            "yfinance_available": True,
        },
        "twitter": {
            "configured_accounts": len(settings.twitter_accounts),
            "graphql_tokens": len(settings.twitter_auth_tokens),
            "monitor_interval_seconds": settings.twitter_monitor_interval,
            "digest_enabled": settings.twitter_digest_enabled,
            "digest_time": settings.twitter_digest_time,
            "digest_timezone": settings.twitter_digest_timezone,
        },
        "database": {
            "initialized": db_engine.engine is not None,
            "driver": database_driver,
            "host": database_host,
            "database": database_name,
        },
        "scheduler": {
            "timezone": settings.scheduler_timezone,
            "daily_pick_time": settings.daily_pick_time,
            "daily_briefing_time": settings.daily_briefing_time,
        },
        "alerts": {
            "enabled": settings.alert_enabled,
            "interval_minutes": settings.alert_interval_minutes,
            "price_pct": settings.alert_price_pct,
            "volume_ratio": settings.alert_volume_ratio,
        },
    }


def format_system_status(payload: dict[str, Any]) -> str:
    """Format system status for IM clients."""
    bots = payload["bots"]
    llm = payload["llm"]
    market = payload["market_data"]
    database = payload["database"]
    twitter = payload["twitter"]
    scheduler = payload["scheduler"]
    alerts = payload["alerts"]
    return "\n".join(
        [
            "*Reveal 系统状态*",
            "",
            f"Telegram Bot: {_flag(bots['telegram']['configured'])}",
            f"飞书 Bot: {_flag(bots['feishu']['configured'])} "
            f"(WS {_flag(bots['feishu']['websocket_enabled'])})",
            f"轻量 LLM: {_flag(llm['lightweight_configured'])} "
            f"{llm['lightweight_model']} @ {llm['lightweight_base_url']}",
            f"研究 Agent: {_flag(llm['agent_configured'])} "
            f"{llm['agent_model']} @ {llm['agent_base_url']}",
            f"Finnhub: {_flag(market['finnhub_configured'])}",
            f"数据库: {_flag(database['initialized'])} "
            f"{database['driver']}://{database['host']}/{database['database']}",
            f"Twitter: {twitter['configured_accounts']} env accounts, "
            f"{twitter['graphql_tokens']} GraphQL tokens, "
            f"interval {twitter['monitor_interval_seconds']}s",
            f"时区: {scheduler['timezone']}",
            f"选股时间: {scheduler['daily_pick_time']} (ET)",
            f"日报: {_flag(twitter['digest_enabled'])} "
            f"{twitter['digest_time']} {twitter['digest_timezone']}",
            f"告警: {_flag(alerts['enabled'])} every {alerts['interval_minutes']}m",
        ]
    )


def _flag(value: bool) -> str:
    return "✅" if value else "❌"
