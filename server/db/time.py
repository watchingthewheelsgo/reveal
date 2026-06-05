"""Datetime helpers for database-compatible UTC values."""

from datetime import UTC, datetime


def utc_now_naive() -> datetime:
    """Return current UTC time as a naive datetime for DB DateTime columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """Normalize aware or naive datetimes to naive UTC for DB storage/queries."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def assume_utc(value: datetime) -> datetime:
    """Treat DB naive datetimes as UTC-aware values for application logic."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
