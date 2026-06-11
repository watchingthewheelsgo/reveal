"""
All SQLAlchemy ORM models for Reveal.
"""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Trading Journal
# ═══════════════════════════════════════════════════════════════════════════════


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, default=func.current_date())
    ticker: Mapped[str] = mapped_column(String(10))
    direction: Mapped[str] = mapped_column(String(10))  # long / short
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    strategy: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entry_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    emotions_before: Mapped[str | None] = mapped_column(String(50), nullable=True)
    emotions_after: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    review: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


# ═══════════════════════════════════════════════════════════════════════════════
# Stock Picks & Tracking
# ═══════════════════════════════════════════════════════════════════════════════


class StockPick(Base):
    __tablename__ = "stock_picks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pick_date: Mapped[date] = mapped_column(Date, default=func.current_date())
    ticker: Mapped[str] = mapped_column(String(10))
    pick_price: Mapped[float] = mapped_column(Float)
    scores: Mapped[dict] = mapped_column(JSON)  # {"technical": 0.8, "fundamental": 0.6, ...}
    factors_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / completed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class TrackingLog(Base):
    __tablename__ = "tracking_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pick_id: Mapped[int] = mapped_column(Integer)
    log_date: Mapped[date] = mapped_column(Date)
    current_price: Mapped[float] = mapped_column(Float)
    pnl_pct: Mapped[float] = mapped_column(Float)  # % 相对推荐价
    benchmark_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # SPY 同期表现
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class StockWatch(Base):
    __tablename__ = "stock_watchlist"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    chat_id: Mapped[str] = mapped_column(String(100), index=True)
    platform: Mapped[str] = mapped_column(String(20), default="auto")
    threshold_pct: Mapped[float] = mapped_column(Float, default=5.0)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "chat_id",
            "platform",
            name="uq_stock_watch_ticker_chat_platform",
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Regulatory Event Alerts
# ═══════════════════════════════════════════════════════════════════════════════


class RegulatoryEvent(Base):
    __tablename__ = "regulatory_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    ticker: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(20), default="info")
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    event_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Market Mover Alerts
# ═══════════════════════════════════════════════════════════════════════════════


class MarketMoverEvent(Base):
    __tablename__ = "market_mover_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    market: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Social Monitor State
# ═══════════════════════════════════════════════════════════════════════════════


class TwitterState(Base):
    __tablename__ = "twitter_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    last_tweet_epoch: Mapped[int] = mapped_column(Integer, default=0)
    newest_tweet_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    history_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class SocialPost(Base):
    __tablename__ = "social_posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50))
    tweet_id: Mapped[str] = mapped_column(String(50), unique=True)
    tweet_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    translated_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    media: Mapped[list | None] = mapped_column(JSON, nullable=True)
    links: Mapped[list | None] = mapped_column(JSON, nullable=True)
    referenced_tweets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_reply: Mapped[bool] = mapped_column(default=False)
    is_repost: Mapped[bool] = mapped_column(default=False)
    is_quote: Mapped[bool] = mapped_column(default=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime)
    mentioned_tickers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    topics: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    urgency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_noteworthy: Mapped[bool] = mapped_column(Boolean, default=False)
    attention_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_pushed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class ResearchSession(Base):
    __tablename__ = "research_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(100), index=True)
    agent_runtime: Mapped[str] = mapped_column(String(50), default="claude_sdk")
    agent_session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), default="twitter")
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(300), nullable=True)
    mentioned_tickers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class BotMessageBinding(Base):
    __tablename__ = "bot_message_bindings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(100), index=True)
    message_id: Mapped[str] = mapped_column(String(100), index=True)
    source_type: Mapped[str] = mapped_column(String(50), default="twitter")
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ═══════════════════════════════════════════════════════════════════════════════
# User Scheduled Tasks
# ═══════════════════════════════════════════════════════════════════════════════


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(100), index=True)
    platform: Mapped[str] = mapped_column(String(20), default="auto")
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_type: Mapped[str] = mapped_column(String(50), default="agent_research")
    prompt: Mapped[str] = mapped_column(Text)
    run_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Scoring Weights (dynamic, updated by feedback)
# ═══════════════════════════════════════════════════════════════════════════════


class ScoringWeights(Base):
    __tablename__ = "scoring_weights"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # technical / fundamental / news_sentiment / sector
    factor: Mapped[str] = mapped_column(String(50), unique=True)
    weight: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
