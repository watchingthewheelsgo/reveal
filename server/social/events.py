"""Adapters from persisted social data to canonical SourceEvent."""

from __future__ import annotations

from server.db.models import SocialPost
from server.events.types import SourceEvent, SourceEventSeverity, SourceRef


def source_event_from_social_post(post: SocialPost) -> SourceEvent:
    refs = [
        SourceRef(
            source="x",
            external_id=post.tweet_id,
            url=post.tweet_url,
            author=f"@{post.username}",
            raw=post.raw_json,
        )
    ]
    for reference in post.referenced_tweets or []:
        external_id = str(reference.get("tweet_id") or reference.get("url") or "")
        if not external_id:
            continue
        refs.append(
            SourceRef(
                source="x",
                external_id=external_id,
                url=reference.get("url"),
                author=f"@{reference.get('username')}" if reference.get("username") else None,
                raw=reference if isinstance(reference, dict) else None,
            )
        )

    return SourceEvent(
        id=f"x:{post.tweet_id}",
        kind="social",
        source="x",
        title=_social_event_title(post),
        summary=post.summary or post.content or "",
        occurred_at=post.posted_at,
        severity=_social_event_severity(post),
        tickers=[str(ticker).upper() for ticker in (post.mentioned_tickers or [])],
        topics=[str(topic) for topic in (post.topics or [])],
        sentiment=post.sentiment,
        links=[str(link) for link in (post.links or [])],
        refs=refs,
        metadata={
            "username": post.username,
            "is_quote": bool(post.is_quote),
            "is_repost": bool(post.is_repost),
            "is_reply": bool(post.is_reply),
            "urgency": post.urgency,
            "is_noteworthy": bool(post.is_noteworthy),
            "attention_reason": post.attention_reason,
        },
    )


def _social_event_title(post: SocialPost) -> str:
    if post.summary:
        return post.summary[:120]
    content = " ".join((post.content or "").split())
    return content[:120] if content else f"@{post.username} X update"


def _social_event_severity(post: SocialPost) -> SourceEventSeverity:
    if post.is_noteworthy or post.urgency == "high":
        return "high"
    if post.urgency == "medium":
        return "medium"
    if post.urgency == "low":
        return "low"
    return "info"
