"""Runtime event DTOs shared by Web, alerts, and research context."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

type EventSourceType = str
type EventPriority = str
type EventSentiment = str
type DeliveryStatus = str
DedupePolicy = Literal["once_per_chat", "repeat_allowed", "replace_previous"]


@dataclass(frozen=True)
class EventItem:
    """Unified event projection used by Web and Agent context."""

    source_type: EventSourceType
    source_id: int | str
    title: str
    summary: str = ""
    body: str = ""
    tickers: tuple[str, ...] = ()
    priority: EventPriority = "info"
    sentiment: EventSentiment = "unknown"
    occurred_at: datetime | None = None
    created_at: datetime | None = None
    url: str | None = None
    raw_ref: str | None = None
    has_research: bool = False
    thread_id: int | None = None
    delivery_status: DeliveryStatus = "none"
    event_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return event_item_id(self.source_type, self.source_id)

    @property
    def stable_key(self) -> str:
        return self.event_key or self.id

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["id"] = self.id
        payload["stable_key"] = self.stable_key
        payload["occurred_at"] = _dt(self.occurred_at)
        payload["created_at"] = _dt(self.created_at)
        return payload


@dataclass(frozen=True)
class AlertCandidate:
    """Candidate alert before delivery, dedupe, and message binding."""

    event_key: str
    event_type: EventSourceType
    title: str
    summary: str
    severity: EventPriority = "info"
    source_id: int | str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    target_chats: tuple[str, ...] = ()
    dedupe_policy: DedupePolicy = "once_per_chat"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def event_item_id(source_type: str, source_id: int | str) -> str:
    return f"{source_type}:{source_id}"


def normalize_tickers(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not values:
        return ()
    normalized = []
    seen = set()
    for value in values:
        ticker = str(value).strip().upper().lstrip("$")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(ticker)
    return tuple(normalized)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
