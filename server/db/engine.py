"""Database engine and session management."""

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import global_settings

engine = None
AsyncSessionLocal = None


async def init_db() -> None:
    global engine, AsyncSessionLocal
    from server.db.models import Base

    db_url = global_settings.database_url
    # Ensure data directory exists for SQLite
    if db_url.startswith("sqlite"):
        db_path = make_url(db_url).database
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        db_url,
        echo=global_settings.database_echo,
        future=True,
    )
    AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if db_url.startswith("sqlite"):
            await _migrate_sqlite_social_posts(conn)


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized.")
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
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
        raise RuntimeError("Database not initialized.")
    return AsyncSessionLocal


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
    }
    for column, column_type in columns.items():
        if column not in existing_columns:
            await conn.execute(text(f"ALTER TABLE social_posts ADD COLUMN {column} {column_type}"))
