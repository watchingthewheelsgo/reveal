"""Database engine and session management."""

import os
from collections.abc import AsyncGenerator
from pathlib import Path

from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import global_settings

engine = None
AsyncSessionLocal = None


async def init_db() -> None:
    global engine, AsyncSessionLocal
    from server.db.models import Base

    db_url = normalize_database_url(global_settings.database_url)
    # Ensure data directory exists for SQLite
    if db_url.get_backend_name() == "sqlite":
        db_path = make_url(db_url).database
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        db_url,
        echo=global_settings.database_echo,
        future=True,
    )
    AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    logger.info(
        "Database configured: driver={} host={} database={}",
        db_url.drivername,
        db_url.host or "local",
        db_url.database or "",
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if db_url.get_backend_name() == "sqlite":
                await _migrate_sqlite_twitter_state(conn)
                await _migrate_sqlite_social_posts(conn)
                await _migrate_sqlite_research_sessions(conn)
    except OSError as exc:
        logger.exception(
            "Database init OS error: context={} exc_type={} error={}",
            database_diagnostic_context(db_url),
            type(exc).__name__,
            exc,
        )
        if engine:
            await engine.dispose()
        engine = None
        AsyncSessionLocal = None
        if is_render_supabase_direct_url(db_url):
            logger.error(
                "Database connection failed before authentication. Render cannot reach the "
                "Supabase direct database host over IPv6. Set DATABASE_URL to the Supabase "
                "Session Pooler URL from Dashboard > Connect."
            )
            raise RuntimeError(
                "Render cannot reach Supabase's direct db.<project-ref>.supabase.co host. "
                "Use the Supabase Session Pooler connection string for DATABASE_URL."
            ) from exc
        raise
    except Exception as exc:
        logger.exception(
            "Database init failed: context={} exc_type={} error={}",
            database_diagnostic_context(db_url),
            type(exc).__name__,
            exc,
        )
        if engine:
            await engine.dispose()
        engine = None
        AsyncSessionLocal = None
        raise


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    if AsyncSessionLocal is None:
        logger.error(
            "Database session requested before initialization: context={}",
            database_diagnostic_context(),
        )
        raise RuntimeError("Database not initialized.")
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            logger.exception("Database session failed; rolling back transaction")
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db() -> None:
    global AsyncSessionLocal, engine
    if engine:
        await engine.dispose()
    engine = None
    AsyncSessionLocal = None


def get_session_factory():
    if AsyncSessionLocal is None:
        logger.error(
            "Database session factory requested before initialization: context={}",
            database_diagnostic_context(),
        )
        raise RuntimeError("Database not initialized.")
    return AsyncSessionLocal


def database_diagnostic_context(url: URL | None = None) -> dict[str, object]:
    """Return redacted database diagnostics for logs."""
    try:
        db_url = url or normalize_database_url(global_settings.database_url)
    except Exception as exc:
        return {
            "initialized": AsyncSessionLocal is not None,
            "url_valid": False,
            "url_error_type": type(exc).__name__,
            "url_error": str(exc),
            "render": bool(os.getenv("RENDER")),
        }
    host = db_url.host or "local"
    return {
        "initialized": AsyncSessionLocal is not None,
        "driver": db_url.drivername,
        "host": host,
        "port": db_url.port,
        "database": db_url.database or "",
        "render": bool(os.getenv("RENDER")),
        "supabase_pooler": host.endswith(".pooler.supabase.com"),
        "supabase_direct": host.startswith("db.") and host.endswith(".supabase.co"),
    }


def normalize_database_url(value: str) -> URL:
    """Return an async SQLAlchemy database URL.

    Hosted Postgres providers commonly expose a plain postgresql:// URL. Reveal uses
    SQLAlchemy's async engine, so normalize that URL to asyncpg at the boundary.
    """
    url = make_url(value)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")
    if url.drivername == "postgresql+asyncpg":
        url = _normalize_asyncpg_ssl(url)
        url = _normalize_supabase_pooler(url)
    return url


def _normalize_asyncpg_ssl(url: URL) -> URL:
    if "sslmode" in url.query and "ssl" not in url.query:
        sslmode = _query_value_to_string(url.query["sslmode"])
        url = url.difference_update_query(["sslmode"]).update_query_dict({"ssl": sslmode})
    elif "sslmode" in url.query:
        url = url.difference_update_query(["sslmode"])
    elif "ssl" not in url.query:
        url = url.update_query_dict({"ssl": "require"})
    return url


def _query_value_to_string(value: tuple[str, ...] | str) -> str:
    if isinstance(value, tuple):
        return value[-1] if value else ""
    return value


def _normalize_supabase_pooler(url: URL) -> URL:
    if "pgbouncer" in url.query:
        url = url.difference_update_query(["pgbouncer"])
    if (
        url.host
        and url.host.endswith(".pooler.supabase.com")
        and url.port == 6543
        and "prepared_statement_cache_size" not in url.query
    ):
        return url.update_query_dict({"prepared_statement_cache_size": "0"})
    return url


def is_render_supabase_direct_url(url: URL) -> bool:
    return bool(
        os.getenv("RENDER")
        and url.host
        and url.host.startswith("db.")
        and url.host.endswith(".supabase.co")
        and url.port == 5432
    )


async def _migrate_sqlite_twitter_state(conn) -> None:
    """Add columns for Twitter account state created before cursor tracking existed."""
    result = await conn.execute(text("PRAGMA table_info(twitter_state)"))
    existing_columns = {row[1] for row in result.fetchall()}
    if not existing_columns:
        return
    columns = {
        "newest_tweet_id": "VARCHAR(50)",
        "history_cursor": "TEXT",
    }
    for column, column_type in columns.items():
        if column not in existing_columns:
            await conn.execute(text(f"ALTER TABLE twitter_state ADD COLUMN {column} {column_type}"))


async def _migrate_sqlite_social_posts(conn) -> None:
    """Add columns for old SQLite databases created before migrations existed."""
    result = await conn.execute(text("PRAGMA table_info(social_posts)"))
    existing_columns = {row[1] for row in result.fetchall()}
    columns = {
        "tweet_url": "VARCHAR(500)",
        "media": "JSON",
        "links": "JSON",
        "referenced_tweets": "JSON",
        "raw_json": "JSON",
        "is_reply": "BOOLEAN NOT NULL DEFAULT 0",
        "is_repost": "BOOLEAN NOT NULL DEFAULT 0",
        "is_quote": "BOOLEAN NOT NULL DEFAULT 0",
        "mentioned_tickers": "JSON",
        "topics": "JSON",
        "sentiment": "VARCHAR(20)",
        "urgency": "VARCHAR(20)",
        "is_noteworthy": "BOOLEAN NOT NULL DEFAULT 0",
        "attention_reason": "TEXT",
    }
    for column, column_type in columns.items():
        if column not in existing_columns:
            await conn.execute(text(f"ALTER TABLE social_posts ADD COLUMN {column} {column_type}"))


async def _migrate_sqlite_research_sessions(conn) -> None:
    """Add columns for research sessions created before the Agent SDK runtime."""
    result = await conn.execute(text("PRAGMA table_info(research_sessions)"))
    existing_columns = {row[1] for row in result.fetchall()}
    if not existing_columns:
        return
    columns = {
        "agent_runtime": "VARCHAR(50) NOT NULL DEFAULT 'claude_sdk'",
        "agent_session_id": "VARCHAR(100)",
        "source_query": "TEXT",
        "mentioned_tickers": "JSON",
    }
    for column, column_type in columns.items():
        if column not in existing_columns:
            await conn.execute(
                text(f"ALTER TABLE research_sessions ADD COLUMN {column} {column_type}")
            )
