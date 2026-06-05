"""Daily Twitter digest generation from cached social posts."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import desc, select

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import SocialPost, TwitterState
from server.db.time import to_naive_utc
from server.llm.client import get_llm_client

MAX_DIGEST_ITEMS_PER_USER = 8


async def generate_twitter_digest(days_ago: int = 1) -> list[str]:
    """Generate one digest message per watched account for the target day."""
    target_date = _target_date(days_ago)
    active_users = await _active_twitter_users()
    posts = await _posts_for_day(target_date, usernames=active_users or None)
    if not posts:
        return []

    by_user: dict[str, list[SocialPost]] = {}
    for post in posts:
        by_user.setdefault(post.username, []).append(post)

    messages: list[str] = []
    for username in sorted(by_user):
        messages.append(await _format_user_digest(username, target_date, by_user[username]))
    return messages


async def generate_user_digest(username: str, target_date: date | None = None) -> str | None:
    """Generate a digest for one account on the target day."""
    username = username.strip().lstrip("@")
    target_date = target_date or _target_date(days_ago=1)
    posts = await _posts_for_day(target_date, usernames=[username])
    if not posts:
        return None
    return await _format_user_digest(username, target_date, posts)


async def _active_twitter_users() -> list[str]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(TwitterState.username).where(TwitterState.is_active.is_(True))
        )
        return list(result.scalars().all())


async def _posts_for_day(
    target_date: date,
    usernames: list[str] | None = None,
) -> list[SocialPost]:
    start_utc, end_utc = _day_range_utc(target_date)
    session_factory = get_session_factory()
    async with session_factory() as session:
        statement = select(SocialPost).where(
            SocialPost.posted_at >= start_utc,
            SocialPost.posted_at < end_utc,
        )
        if usernames:
            statement = statement.where(SocialPost.username.in_(usernames))
        result = await session.execute(
            statement.order_by(SocialPost.username.asc(), desc(SocialPost.posted_at))
        )
        return list(result.scalars().all())


async def _format_user_digest(
    username: str,
    target_date: date,
    posts: list[SocialPost],
) -> str:
    posts = sorted(posts, key=lambda post: post.posted_at, reverse=True)
    summary = await _llm_summary(username, target_date, posts)
    noteworthy = [post for post in posts if post.is_noteworthy or post.urgency == "high"]

    lines = [
        f"*Twitter 日报 · @{username} · {target_date.isoformat()}*",
        f"共 {len(posts)} 条更新，重点 {len(noteworthy)} 条。",
    ]
    if summary:
        lines.extend(["", summary])

    lines.append("")
    lines.append("*重点更新*" if noteworthy else "*主要更新*")
    for index, post in enumerate((noteworthy or posts)[:MAX_DIGEST_ITEMS_PER_USER], start=1):
        lines.extend(_post_digest_lines(index, post))

    if len(posts) > MAX_DIGEST_ITEMS_PER_USER:
        lines.append(
            f"还有 {len(posts) - MAX_DIGEST_ITEMS_PER_USER} 条已缓存，可在 Reveal 看板查看。"
        )
    return "\n".join(lines).strip()


async def _llm_summary(username: str, target_date: date, posts: list[SocialPost]) -> str:
    llm = get_llm_client()
    if not llm:
        return ""

    context = "\n\n".join(
        f"- {post.posted_at.strftime('%H:%M')} {post.summary or post.content}"
        for post in posts[:20]
    )
    if not context.strip():
        return ""

    try:
        return await llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是金融信息助手。把某个 Twitter 账号一天内的更新总结成中文日报，"
                        "重点提炼事实、相关 ticker、值得跟进的线索。不要编造。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"账号: @{username}\n日期: {target_date.isoformat()}\n\n{context}",
                },
            ],
            temperature=0.2,
            max_tokens=900,
        )
    except Exception:
        logger.exception("Twitter digest LLM summary failed for @{}", username)
        return ""


def _post_digest_lines(index: int, post: SocialPost) -> list[str]:
    markers = []
    if post.is_noteworthy or post.urgency == "high":
        markers.append("重点")
    if post.mentioned_tickers:
        markers.append(", ".join(str(t) for t in post.mentioned_tickers[:5]))
    marker_text = f" ({' · '.join(markers)})" if markers else ""
    preview = _compact(post.summary or post.content or "（无正文）", 220)
    lines = [f"{index}. #{post.id}{marker_text} {preview}"]
    if post.attention_reason:
        lines.append(f"   原因: {_compact(post.attention_reason, 140)}")
    if post.tweet_url:
        lines.append(f"   {post.tweet_url}")
    return lines


def _target_date(days_ago: int) -> date:
    tz = ZoneInfo(get_settings().twitter_digest_timezone)
    return (datetime.now(tz) - timedelta(days=max(1, days_ago))).date()


def _day_range_utc(target_date: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(get_settings().twitter_digest_timezone)
    start_local = datetime.combine(target_date, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return to_naive_utc(start_local.astimezone(UTC)), to_naive_utc(end_local.astimezone(UTC))


def _compact(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."
