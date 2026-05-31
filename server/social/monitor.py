"""Twitter/X monitor using vxTwitter API with persistence and LLM integration."""

import asyncio
from datetime import UTC, datetime

import httpx
from loguru import logger

from server.bot.base import BotAdapter
from server.db.engine import get_session_factory
from server.db.models import SocialPost, TwitterState


async def list_active_twitter_accounts(configured_accounts: list[str] | None = None) -> list[str]:
    configured_accounts = configured_accounts or []
    session_factory = get_session_factory()
    async with session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(TwitterState))
        states = result.scalars().all()

    disabled = {state.username for state in states if not state.is_active}
    accounts: list[str] = []
    seen: set[str] = set()

    for username in configured_accounts:
        username = username.strip().lstrip("@")
        if username and username not in disabled and username not in seen:
            accounts.append(username)
            seen.add(username)

    for state in states:
        if state.is_active and state.username not in seen:
            accounts.append(state.username)
            seen.add(state.username)

    return accounts


async def set_twitter_account_active(username: str, is_active: bool) -> None:
    username = username.strip().lstrip("@")
    if not username:
        raise ValueError("username is required")

    session_factory = get_session_factory()
    async with session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(TwitterState).where(TwitterState.username == username)
        )
        state = result.scalar_one_or_none()
        if state:
            state.is_active = is_active
        else:
            session.add(TwitterState(username=username, is_active=is_active))
        await session.commit()


async def fetch_user_tweets(username: str) -> dict | None:
    """Fetch latest tweets for a user via vxTwitter API."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.vxtwitter.com/{username}",
                params={
                    "with_tweets": True,
                    "timestamp": int(datetime.now(UTC).timestamp()),
                },
                headers={"User-Agent": "https://github.com/EthanC/Bluebird"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data or "latest_tweets" not in data:
                logger.warning(f"vxTwitter returned invalid data for @{username}")
                return None

            # Sort by time
            data["latest_tweets"] = sorted(
                data["latest_tweets"], key=lambda t: t.get("date_epoch", 0)
            )
            return data
    except Exception as e:
        logger.debug(f"vxTwitter fetch error for @{username}: {e}")
        return None


async def check_and_notify(
    username: str,
    adapter: BotAdapter | None = None,
    llm_processor=None,
):
    """Check for new tweets from a user and push notifications."""
    data = await fetch_user_tweets(username)
    if data is None:
        return []

    tweets = data.get("latest_tweets", [])
    if not tweets:
        return []

    session_factory = get_session_factory()
    posts_to_push = []
    max_seen_epoch = 0
    last_epoch = 0

    async with session_factory() as session:
        from sqlalchemy import select

        # Get last seen epoch
        result = await session.execute(
            select(TwitterState).where(TwitterState.username == username)
        )
        state = result.scalar_one_or_none()

        last_epoch = state.last_tweet_epoch if state else 0
        max_seen_epoch = last_epoch

        for tweet in tweets:
            tweet_epoch = tweet.get("date_epoch", 0)
            tweet_id = tweet.get("id", str(tweet_epoch))

            if tweet_epoch <= last_epoch:
                continue
            max_seen_epoch = max(max_seen_epoch, tweet_epoch)

            # Check if already seen (cross-check by tweet_id)
            existing = await session.execute(
                select(SocialPost).where(SocialPost.tweet_id == tweet_id)
            )
            existing_post = existing.scalar_one_or_none()
            if existing_post:
                if not existing_post.is_pushed:
                    posts_to_push.append(existing_post)
                continue

            content = tweet.get("text", "")
            if not content:
                continue

            # Run LLM processing
            translated = None
            summary = None
            if llm_processor and content:
                try:
                    translated = await llm_processor.translate(content)
                    summary = await llm_processor.summarize(content)
                except Exception as e:
                    logger.warning(f"LLM processing failed for @{username}: {e}")

            post = SocialPost(
                username=username,
                tweet_id=tweet_id,
                content=content,
                translated_content=translated,
                summary=summary,
                posted_at=datetime.fromtimestamp(tweet_epoch, tz=UTC),
                is_pushed=False,
            )
            session.add(post)
            posts_to_push.append(post)

        await session.commit()

    # Push notifications
    successful_tweet_ids: list[str] = []
    push_failed = False
    if adapter and posts_to_push:
        for post in posts_to_push:
            try:
                await push_tweet(post, adapter)
                successful_tweet_ids.append(post.tweet_id)
            except Exception as e:
                push_failed = True
                logger.warning(f"Tweet push failed for @{username}/{post.tweet_id}: {e}")
    elif not adapter:
        successful_tweet_ids = [post.tweet_id for post in posts_to_push]

    async with session_factory() as session:
        from sqlalchemy import select

        if successful_tweet_ids and adapter:
            result = await session.execute(
                select(SocialPost).where(SocialPost.tweet_id.in_(successful_tweet_ids))
            )
            for post in result.scalars().all():
                post.is_pushed = True

        result = await session.execute(
            select(TwitterState).where(TwitterState.username == username)
        )
        state = result.scalar_one_or_none()
        next_epoch = last_epoch if push_failed else max_seen_epoch
        if state:
            state.last_tweet_epoch = max(state.last_tweet_epoch, next_epoch)
            state.last_check_at = datetime.now(UTC)
        else:
            session.add(
                TwitterState(
                    username=username,
                    last_tweet_epoch=next_epoch,
                    last_check_at=datetime.now(UTC),
                )
            )
        await session.commit()

    if posts_to_push:
        logger.info(f"@{username}: {len(successful_tweet_ids)} tweets pushed")

    return posts_to_push


async def push_tweet(post, adapter: BotAdapter):
    """Push a single tweet as a formatted message."""
    lines = [
        f"🐦 *@{post.username}* — {_time_ago(post.posted_at)}",
        "",
        post.content[:500],
    ]
    if post.translated_content:
        lines.append("")
        lines.append(f"🌐 {post.translated_content[:300]}")
    if post.summary:
        lines.append("")
        lines.append(f"📝 摘要: {post.summary[:300]}")

    text = "\n".join(lines)

    try:
        admin_chat_id = getattr(adapter, "admin_chat_id", None)
        if admin_chat_id:
            await adapter.send_message(admin_chat_id, text)
        else:
            await adapter.push_to_admin(text)
    except Exception:
        # Fall back to push_to_admin
        await adapter.push_to_admin(text)


async def run_twitter_monitor(
    usernames: list[str],
    adapter: BotAdapter | None = None,
    llm_processor=None,
):
    """Run one round of Twitter monitoring across all configured users."""
    if not usernames:
        logger.debug("No Twitter accounts configured")
        return

    logger.info(f"Checking {len(usernames)} Twitter accounts...")
    tasks = [check_and_notify(u, adapter, llm_processor) for u in usernames]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total = 0
    for r in results:
        if isinstance(r, list):
            total += len(r)
    logger.info(f"Twitter monitor: {total} new tweets across {len(usernames)} accounts")


def _time_ago(dt: datetime) -> str:
    """Format relative time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    diff = datetime.now(UTC) - dt
    if diff.days > 0:
        return f"{diff.days}天前"
    if diff.seconds >= 3600:
        return f"{diff.seconds // 3600}小时前"
    if diff.seconds >= 60:
        return f"{diff.seconds // 60}分钟前"
    return "刚刚"
