"""Typed runtime event schemas shared across Reveal data sources."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

EventKind = Literal[
    "social",
    "market_price",
    "market_mover",
    "regulatory",
    "news",
    "portfolio",
]
EventSeverity = Literal["info", "low", "medium", "warning", "high", "critical"]
_EVENT_SEVERITIES: set[str] = {"info", "low", "medium", "warning", "high", "critical"}


class EventRef(BaseModel):
    """A source pointer that can be shown to users and used by Agent tools."""

    model_config = ConfigDict(extra="forbid")

    source: str
    external_id: str
    url: str | None = None
    author: str | None = None
    raw: dict[str, Any] | None = None


class Event(BaseModel):
    """Common runtime event fields shared by all source-specific events."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: EventKind
    source: str
    title: str
    summary: str = ""
    occurred_at: datetime | None = None
    severity: EventSeverity = "info"
    tickers: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    refs: list[EventRef] = Field(default_factory=list)
    raw: dict[str, Any] | None = None


class XPostEvent(Event):
    username: str
    tweet_id: str
    tweet_url: str | None = None
    media: list[dict[str, Any]] = Field(default_factory=list)
    referenced_tweets: list[dict[str, Any]] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    sentiment: str | None = None
    urgency: str | None = None
    is_noteworthy: bool = False
    attention_reason: str | None = None
    is_quote: bool = False
    is_reply: bool = False
    is_repost: bool = False


class SECFilingEvent(Event):
    cik: str
    accession: str
    form: str
    company: str | None = None
    filing_date: str | None = None
    report_date: str | None = None
    primary_document: str | None = None


class FDARecallEvent(Event):
    category: str
    recall_number: str
    classification: str = ""
    status: str | None = None
    recalling_firm: str | None = None
    product_description: str | None = None
    reason_for_recall: str | None = None
    matched_keyword: str | None = None


class MarketMoverSignalEvent(Event):
    market: str
    symbol: str
    ticker: str
    event_type: str
    direction: str | None = None
    price: float | None = None
    change_text: str | None = None
    detail: str | None = None


class PriceAlertEvent(Event):
    ticker: str
    current_price: float | None = None
    previous_price: float | None = None
    change_pct: float
    threshold_pct: float | None = None


class VolumeAlertEvent(Event):
    ticker: str
    price: float | None = None
    volume_ratio: float
    threshold_ratio: float | None = None


class NewsEvent(Event):
    ticker: str
    headline: str
    publisher: str | None = None
    published_at: datetime | None = None


COMMON_EVENT_FIELDS = {
    "id",
    "kind",
    "source",
    "title",
    "summary",
    "occurred_at",
    "severity",
    "tickers",
    "links",
    "refs",
    "raw",
}


def compact_event_context(event: Event) -> str:
    """Render an event snapshot for prompts and debugging."""

    lines = [
        f"event_id: {event.id}",
        f"kind: {event.kind}",
        f"source: {event.source}",
        f"severity: {event.severity}",
        f"title: {event.title}",
    ]
    if event.occurred_at:
        lines.append(f"occurred_at: {event.occurred_at.isoformat()}")
    if event.tickers:
        lines.append("tickers: " + ", ".join(event.tickers[:12]))
    if event.summary:
        lines.extend(["summary:", event.summary])
    if event.links:
        lines.append("links: " + ", ".join(event.links[:8]))
    if event.refs:
        lines.append("source_refs:")
        for ref in event.refs[:5]:
            label = ref.author or ref.source
            lines.append(f"- {ref.source}:{ref.external_id} {label} {ref.url or ''}".strip())

    details = event.model_dump(
        mode="json",
        exclude=COMMON_EVENT_FIELDS,
        exclude_none=True,
    )
    if details:
        lines.extend(
            [
                "source_specific_fields:",
                json.dumps(details, ensure_ascii=False, sort_keys=True),
            ]
        )
    return "\n".join(lines)


def normalize_event_severity(value: Any) -> EventSeverity:
    normalized = str(value or "info").strip().lower()
    if normalized in _EVENT_SEVERITIES:
        return cast(EventSeverity, normalized)
    return "info"


# Backward-compatible names while callers migrate from the older dataclass API.
SourceRef = EventRef
SourceEvent = Event
SourceEventKind = EventKind
SourceEventSeverity = EventSeverity
