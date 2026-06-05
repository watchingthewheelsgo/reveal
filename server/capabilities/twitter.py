"""Reusable Twitter/X capability implementations."""

import re
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import SocialPost, TwitterState
from server.db.time import to_naive_utc


async def get_twitter_watch_list_payload() -> dict[str, Any]:
    """Return the active watch list plus persisted cursor/check state."""
    from server.social.monitor import list_active_twitter_accounts

    settings = get_settings()
    active_accounts = await list_active_twitter_accounts(settings.twitter_accounts)
    configured = {item.strip().lstrip("@") for item in settings.twitter_accounts}
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(TwitterState).order_by(TwitterState.username.asc()))
        states = {state.username: state for state in result.scalars().all()}

    accounts = []
    for username in active_accounts:
        state = states.get(username)
        accounts.append(
            {
                "username": username,
                "source": "env+db"
                if username in configured and state
                else "env"
                if username in configured
                else "db",
                "active": True,
                "last_tweet_epoch": state.last_tweet_epoch if state else 0,
                "newest_tweet_id": state.newest_tweet_id if state else None,
                "history_cursor": state.history_cursor if state else None,
                "last_check_at": _dt(state.last_check_at) if state else None,
            }
        )

    disabled = [
        {
            "username": state.username,
            "active": False,
            "last_tweet_epoch": state.last_tweet_epoch,
            "newest_tweet_id": state.newest_tweet_id,
            "history_cursor": state.history_cursor,
            "last_check_at": _dt(state.last_check_at),
        }
        for state in states.values()
        if not state.is_active
    ]
    return {"accounts": accounts, "disabled_accounts": disabled, "count": len(accounts)}


async def set_twitter_watch_account_payload(
    username: str,
    is_active: bool,
    backfill_limit: int = 0,
) -> dict[str, Any]:
    """Add or remove an account from the watch list, optionally returning latest posts."""
    normalized = username.strip().lstrip("@")
    if not normalized:
        raise ValueError("username is required")

    from server.social.monitor import set_twitter_account_active

    await set_twitter_account_active(normalized, is_active)
    effective_backfill_limit = (
        _clamp_limit(backfill_limit) if is_active and backfill_limit > 0 else 0
    )
    posts: list[dict[str, Any]] = []
    if effective_backfill_limit:
        payload = await get_twitter_latest_payload(normalized, limit=effective_backfill_limit)
        posts = payload["posts"]
    return {
        "username": normalized,
        "active": is_active,
        "backfill_limit": effective_backfill_limit,
        "posts": posts,
        "message": f"@{normalized} 已{'加入' if is_active else '移出'} Twitter watch list",
    }


async def get_twitter_latest_payload(username: str, limit: int = 5) -> dict[str, Any]:
    """Fetch, cache, and return latest tweets for an account."""
    normalized = username.strip().lstrip("@")
    limit = _clamp_limit(limit)
    from server.social.monitor import cache_user_tweets

    posts = await cache_user_tweets(normalized, count=limit)
    posts = sorted(posts, key=lambda post: post.posted_at, reverse=True)[:limit]
    return {
        "username": normalized,
        "limit": limit,
        "posts": [social_post_payload(post) for post in posts],
    }


async def search_cached_twitter_posts_payload(
    query: str,
    limit: int = 8,
    username: str | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> dict[str, Any]:
    """Search cached Twitter/X posts in Reveal's database."""
    posts = await search_cached_twitter_posts(
        query=query,
        limit=limit,
        username=username,
        start_utc=start_utc,
        end_utc=end_utc,
    )
    return {
        "query": query,
        "username": username.strip().lstrip("@") if username else None,
        "limit": _clamp_limit(limit),
        "posts": [social_post_payload(post) for post in posts],
    }


async def search_cached_twitter_posts(
    query: str,
    limit: int = 8,
    username: str | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> list[SocialPost]:
    """Return cached posts matching a text/ticker query."""
    normalized_username = username.strip().lstrip("@") if username else None
    session_factory = get_session_factory()
    async with session_factory() as session:
        statement = select(SocialPost)
        if normalized_username:
            statement = statement.where(SocialPost.username == normalized_username)
        if start_utc:
            statement = statement.where(SocialPost.posted_at >= to_naive_utc(start_utc))
        if end_utc:
            statement = statement.where(SocialPost.posted_at < to_naive_utc(end_utc))
        result = await session.execute(
            statement.order_by(desc(SocialPost.posted_at), desc(SocialPost.id)).limit(300)
        )
        posts = list(result.scalars().all())

    if query:
        posts = [post for post in posts if post_matches_cached_query(post, query)]
    return posts[: _clamp_limit(limit)]


def social_post_payload(post: SocialPost) -> dict[str, Any]:
    """Serialize a cached SocialPost for Agent/MCP consumption."""
    return {
        "id": post.id,
        "username": post.username,
        "tweet_id": post.tweet_id,
        "tweet_url": post.tweet_url,
        "content": post.content,
        "translated_content": post.translated_content,
        "summary": post.summary,
        "media": post.media or [],
        "links": post.links or [],
        "referenced_tweets": post.referenced_tweets or [],
        "posted_at": _dt(post.posted_at),
        "mentioned_tickers": post.mentioned_tickers or [],
        "topics": post.topics or [],
        "sentiment": post.sentiment,
        "urgency": post.urgency,
        "is_noteworthy": bool(post.is_noteworthy),
        "attention_reason": post.attention_reason,
        "is_reply": bool(post.is_reply),
        "is_repost": bool(post.is_repost),
        "is_quote": bool(post.is_quote),
    }


def post_matches_cached_query(post: SocialPost, query: str) -> bool:
    needle = query.strip().lower()
    ticker = query.strip().upper().lstrip("$")
    haystack = " ".join(
        str(value or "")
        for value in (
            post.username,
            post.content,
            post.summary,
            post.translated_content,
            " ".join(str(item) for item in post.topics or []),
            " ".join(str(item) for item in post.links or []),
        )
    ).lower()
    if needle and needle in haystack:
        return True
    return ticker in {str(item).upper().lstrip("$") for item in post.mentioned_tickers or []}


def format_twitter_watch_list(payload: dict[str, Any]) -> str:
    accounts = payload.get("accounts") or []
    if not accounts:
        return "暂无监控账号。\n用 /x add @用户名 添加。"
    lines = ["*🐦 Twitter 监控列表*", ""]
    for account in accounts:
        last_check = account.get("last_check_at") or "-"
        newest = account.get("newest_tweet_id") or "-"
        lines.append(f"  • @{account['username']} · last_check={last_check} · newest={newest}")
    return "\n".join(lines)


def format_twitter_posts_payload(title: str, posts: list[dict[str, Any]]) -> str:
    if not posts:
        return f"{title}\n\n本地缓存里暂时没有匹配内容。"

    lines = [f"*{title}*", ""]
    for post in posts:
        posted = _format_posted_at(post.get("posted_at"))
        preview = _compact_text(
            str(post.get("summary") or post.get("content") or "（无正文）"), 180
        )
        marker = "重点关注 · " if post.get("is_noteworthy") else ""
        lines.append(f"#{post['id']} {marker}@{post['username']} · {posted}")
        lines.append(preview)
        if post.get("tweet_url"):
            lines.append(str(post["tweet_url"]))
        lines.append("")
    return "\n".join(lines).strip()


def _clamp_limit(limit: int, default: int = 8, max_limit: int = 20) -> int:
    try:
        return max(1, min(max_limit, int(limit)))
    except (TypeError, ValueError):
        return default


def _compact_text(text: str, limit: int) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def _format_posted_at(value: str | None) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
