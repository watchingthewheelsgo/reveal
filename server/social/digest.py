"""Daily Twitter digest generation from cached social posts."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import SocialPost, TwitterState
from server.db.time import to_naive_utc
from server.social.relevance import group_similar_social_posts, is_relevant_social_post

MAX_DIGEST_STORIES = 5
MAX_DIGEST_POSTS_PER_STORY = 3


async def generate_twitter_digest(days_ago: int = 1) -> list[str]:
    """Generate a compact event-centered digest for watched accounts."""
    target_date = _target_date(days_ago)
    active_users = await _active_twitter_users()
    posts = await _posts_for_day(target_date, usernames=active_users or None)
    relevant_posts = _digest_relevant_posts(posts)
    if not relevant_posts:
        return []
    return [_format_event_digest(target_date, relevant_posts)]


async def generate_user_digest(username: str, target_date: date | None = None) -> str | None:
    """Generate a digest for one account on the target day."""
    username = username.strip().lstrip("@")
    target_date = target_date or _target_date(days_ago=1)
    posts = await _posts_for_day(target_date, usernames=[username])
    if not posts:
        return None
    relevant_posts = _digest_relevant_posts(posts)
    if not relevant_posts:
        return f"*Twitter 日报 · @{username} · {target_date.isoformat()}*\n暂无重点市场相关更新。"
    return _format_event_digest(target_date, relevant_posts, username=username)


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


def _digest_relevant_posts(posts: list[SocialPost]) -> list[SocialPost]:
    return [
        post
        for post in posts
        if post.is_pushed
        or post.is_noteworthy
        or post.urgency in {"high", "medium"}
        or is_relevant_social_post(post)
    ]


def _format_event_digest(
    target_date: date,
    posts: list[SocialPost],
    username: str | None = None,
) -> str:
    posts = sorted(posts, key=lambda post: post.posted_at, reverse=True)
    grouped = group_similar_social_posts(posts)
    grouped = sorted(grouped, key=_story_sort_key, reverse=True)
    title = (
        f"*Twitter 日报 · @{username} · {target_date.isoformat()}*"
        if username
        else f"*Twitter 事件日报 · {target_date.isoformat()}*"
    )

    lines = [
        title,
        f"共 {len(posts)} 条重点更新，归并为 {len(grouped)} 个事件。",
    ]
    lines.append("")
    lines.append("*重点事件*")
    for index, group in enumerate(grouped[:MAX_DIGEST_STORIES], start=1):
        lines.extend(_story_digest_lines(index, group))

    if len(grouped) > MAX_DIGEST_STORIES:
        lines.append(f"还有 {len(grouped) - MAX_DIGEST_STORIES} 个事件已缓存，可在看板查看。")
    return "\n".join(lines).strip()


def _story_sort_key(group: list[SocialPost]) -> tuple[int, datetime]:
    priority = 0
    for post in group:
        if post.is_noteworthy or post.urgency == "high":
            priority = max(priority, 3)
        elif post.urgency == "medium":
            priority = max(priority, 2)
        elif post.is_pushed:
            priority = max(priority, 1)
    return priority, max(post.posted_at for post in group)


def _story_digest_lines(index: int, group: list[SocialPost]) -> list[str]:
    ordered = sorted(group, key=lambda post: post.posted_at, reverse=True)
    tickers = _story_tickers(ordered)
    ticker_text = f" · {', '.join(tickers)}" if tickers else ""
    lines = [f"{index}. {_story_title(ordered)}{ticker_text}"]
    lines.append(f"   事实: {_compact(_story_fact(ordered), 180)}")
    lines.append("   观点:")
    for post in ordered[:MAX_DIGEST_POSTS_PER_STORY]:
        lines.append(f"   - @{post.username}: {_compact(_post_viewpoint(post), 120)}")
    if len(ordered) > MAX_DIGEST_POSTS_PER_STORY:
        lines.append(f"   - 另有 {len(ordered) - MAX_DIGEST_POSTS_PER_STORY} 条相关更新。")
    if reference := _story_reference(ordered):
        lines.append(f"   参考: {reference}")
    return lines


def _story_title(posts: list[SocialPost]) -> str:
    topics = []
    for post in posts:
        for topic in post.topics or []:
            value = str(topic).strip()
            if value and value not in topics:
                topics.append(value)
            if len(topics) >= 3:
                break
        if len(topics) >= 3:
            break
    if topics:
        return " · ".join(topics)
    first = posts[0]
    return _compact(first.summary or first.attention_reason or first.content or "未命名事件", 48)


def _story_fact(posts: list[SocialPost]) -> str:
    for post in posts:
        if post.summary:
            return post.summary
    return posts[0].content or "暂无事实摘要"


def _post_viewpoint(post: SocialPost) -> str:
    return post.attention_reason or post.summary or post.content or "暂无观点摘要"


def _story_tickers(posts: list[SocialPost]) -> list[str]:
    tickers: list[str] = []
    for post in posts:
        for ticker in post.mentioned_tickers or []:
            value = str(ticker).upper().strip()
            if value and value not in tickers:
                tickers.append(value)
            if len(tickers) >= 5:
                return tickers
    return tickers


def _story_reference(posts: list[SocialPost]) -> str:
    for post in posts:
        if post.tweet_url:
            return post.tweet_url
    return ""


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
