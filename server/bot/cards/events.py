"""Generic event alert cards."""

from __future__ import annotations

from dataclasses import dataclass

from server.bot.cards._base import card_shell, markdown_elements, note


@dataclass(frozen=True)
class EventCardData:
    title: str
    summary: str
    source: str = ""
    event_id: str = ""
    priority: str = "info"
    sentiment: str = ""
    url: str | None = None
    footer: str = "继续回复本话题即可追问"


def event_alert_card(data: EventCardData) -> dict:
    lines = [f"**{data.title}**"]
    if data.summary:
        lines.extend(["", data.summary])
    meta = _event_meta(data)
    if meta:
        lines.extend(["", meta])
    if data.url:
        lines.extend(["", f"[打开原文]({data.url})"])

    elements = markdown_elements("\n".join(lines))
    elements.append({"tag": "hr"})
    elements.append(note(data.footer))
    return card_shell("Reveal · 市场事件", elements, template=_template(data.priority))


def _event_meta(data: EventCardData) -> str:
    pairs = []
    if data.source:
        pairs.append(f"来源: {data.source}")
    if data.event_id:
        pairs.append(f"事件: {data.event_id}")
    if data.priority:
        pairs.append(f"优先级: {data.priority}")
    if data.sentiment:
        pairs.append(f"情绪: {data.sentiment}")
    return " · ".join(pairs)


def _template(priority: str) -> str:
    normalized = priority.strip().lower()
    if normalized == "critical":
        return "red"
    if normalized == "warning":
        return "orange"
    if normalized == "low":
        return "grey"
    return "blue"
