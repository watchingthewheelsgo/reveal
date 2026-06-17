"""Adapters from persisted social data to typed runtime events."""

from __future__ import annotations

from server.db.models import SocialPost
from server.events.types import Event, EventRef, EventSeverity, RedditPostEvent, XPostEvent
from server.social.relevance import agent_market_relevance


def event_from_social_post(post: SocialPost) -> Event:
    if str(post.tweet_id or "").startswith("reddit:"):
        return event_from_reddit_post(post)

    refs = [
        EventRef(
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
            EventRef(
                source="x",
                external_id=external_id,
                url=reference.get("url"),
                author=f"@{reference.get('username')}" if reference.get("username") else None,
                raw=reference if isinstance(reference, dict) else None,
            )
        )

    return XPostEvent(
        id=f"x:{post.tweet_id}",
        kind="social",
        source="x",
        title=_social_event_title(post),
        summary=post.summary or post.content or "",
        occurred_at=post.posted_at,
        severity=_social_event_severity(post),
        tickers=[str(ticker).upper() for ticker in (post.mentioned_tickers or [])],
        links=[str(link) for link in (post.links or [])],
        refs=refs,
        raw=post.raw_json,
        username=post.username,
        tweet_id=post.tweet_id,
        tweet_url=post.tweet_url,
        media=post.media or [],
        referenced_tweets=post.referenced_tweets or [],
        topics=[str(topic) for topic in (post.topics or [])],
        sentiment=post.sentiment,
        urgency=post.urgency,
        is_market_relevant=agent_market_relevance(post),
        is_noteworthy=bool(post.is_noteworthy),
        attention_reason=post.attention_reason,
        is_quote=bool(post.is_quote),
        is_reply=bool(post.is_reply),
        is_repost=bool(post.is_repost),
    )


def event_from_reddit_post(post: SocialPost) -> RedditPostEvent:
    raw = post.raw_json if isinstance(post.raw_json, dict) else {}
    subreddit = str(raw.get("subreddit") or post.username.removeprefix("r/") or "")
    reddit_id = str(raw.get("reddit_id") or post.tweet_id.rsplit(":", 1)[-1])
    permalink = str(raw.get("permalink") or post.tweet_url or "")
    return RedditPostEvent(
        id=f"reddit:{subreddit}:{reddit_id}",
        kind="social",
        source="reddit",
        title=_social_event_title(post),
        summary=post.summary or post.content or "",
        occurred_at=post.posted_at,
        severity=_social_event_severity(post),
        tickers=[str(ticker).upper() for ticker in (post.mentioned_tickers or [])],
        links=[str(link) for link in (post.links or [])],
        refs=[
            EventRef(
                source="reddit",
                external_id=reddit_id,
                url=permalink or post.tweet_url,
                author=post.username,
                raw=raw,
            )
        ],
        raw=raw,
        subreddit=subreddit,
        reddit_id=reddit_id,
        permalink=permalink,
        author=str(raw.get("author") or "") or None,
        score=_safe_int(raw.get("score")),
        upvote_ratio=_safe_float(raw.get("upvote_ratio")),
        num_comments=_safe_int(raw.get("num_comments")),
        flair=str(raw.get("flair") or "") or None,
        topics=[str(topic) for topic in (post.topics or [])],
        sentiment=post.sentiment,
        urgency=post.urgency,
        is_market_relevant=agent_market_relevance(post),
        is_noteworthy=bool(post.is_noteworthy),
        attention_reason=post.attention_reason,
    )


def _social_event_title(post: SocialPost) -> str:
    if post.summary:
        return post.summary[:120]
    content = " ".join((post.content or "").split())
    return content[:120] if content else f"@{post.username} X update"


def _social_event_severity(post: SocialPost) -> EventSeverity:
    if post.is_noteworthy or post.urgency == "high":
        return "high"
    if post.urgency == "medium":
        return "medium"
    if post.urgency == "low":
        return "low"
    return "info"


source_event_from_social_post = event_from_social_post


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
