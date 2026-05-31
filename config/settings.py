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
    feishu_admin_chat_id: str = Field(default="", alias="FEISHU_ADMIN_CHAT_ID")

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
