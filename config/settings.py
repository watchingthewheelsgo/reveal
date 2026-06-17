"""Application settings loaded from environment variables."""

import json
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

load_dotenv()

_TIMEZONE_ALIASES = {
    "US/Eastern": "America/New_York",
    "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
    "US/Alaska": "America/Anchorage",
    "US/Hawaii": "Pacific/Honolulu",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_admin_chat_id: str = Field(default="", alias="TELEGRAM_ADMIN_CHAT_ID")

    def get_telegram_admin_chat_ids(self) -> list[str]:
        if not self.telegram_admin_chat_id:
            return []
        return [cid.strip() for cid in self.telegram_admin_chat_id.split(",") if cid.strip()]

    # Feishu
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    feishu_verification_token: str = Field(default="", alias="FEISHU_VERIFICATION_TOKEN")
    feishu_encrypt_key: str = Field(default="", alias="FEISHU_ENCRYPT_KEY")
    feishu_admin_chat_id: str = Field(default="", alias="FEISHU_ADMIN_CHAT_ID")
    feishu_enable_ws: bool = Field(default=True, alias="FEISHU_ENABLE_WS")

    def is_feishu_configured(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret)

    def get_feishu_admin_chat_ids(self) -> list[str]:
        if not self.feishu_admin_chat_id:
            return []
        return [cid.strip() for cid in self.feishu_admin_chat_id.split(",") if cid.strip()]

    # Database
    database_url: str = Field(default="sqlite+aiosqlite:///./data/reveal.db", alias="DATABASE_URL")
    database_echo: bool = Field(default=False, alias="DATABASE_ECHO")

    # LLM
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="", alias="DEEPSEEK_MODEL")
    # Backward-compatible names for older OpenAI-compatible DeepSeek config.
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.deepseek.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="deepseek-chat", alias="OPENAI_MODEL")
    max_tokens: int = Field(default=32000, alias="MAX_TOKENS")
    temperature: float = Field(default=0.7, alias="TEMPERATURE")

    def is_llm_configured(self) -> bool:
        return bool(self.get_llm_auth_token())

    def get_llm_auth_token(self) -> str:
        return self.deepseek_api_key or self.anthropic_auth_token or self.openai_api_key

    def get_llm_base_url(self) -> str:
        return self.deepseek_base_url or self.openai_base_url or "https://api.deepseek.com/v1"

    def get_llm_model(self) -> str:
        return self.deepseek_model or self.openai_model or "deepseek-chat"

    # Finnhub
    finnhub_api_key: str = Field(default="", alias="FINNHUB_API_KEY")
    finnhub_base_url: str = Field(default="https://finnhub.io/api/v1", alias="FINNHUB_BASE_URL")

    def is_finnhub_configured(self) -> bool:
        return bool(self.finnhub_api_key)

    # Scheduler
    scheduler_timezone: str = Field(default="America/New_York", alias="SCHEDULER_TIMEZONE")
    twitter_monitor_interval: int = Field(default=3600, alias="TWITTER_MONITOR_INTERVAL")
    twitter_fetch_min_interval: int = Field(default=900, alias="TWITTER_FETCH_MIN_INTERVAL")
    daily_pick_time: str = Field(default="08:00", alias="DAILY_PICK_TIME")
    daily_briefing_time: str = Field(default="08:30", alias="DAILY_BRIEFING_TIME")
    twitter_digest_enabled: bool = Field(default=True, alias="TWITTER_DIGEST_ENABLED")
    twitter_digest_time: str = Field(default="17:00", alias="TWITTER_DIGEST_TIME")
    twitter_digest_timezone: str = Field(default="Asia/Shanghai", alias="TWITTER_DIGEST_TIMEZONE")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Alerts
    alert_enabled: bool = Field(default=True, alias="ALERT_ENABLED")
    alert_interval_minutes: int = Field(default=30, alias="ALERT_INTERVAL_MINUTES")
    alert_price_pct: float = Field(default=3.0, alias="ALERT_PRICE_PCT")
    alert_price_critical_pct: float = Field(default=5.0, alias="ALERT_PRICE_CRITICAL_PCT")
    alert_volume_ratio: float = Field(default=2.5, alias="ALERT_VOLUME_RATIO")
    alert_volume_critical_ratio: float = Field(
        default=4.0,
        alias="ALERT_VOLUME_CRITICAL_RATIO",
    )
    stock_watch_critical_multiplier: float = Field(
        default=2.0,
        alias="STOCK_WATCH_CRITICAL_MULTIPLIER",
    )

    # Regulatory event alerts
    regulatory_alert_enabled: bool = Field(default=True, alias="REGULATORY_ALERT_ENABLED")
    regulatory_alert_interval_minutes: int = Field(
        default=60, alias="REGULATORY_ALERT_INTERVAL_MINUTES"
    )
    regulatory_alert_lookback_hours: int = Field(
        default=24, alias="REGULATORY_ALERT_LOOKBACK_HOURS"
    )
    sec_user_agent: str = Field(default="", alias="SEC_USER_AGENT")
    sec_alert_forms: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "8-K",
            "10-K",
            "10-Q",
            "S-1",
            "F-1",
            "SC 13D",
            "SC 13G",
            "4",
            "424B",
            "DEF 14A",
        ],
        alias="SEC_ALERT_FORMS",
    )
    sec_alert_critical_forms: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["8-K", "S-1", "F-1", "424B"],
        alias="SEC_ALERT_CRITICAL_FORMS",
    )
    sec_alert_warning_forms: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["10-K", "10-Q", "SC 13D", "SC 13G", "DEF 14A"],
        alias="SEC_ALERT_WARNING_FORMS",
    )
    fda_alert_enabled: bool = Field(default=True, alias="FDA_ALERT_ENABLED")
    fda_base_url: str = Field(default="https://api.fda.gov", alias="FDA_BASE_URL")
    fda_alert_categories: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["drug", "device"], alias="FDA_ALERT_CATEGORIES"
    )
    fda_alert_classifications: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Class I", "Class II"], alias="FDA_ALERT_CLASSIFICATIONS"
    )
    fda_alert_critical_classifications: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Class I"], alias="FDA_ALERT_CRITICAL_CLASSIFICATIONS"
    )
    fda_alert_warning_classifications: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Class II"], alias="FDA_ALERT_WARNING_CLASSIFICATIONS"
    )
    fda_alert_keywords: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="FDA_ALERT_KEYWORDS"
    )

    # Longbridge market anomaly discovery
    longbridge_enabled: bool = Field(default=False, alias="LONGBRIDGE_ENABLED")
    longbridge_api_base: str = Field(
        default="https://openapi.longbridge.cn", alias="LONGBRIDGE_API_BASE"
    )
    longbridge_oauth_token_path: str = Field(default="", alias="LONGBRIDGE_OAUTH_TOKEN_PATH")
    longbridge_movers_enabled: bool = Field(default=True, alias="LONGBRIDGE_MOVERS_ENABLED")
    longbridge_movers_market: str = Field(default="US", alias="LONGBRIDGE_MOVERS_MARKET")
    longbridge_movers_interval_seconds: int = Field(
        default=300, alias="LONGBRIDGE_MOVERS_INTERVAL_SECONDS"
    )
    longbridge_movers_count: int = Field(default=50, alias="LONGBRIDGE_MOVERS_COUNT")
    longbridge_movers_push_limit: int = Field(default=10, alias="LONGBRIDGE_MOVERS_PUSH_LIMIT")

    def is_longbridge_configured(self) -> bool:
        return bool(self.longbridge_enabled and self.longbridge_oauth_token_path)

    # Twitter monitor
    twitter_accounts: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="TWITTER_ACCOUNTS"
    )
    twitter_auth_tokens: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="TWITTER_AUTH_TOKENS"
    )

    # Claude Agent SDK runtime backed by DeepSeek's Anthropic-compatible API.
    agent_runtime: str = Field(default="claude_sdk", alias="AGENT_RUNTIME")
    agent_effort: str = Field(default="max", alias="AGENT_EFFORT")
    agent_max_turns: int = Field(default=20, alias="AGENT_MAX_TURNS")

    # Native Claude Code / Agent SDK environment names.
    anthropic_base_url: str = Field(
        default="https://api.deepseek.com/anthropic", alias="ANTHROPIC_BASE_URL"
    )
    anthropic_auth_token: str = Field(default="", alias="ANTHROPIC_AUTH_TOKEN")
    anthropic_model: str = Field(default="deepseek-v4-pro[1m]", alias="ANTHROPIC_MODEL")
    anthropic_default_opus_model: str = Field(
        default="deepseek-v4-pro[1m]", alias="ANTHROPIC_DEFAULT_OPUS_MODEL"
    )
    anthropic_default_sonnet_model: str = Field(
        default="deepseek-v4-pro[1m]", alias="ANTHROPIC_DEFAULT_SONNET_MODEL"
    )
    anthropic_default_haiku_model: str = Field(
        default="deepseek-v4-flash", alias="ANTHROPIC_DEFAULT_HAIKU_MODEL"
    )

    def is_agent_configured(self) -> bool:
        return bool(self.get_agent_auth_token())

    def get_agent_base_url(self) -> str:
        return self.anthropic_base_url

    def get_agent_auth_token(self) -> str:
        return self.anthropic_auth_token or self.deepseek_api_key or self.openai_api_key

    def get_agent_model(self) -> str:
        return self.anthropic_model

    def get_agent_opus_model(self) -> str:
        return self.anthropic_default_opus_model

    def get_agent_sonnet_model(self) -> str:
        return self.anthropic_default_sonnet_model

    def get_agent_haiku_model(self) -> str:
        return self.anthropic_default_haiku_model

    @field_validator("twitter_accounts", mode="before")
    @classmethod
    def parse_twitter_accounts(cls, value) -> list[str]:
        return cls._parse_string_list(value, "TWITTER_ACCOUNTS")

    @field_validator("twitter_auth_tokens", mode="before")
    @classmethod
    def parse_twitter_auth_tokens(cls, value) -> list[str]:
        return cls._parse_string_list(value, "TWITTER_AUTH_TOKENS")

    @field_validator(
        "sec_alert_forms",
        "sec_alert_critical_forms",
        "sec_alert_warning_forms",
        "fda_alert_categories",
        "fda_alert_classifications",
        "fda_alert_critical_classifications",
        "fda_alert_warning_classifications",
        "fda_alert_keywords",
        mode="before",
    )
    @classmethod
    def parse_regulatory_lists(cls, value, info) -> list[str]:
        return cls._parse_string_list(value, info.field_name.upper())

    @classmethod
    def _parse_string_list(cls, value, field_name: str) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                raw_items = json.loads(stripped)
            else:
                raw_items = stripped.split(",")
        else:
            raise ValueError(f"{field_name} must be a comma-separated string or JSON list")

        items: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            normalized = str(item).strip()
            if field_name == "TWITTER_ACCOUNTS":
                normalized = normalized.lstrip("@")
            if normalized and normalized not in seen:
                items.append(normalized)
                seen.add(normalized)
        return items

    @field_validator("daily_pick_time", "daily_briefing_time", "twitter_digest_time")
    @classmethod
    def validate_hhmm_time(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError("time values must use HH:MM format")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise ValueError("time values must use numeric HH:MM format") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("time values must be in 00:00..23:59")
        return f"{hour:02d}:{minute:02d}"

    @field_validator(
        "twitter_monitor_interval",
        "twitter_fetch_min_interval",
        "alert_interval_minutes",
        "regulatory_alert_interval_minutes",
        "regulatory_alert_lookback_hours",
        "longbridge_movers_interval_seconds",
        "longbridge_movers_count",
        "longbridge_movers_push_limit",
    )
    @classmethod
    def validate_positive_interval(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("interval values must be positive")
        return value

    @field_validator(
        "alert_price_pct",
        "alert_price_critical_pct",
        "alert_volume_ratio",
        "alert_volume_critical_ratio",
        "stock_watch_critical_multiplier",
    )
    @classmethod
    def validate_positive_alert_thresholds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("alert thresholds must be positive")
        return value

    @field_validator("longbridge_movers_market")
    @classmethod
    def validate_longbridge_market(cls, value: str) -> str:
        normalized = value.upper().strip()
        if normalized not in {"US", "HK", "CN", "SG"}:
            raise ValueError("LONGBRIDGE_MOVERS_MARKET must be one of: US, HK, CN, SG")
        return normalized

    @field_validator("agent_runtime")
    @classmethod
    def validate_agent_runtime(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized != "claude_sdk":
            raise ValueError("AGENT_RUNTIME must be claude_sdk")
        return normalized

    @field_validator("agent_max_turns")
    @classmethod
    def validate_positive_agent_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("AGENT_MAX_TURNS must be positive")
        return value

    @field_validator("agent_effort")
    @classmethod
    def validate_agent_effort(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("AGENT_EFFORT must be one of: low, medium, high, xhigh, max")
        return normalized

    @field_validator("scheduler_timezone", "twitter_digest_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = _TIMEZONE_ALIASES.get(value.strip(), value.strip())
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return normalized


global_settings = Settings()


def get_settings() -> Settings:
    return global_settings
