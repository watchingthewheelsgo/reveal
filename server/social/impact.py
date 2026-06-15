"""Personal impact assessment for market-relevant social posts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from server.db.models import SocialPost


@dataclass(frozen=True)
class ImpactAssessment:
    ticker: str
    label: str
    relation: str
    relation_text: str
    event_type: str
    materiality: str
    horizon: str
    direction: str
    confidence: str
    reason: str
    detail: str


def build_personal_impact_lines(post: SocialPost, items: list[dict[str, str]]) -> list[str]:
    assessments = [assess_post_impact(post, item) for item in items]
    return [format_impact_line(assessment) for assessment in assessments]


def assess_post_impact(post: SocialPost, item: dict[str, str]) -> ImpactAssessment:
    ticker = item["ticker"]
    relation = item["relation"]
    label = item["label"]
    event_type = _event_type(post)
    direction = _direction(post)
    reason = _reason(post)
    return ImpactAssessment(
        ticker=ticker,
        label=label,
        relation=relation,
        relation_text=_relation_text(relation),
        event_type=event_type,
        materiality=_materiality(post, relation, event_type),
        horizon=_horizon(post, event_type),
        direction=direction,
        confidence=_confidence(post, ticker),
        reason=reason,
        detail=item.get("detail", ""),
    )


def format_impact_line(assessment: ImpactAssessment) -> str:
    parts = [
        f"- {assessment.ticker}（{assessment.label}）: {assessment.relation_text}",
    ]
    if assessment.detail and assessment.detail != f"{assessment.ticker} ({assessment.label})":
        parts.append(f"；{assessment.detail}")
    parts.append(
        "；"
        f"事件类型: {assessment.event_type}；"
        f"重要性: {assessment.materiality}；"
        f"方向: {assessment.direction}；"
        f"窗口: {assessment.horizon}；"
        f"置信度: {assessment.confidence}"
    )
    if assessment.reason:
        parts.append(f"；{assessment.reason}")
    parts.append("。建议关注后续价格、成交量和消息确认。")
    return "".join(parts)


def _event_type(post: SocialPost) -> str:
    topics = [str(topic).strip() for topic in (post.topics or []) if str(topic).strip()]
    if topics:
        return " / ".join(topics[:2])
    return "市场相关"


def _materiality(post: SocialPost, relation: str, event_type: str) -> str:
    del event_type
    if post.urgency == "high" or (relation == "holding" and post.is_noteworthy):
        return "high"
    if post.urgency == "medium" or relation in {"holding", "tracking", "watchlist"}:
        return "medium"
    return "low"


def _horizon(post: SocialPost, event_type: str) -> str:
    del event_type
    if post.urgency == "high":
        return "短线"
    return "观察"


def _direction(post: SocialPost) -> str:
    return {
        "bullish": "看多",
        "bearish": "看空",
        "mixed": "分歧",
        "neutral": "中性",
    }.get(post.sentiment or "", "待确认")


def _confidence(post: SocialPost, ticker: str) -> str:
    mentioned = {_normalize_ticker(t) for t in post.mentioned_tickers or []}
    if ticker in mentioned and post.attention_reason:
        return "high"
    if ticker in mentioned:
        return "medium"
    return "low"


def _reason(post: SocialPost) -> str:
    basis = (
        post.attention_reason
        or _stored_urgency_reason(post)
        or post.summary
        or post.translated_content
        or post.content
        or ""
    )
    return _trim_text(str(basis), 140).rstrip("。.")


def _relation_text(relation: str) -> str:
    if relation == "holding":
        return "与你的持仓直接相关"
    if relation == "tracking":
        return "与你的追踪标的相关"
    return "与你的观察列表相关"


def _stored_urgency_reason(post: SocialPost) -> str:
    raw = post.raw_json or {}
    analysis = raw.get("reveal_analysis") if isinstance(raw, dict) else None
    if not isinstance(analysis, dict):
        return ""
    return str(analysis.get("urgency_reason") or "")


def _normalize_ticker(value: object) -> str:
    ticker = str(value or "").strip().upper().lstrip("$")
    return ticker if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker) else ""


def _trim_text(text: str, limit: int) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."
