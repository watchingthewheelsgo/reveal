"""Static system catalog for Reveal modules and data sources.

This catalog describes how existing code is organized. It is metadata for
status pages, Web workbench diagnostics, and Agent context; it is not a plugin
runtime and does not own execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from server.capabilities.registry import list_capabilities, list_external_services

DataSourceKind = Literal[
    "builtin",
    "internal_api",
    "database",
    "bot",
    "llm",
    "market_data",
    "regulatory",
    "social",
    "scheduler",
]
ModuleType = Literal["monitor", "alert", "interactive", "research", "report", "maintenance"]
ScheduleKind = Literal["interval", "cron", "on_demand", "runtime"]
OutputType = Literal[
    "event",
    "alert",
    "report",
    "research_session",
    "message",
    "domain_record",
    "status",
]


@dataclass(frozen=True)
class DataSourceSpec:
    """External or internal source that provides facts, execution, or delivery."""

    id: str
    title: str
    kind: DataSourceKind
    description: str
    config_keys: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    owner_modules: tuple[str, ...] = ()


@dataclass(frozen=True)
class SystemModuleSpec:
    """A stable description of an existing Reveal system module."""

    id: str
    title: str
    module_type: ModuleType
    owner_path: str
    schedule: ScheduleKind
    output_types: tuple[OutputType, ...] = ()
    data_sources: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()
    description: str = ""


DATA_SOURCES: tuple[DataSourceSpec, ...] = (
    DataSourceSpec(
        id="builtin.websearch",
        title="Claude Agent WebSearch",
        kind="builtin",
        description="Agent runtime built-in web search for fresh public facts.",
        event_types=("research_evidence",),
        owner_modules=("research_agent",),
    ),
    DataSourceSpec(
        id="builtin.webfetch",
        title="Claude Agent WebFetch",
        kind="builtin",
        description="Agent runtime built-in web page fetch for source inspection.",
        event_types=("research_evidence",),
        owner_modules=("research_agent",),
    ),
    DataSourceSpec(
        id="mcp.reveal",
        title="Reveal MCP adapter",
        kind="internal_api",
        description="In-process MCP tools exposing Reveal capabilities to the Agent runtime.",
        event_types=("tool_result",),
        owner_modules=("research_agent", "interactive_mcp"),
    ),
    DataSourceSpec(
        id="database.app",
        title="Reveal database",
        kind="database",
        description=(
            "Persistent state for source records, research, trades, watchlists, and bindings."
        ),
        config_keys=("DATABASE_URL",),
        event_types=("domain_record",),
        owner_modules=(
            "twitter_monitor",
            "stock_watch_price",
            "alert_cycle",
            "regulatory_alerts",
            "longbridge_market_movers",
            "research_agent",
            "daily_briefing",
            "twitter_digest",
            "trading_journal",
            "web_workbench",
        ),
    ),
    DataSourceSpec(
        id="bot.feishu",
        title="Feishu Bot API",
        kind="bot",
        description="Feishu messages, cards, topic replies, image upload, and event callbacks.",
        config_keys=("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_ADMIN_CHAT_ID"),
        event_types=("user_message", "delivery"),
        owner_modules=("feishu_interface",),
    ),
    DataSourceSpec(
        id="bot.telegram",
        title="Telegram Bot API",
        kind="bot",
        description="Telegram command, message, and admin push interface.",
        config_keys=("TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID"),
        event_types=("user_message", "delivery"),
        owner_modules=("telegram_interface",),
    ),
    DataSourceSpec(
        id="llm.deepseek_chat",
        title="DeepSeek chat",
        kind="llm",
        description=(
            "Lightweight OpenAI-compatible chat model for summarization and classification."
        ),
        config_keys=("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"),
        event_types=("llm_summary", "llm_classification"),
        owner_modules=("twitter_monitor", "twitter_digest", "daily_briefing"),
    ),
    DataSourceSpec(
        id="llm.deepseek_agent",
        title="DeepSeek Agent runtime",
        kind="llm",
        description="Anthropic-compatible model endpoint used by Claude Agent SDK.",
        config_keys=("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"),
        event_types=("research_session",),
        owner_modules=("research_agent",),
    ),
    DataSourceSpec(
        id="market.finnhub",
        title="Finnhub API",
        kind="market_data",
        description="Market quotes and company news.",
        config_keys=("FINNHUB_API_KEY", "FINNHUB_BASE_URL"),
        event_types=("quote", "company_news", "price_alert", "news_alert"),
        owner_modules=("market_tools", "alert_cycle", "daily_briefing"),
    ),
    DataSourceSpec(
        id="market.yfinance",
        title="Yahoo Finance via yfinance",
        kind="market_data",
        description="Historical market data, technical indicators, and fallback quote source.",
        event_types=("quote", "technical_indicator", "tracking_update"),
        owner_modules=("market_tools", "daily_pick", "tracking_update", "alert_cycle"),
    ),
    DataSourceSpec(
        id="market.longbridge",
        title="Longbridge OpenAPI",
        kind="market_data",
        description="OAuth-backed market anomaly discovery and quote permissions.",
        config_keys=("LONGBRIDGE_ENABLED", "LONGBRIDGE_OAUTH_TOKEN_PATH"),
        event_types=("market_mover",),
        owner_modules=("longbridge_market_movers",),
    ),
    DataSourceSpec(
        id="sec.edgar",
        title="SEC EDGAR APIs",
        kind="regulatory",
        description="SEC company ticker mapping and EDGAR submissions JSON.",
        config_keys=("SEC_USER_AGENT", "SEC_ALERT_FORMS"),
        event_types=("regulatory_event", "sec_filing"),
        owner_modules=("regulatory_alerts",),
    ),
    DataSourceSpec(
        id="fda.openfda",
        title="openFDA APIs",
        kind="regulatory",
        description="FDA enforcement endpoints for drug, device, and food recalls.",
        config_keys=("FDA_ALERT_ENABLED", "FDA_ALERT_CATEGORIES", "FDA_ALERT_KEYWORDS"),
        event_types=("regulatory_event", "fda_recall"),
        owner_modules=("regulatory_alerts",),
    ),
    DataSourceSpec(
        id="social.x_graphql",
        title="X/Twitter GraphQL",
        kind="social",
        description="Authenticated X/Twitter timeline source with cursor support.",
        config_keys=("TWITTER_AUTH_TOKENS",),
        event_types=("social_post",),
        owner_modules=("twitter_monitor", "twitter_watch"),
    ),
    DataSourceSpec(
        id="social.vxtwitter",
        title="vxTwitter API",
        kind="social",
        description="Public Twitter/X fallback and single-post hydration source.",
        event_types=("social_post",),
        owner_modules=("twitter_monitor", "twitter_watch"),
    ),
    DataSourceSpec(
        id="scheduler.apscheduler",
        title="APScheduler",
        kind="scheduler",
        description="Runtime scheduler for interval and cron jobs.",
        config_keys=("SCHEDULER_TIMEZONE",),
        event_types=("job_run",),
        owner_modules=("scheduler_runtime",),
    ),
)


SYSTEM_MODULES: tuple[SystemModuleSpec, ...] = (
    SystemModuleSpec(
        id="feishu_interface",
        title="Feishu interface",
        module_type="interactive",
        owner_path="server/bot/feishu.py",
        schedule="runtime",
        output_types=("message",),
        data_sources=("bot.feishu",),
        capability_ids=("system.help", "system.tools", "system.status"),
        description="Receives Feishu messages and sends text/cards/topic replies.",
    ),
    SystemModuleSpec(
        id="telegram_interface",
        title="Telegram interface",
        module_type="interactive",
        owner_path="server/bot/telegram.py",
        schedule="runtime",
        output_types=("message",),
        data_sources=("bot.telegram",),
        capability_ids=("system.help", "system.tools", "system.status"),
        description="Receives Telegram commands/messages and sends admin pushes.",
    ),
    SystemModuleSpec(
        id="web_workbench",
        title="Web workbench",
        module_type="interactive",
        owner_path="server/web.py",
        schedule="on_demand",
        output_types=("event", "research_session", "status"),
        data_sources=("database.app",),
        capability_ids=("research.tweet",),
        description="Browser workbench for cached posts and research sessions.",
    ),
    SystemModuleSpec(
        id="interactive_mcp",
        title="Reveal MCP tools",
        module_type="interactive",
        owner_path="server/mcp.py",
        schedule="on_demand",
        output_types=("message", "status", "domain_record"),
        data_sources=("mcp.reveal", "database.app"),
        capability_ids=(
            "system.tools",
            "system.status",
            "stock.quote",
            "stock.watch",
            "market.movers",
            "twitter.watch",
            "journal.view",
            "briefing.daily",
        ),
        description="Exposes controlled Reveal tools to Agent sessions.",
    ),
    SystemModuleSpec(
        id="research_agent",
        title="Research Agent",
        module_type="research",
        owner_path="server/research",
        schedule="on_demand",
        output_types=("research_session", "message"),
        data_sources=(
            "llm.deepseek_agent",
            "builtin.websearch",
            "builtin.webfetch",
            "mcp.reveal",
            "database.app",
        ),
        capability_ids=("research.ticker", "research.tweet", "research.history"),
        description="Runs multi-step Agent research with approved tools.",
    ),
    SystemModuleSpec(
        id="twitter_monitor",
        title="Twitter monitor",
        module_type="monitor",
        owner_path="server/social/monitor.py",
        schedule="interval",
        output_types=("event", "alert", "message", "domain_record"),
        data_sources=(
            "social.x_graphql",
            "social.vxtwitter",
            "llm.deepseek_chat",
            "database.app",
        ),
        capability_ids=("twitter.watch", "research.tweet"),
        description=(
            "Polls watched X/Twitter accounts, caches posts, and pushes noteworthy updates."
        ),
    ),
    SystemModuleSpec(
        id="twitter_watch",
        title="Twitter watchlist",
        module_type="interactive",
        owner_path="server/capabilities/twitter.py",
        schedule="on_demand",
        output_types=("domain_record", "event"),
        data_sources=("social.x_graphql", "social.vxtwitter", "database.app"),
        capability_ids=("twitter.watch",),
        description="Adds, removes, lists, and backfills watched Twitter/X accounts.",
    ),
    SystemModuleSpec(
        id="twitter_digest",
        title="Twitter digest",
        module_type="report",
        owner_path="server/social/digest.py",
        schedule="cron",
        output_types=("report", "message"),
        data_sources=("database.app", "llm.deepseek_chat"),
        capability_ids=("twitter.digest",),
        description="Generates account-level digest messages from cached social posts.",
    ),
    SystemModuleSpec(
        id="market_tools",
        title="Market tools",
        module_type="interactive",
        owner_path="server/capabilities/market.py",
        schedule="on_demand",
        output_types=("message", "status"),
        data_sources=("market.finnhub", "market.yfinance", "database.app"),
        capability_ids=(
            "stock.quote",
            "stock.technical",
            "stock.news",
            "stock.score",
            "portfolio.view",
            "research.history",
        ),
        description="Provides quotes, technical analysis, news, scoring, portfolio, and history.",
    ),
    SystemModuleSpec(
        id="daily_pick",
        title="Daily stock pick",
        module_type="report",
        owner_path="server/stock/scanner.py",
        schedule="cron",
        output_types=("domain_record", "message"),
        data_sources=("market.yfinance", "database.app"),
        capability_ids=("stock.pick",),
        description="Runs the daily stock scan and pushes the selected candidate.",
    ),
    SystemModuleSpec(
        id="tracking_update",
        title="Tracking update",
        module_type="maintenance",
        owner_path="server/stock/tracker.py",
        schedule="cron",
        output_types=("domain_record", "report"),
        data_sources=("market.yfinance", "database.app"),
        capability_ids=("stock.track",),
        description="Updates tracked picks and applies feedback after market close.",
    ),
    SystemModuleSpec(
        id="stock_watch_price",
        title="Stock watch price alerts",
        module_type="alert",
        owner_path="server/stock/watchlist.py",
        schedule="interval",
        output_types=("alert", "message", "domain_record"),
        data_sources=("market.finnhub", "market.yfinance", "database.app"),
        capability_ids=("stock.watch",),
        description="Checks per-chat stock watches every five minutes and pushes price moves.",
    ),
    SystemModuleSpec(
        id="alert_cycle",
        title="Intraday alert cycle",
        module_type="alert",
        owner_path="server/alerts/engine.py",
        schedule="interval",
        output_types=("alert", "message"),
        data_sources=("market.finnhub", "market.yfinance", "database.app"),
        capability_ids=("alert.manage",),
        description=(
            "Checks price, volume, and news alerts for watched tickers during market hours."
        ),
    ),
    SystemModuleSpec(
        id="regulatory_alerts",
        title="Regulatory alerts",
        module_type="alert",
        owner_path="server/alerts/regulatory.py",
        schedule="interval",
        output_types=("event", "alert", "message", "domain_record"),
        data_sources=("sec.edgar", "fda.openfda", "database.app"),
        capability_ids=("alert.manage",),
        description="Checks SEC filings and FDA enforcement events for watched names.",
    ),
    SystemModuleSpec(
        id="longbridge_market_movers",
        title="Longbridge market movers",
        module_type="alert",
        owner_path="server/alerts/market_movers.py",
        schedule="interval",
        output_types=("event", "alert", "message", "domain_record"),
        data_sources=("market.longbridge", "database.app"),
        capability_ids=("market.movers",),
        description="Discovers Longbridge anomaly events, dedupes, stores, and pushes new events.",
    ),
    SystemModuleSpec(
        id="daily_briefing",
        title="Daily market briefing",
        module_type="report",
        owner_path="server/briefing.py",
        schedule="cron",
        output_types=("report", "message"),
        data_sources=(
            "market.finnhub",
            "market.yfinance",
            "database.app",
            "llm.deepseek_chat",
        ),
        capability_ids=("briefing.daily",),
        description=(
            "Aggregates market context, holdings, tracked names, social signals, and research."
        ),
    ),
    SystemModuleSpec(
        id="trading_journal",
        title="Trading journal",
        module_type="interactive",
        owner_path="server/journal",
        schedule="on_demand",
        output_types=("domain_record", "report", "message"),
        data_sources=("database.app",),
        capability_ids=("journal.log", "journal.view", "portfolio.view"),
        description="Records trades and summarizes journal/P&L state.",
    ),
    SystemModuleSpec(
        id="scheduler_runtime",
        title="Scheduler runtime",
        module_type="maintenance",
        owner_path="server/scheduler.py",
        schedule="runtime",
        output_types=("status",),
        data_sources=("scheduler.apscheduler",),
        capability_ids=("system.status",),
        description="Registers and runs cron/interval jobs.",
    ),
)


def list_data_sources() -> tuple[DataSourceSpec, ...]:
    return DATA_SOURCES


def list_system_modules() -> tuple[SystemModuleSpec, ...]:
    return SYSTEM_MODULES


def get_data_source(source_id: str) -> DataSourceSpec | None:
    return _data_sources_by_id().get(source_id)


def get_system_module(module_id: str) -> SystemModuleSpec | None:
    return _modules_by_id().get(module_id)


def system_modules_for_data_source(source_id: str) -> tuple[SystemModuleSpec, ...]:
    return tuple(module for module in SYSTEM_MODULES if source_id in module.data_sources)


def data_sources_for_module(module_id: str) -> tuple[DataSourceSpec, ...]:
    module = get_system_module(module_id)
    if module is None:
        return ()
    sources = _data_sources_by_id()
    return tuple(sources[source_id] for source_id in module.data_sources if source_id in sources)


def get_system_catalog_payload() -> dict:
    """Return the system catalog as JSON-serializable dictionaries."""
    return {
        "data_sources": [asdict(source) for source in DATA_SOURCES],
        "system_modules": [asdict(module) for module in SYSTEM_MODULES],
    }


def validate_system_catalog() -> list[str]:
    """Return catalog consistency errors. Empty means valid."""
    errors: list[str] = []
    data_source_ids = {source.id for source in DATA_SOURCES}
    module_ids = {module.id for module in SYSTEM_MODULES}
    capability_ids = {cap.id for cap in list_capabilities()}
    external_service_ids = {service.id for service in list_external_services()}

    duplicate_sources = _duplicates(source.id for source in DATA_SOURCES)
    duplicate_modules = _duplicates(module.id for module in SYSTEM_MODULES)
    for source_id in duplicate_sources:
        errors.append(f"duplicate data source id: {source_id}")
    for module_id in duplicate_modules:
        errors.append(f"duplicate system module id: {module_id}")

    for source in DATA_SOURCES:
        if source.id not in external_service_ids:
            errors.append(f"data source is not registered external service: {source.id}")
        for module_id in source.owner_modules:
            if module_id not in module_ids:
                errors.append(f"data source {source.id} references unknown module: {module_id}")

    for module in SYSTEM_MODULES:
        for source_id in module.data_sources:
            if source_id not in data_source_ids:
                errors.append(f"module {module.id} references unknown data source: {source_id}")
        for capability_id in module.capability_ids:
            if capability_id not in capability_ids:
                errors.append(f"module {module.id} references unknown capability: {capability_id}")

    return errors


def _data_sources_by_id() -> dict[str, DataSourceSpec]:
    return {source.id: source for source in DATA_SOURCES}


def _modules_by_id() -> dict[str, SystemModuleSpec]:
    return {module.id: module for module in SYSTEM_MODULES}


def _duplicates(values) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates
