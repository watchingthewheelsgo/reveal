"""Canonical event primitives shared across Reveal sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

SourceEventKind = Literal[
    "social",
    "market_price",
    "market_mover",
    "regulatory",
    "news",
    "portfolio",
]
SourceEventSeverity = Literal["info", "low", "medium", "high", "critical"]


@dataclass(frozen=True)
class SourceRef:
    """A source pointer that can be shown to users and used by Agent tools."""

    source: str
    external_id: str
    url: str | None = None
    author: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class SourceEvent:
    """Normalized market-intelligence event.

    Keep this layer factual and neutral. Biased interpretation belongs in
    MarketSkill outputs, not in the canonical event itself.
    """

    id: str
    kind: SourceEventKind
    source: str
    title: str
    summary: str
    occurred_at: datetime | None = None
    severity: SourceEventSeverity = "info"
    tickers: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    sentiment: str | None = None
    links: list[str] = field(default_factory=list)
    refs: list[SourceRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def compact_event_context(event: SourceEvent) -> str:
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
    if event.topics:
        lines.append("topics: " + ", ".join(event.topics[:8]))
    if event.sentiment:
        lines.append(f"sentiment: {event.sentiment}")
    if event.summary:
        lines.extend(["summary:", event.summary])
    if event.links:
        lines.append("links: " + ", ".join(event.links[:8]))
    if event.refs:
        lines.append("source_refs:")
        for ref in event.refs[:5]:
            label = ref.author or ref.source
            lines.append(f"- {ref.source}:{ref.external_id} {label} {ref.url or ''}".strip())
    return "\n".join(lines)
