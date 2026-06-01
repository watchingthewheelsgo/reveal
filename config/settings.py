"""Application settings loaded from environment variables."""

import json
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

load_dotenv()


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
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.deepseek.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="deepseek-chat", alias="OPENAI_MODEL")
    max_tokens: int = Field(default=32000, alias="MAX_TOKENS")
    temperature: float = Field(default=0.7, alias="TEMPERATURE")

    def is_llm_configured(self) -> bool:
        return bool(self.openai_api_key)

    # Finnhub
    finnhub_api_key: str = Field(default="", alias="FINNHUB_API_KEY")
    finnhub_base_url: str = Field(default="https://finnhub.io/api/v1", alias="FINNHUB_BASE_URL")

    def is_finnhub_configured(self) -> bool:
        return bool(self.finnhub_api_key)

    # Scheduler
    scheduler_timezone: str = Field(default="US/Eastern", alias="SCHEDULER_TIMEZONE")
    twitter_monitor_interval: int = Field(default=3600, alias="TWITTER_MONITOR_INTERVAL")
    daily_pick_time: str = Field(default="08:00", alias="DAILY_PICK_TIME")
    daily_briefing_time: str = Field(default="08:30", alias="DAILY_BRIEFING_TIME")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Twitter monitor
    twitter_accounts: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="TWITTER_ACCOUNTS"
    )

    # Claude Agent SDK runtime backed by DeepSeek's Anthropic-compatible API.
    agent_runtime: str = Field(default="claude_sdk", alias="AGENT_RUNTIME")
    claude_agent_base_url: str = Field(
        default="https://api.deepseek.com/anthropic", alias="CLAUDE_AGENT_BASE_URL"
    )
    claude_agent_auth_token: str = Field(default="", alias="CLAUDE_AGENT_AUTH_TOKEN")
    claude_agent_model: str = Field(default="deepseek-v4-pro[1m]", alias="CLAUDE_AGENT_MODEL")
    claude_agent_small_model: str = Field(
        default="deepseek-v4-flash", alias="CLAUDE_AGENT_SMALL_MODEL"
    )
    claude_agent_effort: str = Field(default="max", alias="CLAUDE_AGENT_EFFORT")
    claude_agent_max_turns: int = Field(default=8, alias="CLAUDE_AGENT_MAX_TURNS")

    # Native Claude Code / Agent SDK environment names. These take precedence
    # over the Reveal-specific CLAUDE_AGENT_* names when present.
    anthropic_base_url: str = Field(default="", alias="ANTHROPIC_BASE_URL")
    anthropic_auth_token: str = Field(default="", alias="ANTHROPIC_AUTH_TOKEN")
    anthropic_model: str = Field(default="", alias="ANTHROPIC_MODEL")
    anthropic_default_opus_model: str = Field(default="", alias="ANTHROPIC_DEFAULT_OPUS_MODEL")
    anthropic_default_sonnet_model: str = Field(default="", alias="ANTHROPIC_DEFAULT_SONNET_MODEL")
    anthropic_default_haiku_model: str = Field(default="", alias="ANTHROPIC_DEFAULT_HAIKU_MODEL")

    def is_agent_configured(self) -> bool:
        return bool(self.get_agent_auth_token())

    def get_agent_base_url(self) -> str:
        return self.anthropic_base_url or self.claude_agent_base_url

    def get_agent_auth_token(self) -> str:
        return self.anthropic_auth_token or self.claude_agent_auth_token or self.openai_api_key

    def get_agent_model(self) -> str:
        return self.anthropic_model or self.claude_agent_model

    def get_agent_opus_model(self) -> str:
        return self.anthropic_default_opus_model or self.get_agent_model()

    def get_agent_sonnet_model(self) -> str:
        return self.anthropic_default_sonnet_model or self.get_agent_model()

    def get_agent_haiku_model(self) -> str:
        return self.anthropic_default_haiku_model or self.claude_agent_small_model

    @field_validator("twitter_accounts", mode="before")
    @classmethod
    def parse_twitter_accounts(cls, value) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            raw_accounts = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                raw_accounts = json.loads(stripped)
            else:
                raw_accounts = stripped.split(",")
        else:
            raise ValueError("TWITTER_ACCOUNTS must be a comma-separated string or JSON list")

        accounts: list[str] = []
        seen: set[str] = set()
        for item in raw_accounts:
            username = str(item).strip().lstrip("@")
            if username and username not in seen:
                accounts.append(username)
                seen.add(username)
        return accounts

    @field_validator("daily_pick_time", "daily_briefing_time")
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

    @field_validator("twitter_monitor_interval")
    @classmethod
    def validate_positive_interval(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("TWITTER_MONITOR_INTERVAL must be positive")
        return value

    @field_validator("agent_runtime")
    @classmethod
    def validate_agent_runtime(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized != "claude_sdk":
            raise ValueError("AGENT_RUNTIME must be claude_sdk")
        return normalized

    @field_validator("claude_agent_max_turns")
    @classmethod
    def validate_positive_agent_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("CLAUDE_AGENT_MAX_TURNS must be positive")
        return value

    @field_validator("claude_agent_effort")
    @classmethod
    def validate_claude_agent_effort(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("CLAUDE_AGENT_EFFORT must be one of: low, medium, high, xhigh, max")
        return normalized

    @field_validator("scheduler_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value


global_settings = Settings()


def get_settings() -> Settings:
    return global_settings
