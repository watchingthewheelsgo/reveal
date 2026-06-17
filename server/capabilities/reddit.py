"""Reusable Reddit/subreddit capability implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import desc, select

from config.settings import get_settings
from server.capabilities.twitter import (
    _clamp_limit,
    _compact_text,
    _dt,
    _format_posted_at,
    social_post_payload,
)
from server.db.engine import get_session_factory
from server.db.models import RedditState, SocialPost
from server.db.time import to_naive_utc
from server.social.reddit import (
    cache_subreddit_posts,
    list_active_reddit_subreddits,
    set_reddit_subreddit_active,
)


async def get_reddit_watch_list_payload() -> dict[str, Any]:
    settings = get_settings()
    active_subreddits = await list_active_reddit_subreddits(settings.reddit_subreddits)
    configured = {item.strip().strip("/").removeprefix("r/") for item in settings.reddit_subreddits}
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(RedditState).order_by(RedditState.subreddit.asc()))
        states = {state.subreddit: state for state in result.scalars().all()}

    subreddits = []
    for subreddit in active_subreddits:
        state = states.get(subreddit)
        subreddits.append(
            {
                "subreddit": subreddit,
                "source": "env+db"
                if subreddit in configured and state
                else "env"
                if subreddit in configured
                else "db",
                "active": True,
                "last_post_epoch": state.last_post_epoch if state else 0,
                "newest_post_id": state.newest_post_id if state else None,
                "last_check_at": _dt(state.last_check_at) if state else None,
            }
        )

    disabled = [
        {
            "subreddit": state.subreddit,
            "active": False,
            "last_post_epoch": state.last_post_epoch,
            "newest_post_id": state.newest_post_id,
            "last_check_at": _dt(state.last_check_at),
        }
        for state in states.values()
        if not state.is_active
    ]
    return {
        "configured": settings.is_reddit_configured(),
        "enabled": settings.reddit_enabled,
        "subreddits": subreddits,
        "disabled_subreddits": disabled,
        "count": len(subreddits),
    }


async def set_reddit_watch_subreddit_payload(
    subreddit: str,
    is_active: bool,
    backfill_limit: int = 0,
) -> dict[str, Any]:
    normalized = _normalize_subreddit(subreddit)
    if not normalized:
        raise ValueError("subreddit is required")

    await set_reddit_subreddit_active(normalized, is_active)
    effective_backfill_limit = (
        _clamp_limit(backfill_limit) if is_active and backfill_limit > 0 else 0
    )
    posts: list[dict[str, Any]] = []
    if effective_backfill_limit:
        payload = await get_reddit_latest_payload(normalized, limit=effective_backfill_limit)
        posts = payload["posts"]
    return {
        "subreddit": normalized,
        "active": is_active,
        "backfill_limit": effective_backfill_limit,
        "posts": posts,
        "message": f"r/{normalized} 已{'加入' if is_active else '移出'} Reddit watch list",
    }


async def get_reddit_latest_payload(subreddit: str, limit: int = 5) -> dict[str, Any]:
    normalized = _normalize_subreddit(subreddit)
    limit = _clamp_limit(limit)
    posts = await cache_subreddit_posts(normalized, count=limit)
    posts = sorted(posts, key=lambda post: post.posted_at, reverse=True)[:limit]
    return {
        "subreddit": normalized,
        "limit": limit,
        "posts": [social_post_payload(post) for post in posts],
    }


async def search_cached_reddit_posts_payload(
    query: str,
    limit: int = 8,
    subreddit: str | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> dict[str, Any]:
    posts = await search_cached_reddit_posts(
        query=query,
        limit=limit,
        subreddit=subreddit,
        start_utc=start_utc,
        end_utc=end_utc,
    )
    return {
        "query": query,
        "subreddit": _normalize_subreddit(subreddit or "") if subreddit else None,
        "limit": _clamp_limit(limit),
        "posts": [social_post_payload(post) for post in posts],
    }


async def search_cached_reddit_posts(
    query: str,
    limit: int = 8,
    subreddit: str | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> list[SocialPost]:
    normalized_subreddit = _normalize_subreddit(subreddit or "") if subreddit else None
    session_factory = get_session_factory()
    async with session_factory() as session:
        statement = select(SocialPost).where(SocialPost.tweet_id.like("reddit:%"))
        if normalized_subreddit:
            statement = statement.where(SocialPost.username == f"r/{normalized_subreddit}")
        if start_utc:
            statement = statement.where(SocialPost.posted_at >= to_naive_utc(start_utc))
        if end_utc:
            statement = statement.where(SocialPost.posted_at < to_naive_utc(end_utc))
        result = await session.execute(
            statement.order_by(desc(SocialPost.posted_at), desc(SocialPost.id)).limit(300)
        )
        posts = list(result.scalars().all())

    if query:
        from server.capabilities.twitter import post_matches_cached_query

        posts = [post for post in posts if post_matches_cached_query(post, query)]
    return posts[: _clamp_limit(limit)]


def format_reddit_watch_list(payload: dict[str, Any]) -> str:
    subreddits = payload.get("subreddits") or []
    if not payload.get("configured"):
        return (
            "Reddit 未配置或未启用。请设置 REDDIT_ENABLED、REDDIT_CLIENT_ID、REDDIT_CLIENT_SECRET。"
        )
    if not subreddits:
        return "暂无监控 subreddit。\n用 /reddit add stocks 添加。"
    lines = ["*Reddit 监控列表*", ""]
    for item in subreddits:
        last_check = item.get("last_check_at") or "-"
        newest = item.get("newest_post_id") or "-"
        lines.append(f"  • r/{item['subreddit']} · last_check={last_check} · newest={newest}")
    return "\n".join(lines)


def format_reddit_posts_payload(title: str, posts: list[dict[str, Any]]) -> str:
    if not posts:
        return f"{title}\n\n本地缓存里暂时没有匹配内容。"

    lines = [f"*{title}*", ""]
    for post in posts:
        posted = _format_posted_at(post.get("posted_at"))
        preview = _compact_text(
            str(post.get("summary") or post.get("content") or "（无正文）"), 180
        )
        marker = "重点关注 · " if post.get("is_noteworthy") else ""
        lines.append(f"#{post['id']} {marker}{post['username']} · {posted}")
        lines.append(preview)
        if post.get("tweet_url"):
            lines.append(str(post["tweet_url"]))
        lines.append("")
    return "\n".join(lines).strip()


def _normalize_subreddit(value: str) -> str:
    normalized = str(value or "").strip().strip("/")
    if normalized.lower().startswith("r/"):
        normalized = normalized[2:]
    return normalized.strip()
