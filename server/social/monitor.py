"""Twitter/X monitor using vxTwitter API with persistence and LLM integration."""

import asyncio
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from server.bot.base import BotAdapter
from server.db.engine import get_session_factory
from server.db.models import SocialPost, TwitterState
from server.db.time import assume_utc, to_naive_utc, utc_now_naive
from server.social.relevance import (
    agent_market_relevance,
    group_similar_social_posts,
    is_relevant_social_post,
    is_x_url,
)
from server.social.twitter_graphql import fetch_user_tweets_graphql
from server.social.urls import normalize_x_url

TWITTER_STATUS_URL_RE = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/([^/\s]+)/status/(\d+)")
URL_RE = re.compile(r"https?://[^\s<>)\]]+")
MAX_PUSHED_MEDIA = 4
INITIAL_WATCH_BACKFILL_LIMIT = 10
MAX_CARD_IMAGES = 4
MAX_CARD_REFERENCES = 3
CARD_SUMMARY_LIMIT = 360
CARD_BODY_LIMIT = 900
CARD_BODY_WITH_SUMMARY_LIMIT = 420
CARD_TRANSLATION_LIMIT = 450
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


async def list_active_twitter_accounts(configured_accounts: list[str] | None = None) -> list[str]:
    configured_accounts = configured_accounts or []
    session_factory = get_session_factory()
    async with session_factory() as session:
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
        result = await session.execute(
            select(TwitterState).where(TwitterState.username == username)
        )
        state = result.scalar_one_or_none()
        if state:
            state.is_active = is_active
        else:
            session.add(TwitterState(username=username, is_active=is_active))
        await session.commit()


async def fetch_user_tweets(
    username: str,
    count: int = 20,
    cursor: str | None = None,
) -> dict | None:
    """Fetch a user timeline page, preferring direct X GraphQL when configured."""
    settings = get_settings()
    if settings.twitter_auth_tokens:
        data = await fetch_user_tweets_graphql(
            username,
            settings.twitter_auth_tokens,
            count=count,
            cursor=cursor,
        )
        if data is not None and "latest_tweets" in data:
            return _normalize_timeline_data(data, username)
        logger.debug(f"X GraphQL timeline fetch failed for @{username}; falling back to vxTwitter")

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
                logger.error("vxTwitter returned invalid data for @{}", username)
                return None

            data["source"] = "vxtwitter"
            data = _normalize_timeline_data(data, username)

            if cache_control := resp.headers.get("cache-control"):
                if max_age := _parse_cache_max_age(cache_control):
                    data["max_age"] = max_age

            return data
    except Exception:
        logger.exception("vxTwitter timeline fetch failed for @{}", username)
        return None


async def fetch_tweet(username: str, tweet_id: str) -> dict | None:
    """Fetch one tweet by user and ID via vxTwitter API."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.vxtwitter.com/{username}/status/{tweet_id}",
                headers={"User-Agent": "https://github.com/EthanC/Bluebird"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.exception("vxTwitter tweet fetch failed for @{}/{}", username, tweet_id)
        return None

    if not data:
        return None
    _enrich_tweet(data, data.get("screen_name") or username, data)
    return data


async def _upsert_social_post(
    session: AsyncSession,
    display_username: str,
    tweet: dict[str, Any],
    user_data: dict[str, Any],
    llm_processor=None,
    should_analyze: bool = False,
    hydrate_references: bool = True,
) -> SocialPost | None:
    _enrich_tweet(tweet, display_username, user_data)
    tweet_id = _tweet_id(tweet)
    tweet_epoch = _tweet_epoch(tweet)
    if not tweet_id or tweet_epoch <= 0:
        logger.warning(f"@{display_username}: skipped tweet with incomplete cache key: {tweet}")
        return None

    existing = await session.execute(select(SocialPost).where(SocialPost.tweet_id == tweet_id))
    post = existing.scalar_one_or_none()

    content = tweet.get("text") or ""
    tweet_url = _tweet_url(display_username, tweet)
    media = _tweet_media(tweet)
    references = _referenced_tweets(display_username, tweet)
    if hydrate_references:
        references = await _hydrate_references(session, references)
    links = _tweet_links(tweet, tweet_url, media, references)

    translated = post.translated_content if post else None
    summary = post.summary if post else None
    post_tickers = post.mentioned_tickers if post else None
    post_topics = post.topics if post else None
    post_sentiment = post.sentiment if post else None
    post_urgency = post.urgency if post else None
    post_is_noteworthy = bool(post.is_noteworthy) if post else False
    post_attention_reason = post.attention_reason if post else None

    llm_context = _llm_context(content, tweet_url, links, references)
    if should_analyze and llm_processor and llm_context and not summary:
        try:
            analysis = await llm_processor.analyze(llm_context, display_username)
            if analysis:
                tweet["reveal_analysis"] = {
                    "summary": analysis.summary,
                    "mentioned_tickers": analysis.mentioned_tickers,
                    "topics": analysis.topics,
                    "sentiment": analysis.sentiment,
                    "urgency": analysis.urgency,
                    "urgency_reason": analysis.urgency_reason,
                    "is_market_relevant": analysis.is_market_relevant,
                    "is_noteworthy": analysis.is_noteworthy,
                    "attention_reason": analysis.attention_reason,
                }
                summary = analysis.summary
                translated = analysis.translation
                post_tickers = analysis.mentioned_tickers or None
                post_topics = analysis.topics or None
                post_sentiment = analysis.sentiment
                post_urgency = analysis.urgency
                post_is_noteworthy = bool(analysis.is_noteworthy)
                post_attention_reason = analysis.attention_reason or analysis.urgency_reason or None
        except Exception:
            logger.exception("LLM processing failed for @{}", display_username)

    fields = {
        "username": display_username,
        "tweet_url": tweet_url,
        "content": content,
        "translated_content": translated,
        "summary": summary,
        "media": media,
        "links": links,
        "referenced_tweets": references,
        "raw_json": tweet,
        "is_reply": bool(tweet.get("is_reply")),
        "is_repost": bool(tweet.get("is_repost")),
        "is_quote": bool(tweet.get("is_quote")),
        "mentioned_tickers": post_tickers,
        "topics": post_topics,
        "sentiment": post_sentiment,
        "urgency": post_urgency,
        "is_noteworthy": post_is_noteworthy,
        "attention_reason": post_attention_reason,
        "posted_at": to_naive_utc(datetime.fromtimestamp(tweet_epoch, tz=UTC)),
    }

    if post:
        for key, value in fields.items():
            setattr(post, key, value)
        return post

    post = SocialPost(
        tweet_id=tweet_id,
        is_pushed=False,
        **fields,
    )
    session.add(post)
    return post


async def check_and_notify(
    username: str,
    adapter: BotAdapter | None = None,
    llm_processor=None,
    notify_no_updates: bool = False,
    push_notifications: bool = True,
):
    """Check for new tweets from a user and push notifications."""
    account_key = username.strip().lstrip("@")
    session_factory = get_session_factory()
    last_epoch = 0
    first_check = False

    async with session_factory() as session:
        result = await session.execute(
            select(TwitterState).where(TwitterState.username == account_key)
        )
        state = result.scalar_one_or_none()
        last_epoch = _epoch_or_zero(state.last_tweet_epoch if state else None)
        first_check = last_epoch <= 0
        last_check_at = state.last_check_at if state else None

    check_age = _seconds_since(last_check_at)
    min_interval = get_settings().twitter_fetch_min_interval
    if check_age is not None and check_age < min_interval:
        logger.info(
            "@{}: skipped Twitter fetch due to cooldown age={:.0f}s min_interval={}s",
            account_key,
            check_age,
            min_interval,
        )
        if adapter and notify_no_updates:
            await adapter.push_to_admin(
                f"@{account_key} {int(check_age)} 秒前刚检查过，已跳过拉取以避免触发限流。"
            )
        return []

    fetch_count = INITIAL_WATCH_BACKFILL_LIMIT if first_check else 20
    data = await fetch_user_tweets(username, count=fetch_count)
    if data is None:
        if adapter and notify_no_updates:
            await adapter.push_to_admin(f"@{account_key} 检查失败，暂时无法获取更新。")
        return []

    tweets = data.get("latest_tweets", [])
    display_username = data.get("screen_name") or account_key
    posts_to_push: list[SocialPost] = []
    max_cached_epoch = last_epoch
    newest_cached_tweet_id: str | None = None
    push_candidate_ids = (
        {_tweet_id(tweet) for tweet in tweets[-INITIAL_WATCH_BACKFILL_LIMIT:]}
        if first_check
        else None
    )

    async with session_factory() as session:
        result = await session.execute(
            select(TwitterState).where(TwitterState.username == account_key)
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = TwitterState(username=account_key)
            session.add(state)

        if first_check:
            logger.info(
                f"@{account_key}: first watch check; backfilling up to "
                f"{INITIAL_WATCH_BACKFILL_LIMIT} tweets"
            )

        for tweet in tweets:
            tweet_id = _tweet_id(tweet)
            if not tweet_id:
                logger.warning(f"@{account_key}: skipped tweet with missing id: {tweet}")
                continue

            tweet_epoch = _tweet_epoch(tweet)
            if tweet_epoch > max_cached_epoch:
                max_cached_epoch = tweet_epoch
                newest_cached_tweet_id = tweet_id

            push_candidate = tweet_epoch > last_epoch and (
                push_candidate_ids is None or tweet_id in push_candidate_ids
            )
            should_analyze = bool(push_candidate and llm_processor)
            post = await _upsert_social_post(
                session,
                display_username,
                tweet,
                data,
                llm_processor=llm_processor,
                should_analyze=should_analyze,
            )
            if post is None:
                continue

            if push_candidate and not post.is_pushed:
                posts_to_push.append(post)

        if newest_cached_tweet_id:
            state.newest_tweet_id = newest_cached_tweet_id
        if history_cursor := data.get("history_cursor"):
            state.history_cursor = str(history_cursor)
        state.last_check_at = utc_now_naive()

        await session.commit()

    # Push notifications
    pushed_tweet_ids: list[str] = []
    push_failed = False
    if adapter and posts_to_push and push_notifications:
        try:
            pushed_posts = await push_tweet_cards(posts_to_push, adapter, is_backfill=first_check)
            pushed_tweet_ids = [post.tweet_id for post in pushed_posts]
            push_failed = len(pushed_tweet_ids) != len(posts_to_push)
        except Exception:
            push_failed = True
            logger.exception("Tweet card push failed for @{}", account_key)

    async with session_factory() as session:
        if pushed_tweet_ids:
            result = await session.execute(
                select(SocialPost).where(SocialPost.tweet_id.in_(pushed_tweet_ids))
            )
            for post in result.scalars().all():
                post.is_pushed = True

        result = await session.execute(
            select(TwitterState).where(TwitterState.username == account_key)
        )
        state = result.scalar_one_or_none()
        next_epoch = last_epoch if push_failed else max_cached_epoch
        if state:
            state.last_tweet_epoch = max(_epoch_or_zero(state.last_tweet_epoch), next_epoch)
            state.last_check_at = utc_now_naive()
            if newest_cached_tweet_id:
                state.newest_tweet_id = newest_cached_tweet_id
        else:
            session.add(
                TwitterState(
                    username=account_key,
                    last_tweet_epoch=next_epoch,
                    newest_tweet_id=newest_cached_tweet_id,
                    last_check_at=utc_now_naive(),
                )
            )
        await session.commit()

    if posts_to_push:
        if push_notifications:
            logger.info(f"@{account_key}: {len(pushed_tweet_ids)} tweets notified")
        else:
            logger.info(f"@{account_key}: {len(posts_to_push)} tweet candidates collected")

    return posts_to_push


async def cache_user_tweets(
    username: str,
    count: int = 10,
    cursor: str | None = None,
    llm_processor=None,
    force_fetch: bool = False,
) -> list[SocialPost]:
    """Fetch a timeline page and persist every returned tweet without pushing cards."""
    account_key = username.strip().lstrip("@")
    try:
        session_factory = get_session_factory()
    except Exception as exc:
        from server.db.engine import database_diagnostic_context

        logger.exception(
            "Twitter cache database unavailable before fetch: username={} count={} cursor={} "
            "db_context={} exc_type={} error={}",
            account_key,
            count,
            bool(cursor),
            database_diagnostic_context(),
            type(exc).__name__,
            exc,
        )
        raise
    state, cached_posts = await _cached_user_tweets(session_factory, account_key, count)
    check_age = _seconds_since(state.last_check_at if state else None)
    min_interval = get_settings().twitter_fetch_min_interval
    if not force_fetch and check_age is not None and check_age < min_interval:
        logger.info(
            "@{}: returning {} cached tweets due to cooldown age={:.0f}s min_interval={}s",
            account_key,
            len(cached_posts),
            check_age,
            min_interval,
        )
        return cached_posts

    data = await fetch_user_tweets(account_key, count=count, cursor=cursor)
    if data is None:
        return cached_posts

    tweets = data.get("latest_tweets", [])
    display_username = data.get("screen_name") or account_key
    cached_posts: list[SocialPost] = []
    newest_cached_epoch = 0
    newest_cached_tweet_id: str | None = None

    try:
        async with session_factory() as session:
            for tweet in tweets:
                tweet_epoch = _tweet_epoch(tweet)
                tweet_id = _tweet_id(tweet)
                if tweet_epoch > newest_cached_epoch:
                    newest_cached_epoch = tweet_epoch
                    newest_cached_tweet_id = tweet_id
                post = await _upsert_social_post(
                    session,
                    display_username,
                    tweet,
                    data,
                    llm_processor=llm_processor,
                    should_analyze=bool(llm_processor),
                )
                if post is not None:
                    cached_posts.append(post)

            result = await session.execute(
                select(TwitterState).where(TwitterState.username == account_key)
            )
            state = result.scalar_one_or_none()
            if state is None:
                state = TwitterState(username=account_key, is_active=False)
                session.add(state)
            if newest_cached_epoch:
                state.last_tweet_epoch = max(
                    _epoch_or_zero(state.last_tweet_epoch), newest_cached_epoch
                )
            if newest_cached_tweet_id:
                state.newest_tweet_id = newest_cached_tweet_id
            if history_cursor := data.get("history_cursor"):
                state.history_cursor = str(history_cursor)
            state.last_check_at = utc_now_naive()
            await session.commit()
    except Exception as exc:
        from server.db.engine import database_diagnostic_context

        logger.exception(
            "Twitter cache database write failed: username={} fetched={} cached_before_error={} "
            "newest_tweet_id={} db_context={} exc_type={} error={}",
            account_key,
            len(tweets),
            len(cached_posts),
            newest_cached_tweet_id,
            database_diagnostic_context(),
            type(exc).__name__,
            exc,
        )
        raise

    return cached_posts


async def _cached_user_tweets(
    session_factory,
    username: str,
    limit: int,
) -> tuple[TwitterState | None, list[SocialPost]]:
    async with session_factory() as session:
        state_result = await session.execute(
            select(TwitterState).where(TwitterState.username == username)
        )
        state = state_result.scalar_one_or_none()
        posts_result = await session.execute(
            select(SocialPost)
            .where(SocialPost.username == username)
            .order_by(desc(SocialPost.posted_at), desc(SocialPost.id))
            .limit(limit)
        )
        posts = list(posts_result.scalars().all())
    return state, posts


async def push_tweet_cards(
    posts: list[SocialPost],
    adapter: BotAdapter,
    is_backfill: bool = False,
) -> list[SocialPost]:
    """Push stored tweets as separate fixed-template cards."""
    if not posts:
        return []

    cross_ref = await _cross_reference_posts(posts) if not is_backfill else {}
    ordered_posts = sorted(posts, key=lambda post: post.posted_at)
    pushed_posts: list[SocialPost] = []
    for post in ordered_posts:
        try:
            await push_tweet_card(
                post,
                adapter,
                cross_ref=cross_ref.get(post.id),
                is_backfill=is_backfill,
            )
            pushed_posts.append(post)
        except Exception:
            logger.exception("Tweet card push failed for #{}", post.id or post.tweet_id)
    return pushed_posts


async def push_relevant_tweet_cards(
    posts: list[SocialPost],
    adapter: BotAdapter,
) -> list[SocialPost]:
    """Filter social updates and push only market-relevant cards, grouped by story."""
    if not posts:
        return []

    relevant_posts = [post for post in posts if is_relevant_social_post(post)]
    skipped = len(posts) - len(relevant_posts)
    if skipped:
        logger.info(
            "Twitter relevance filter skipped {} of {} candidates",
            skipped,
            len(posts),
        )
    if not relevant_posts:
        return []

    cross_ref = await _cross_reference_posts(relevant_posts)
    grouped_posts = group_similar_social_posts(relevant_posts)
    pushed_posts: list[SocialPost] = []
    for group in grouped_posts:
        try:
            await push_tweet_topic_card(group, adapter, cross_ref=cross_ref)
            pushed_posts.extend(group)
        except Exception:
            logger.exception(
                "Tweet grouped push failed for posts={}",
                [post.id or post.tweet_id for post in group],
            )
    return pushed_posts


async def push_tweet_topic_card(
    posts: list[SocialPost],
    adapter: BotAdapter,
    cross_ref: dict[int, dict] | None = None,
) -> None:
    """Push a single story-centered card for multiple related tweets."""
    ordered_posts = sorted(posts, key=lambda post: post.posted_at)
    card = _build_tweet_topic_card(ordered_posts, cross_ref=cross_ref or {})
    sent_messages = await _push_card_to_admin(adapter, card)
    await _bind_sent_messages(sent_messages, ordered_posts[-1].id)


async def mark_tweet_posts_pushed(posts: list[SocialPost]) -> None:
    tweet_ids = [post.tweet_id for post in posts if post.tweet_id]
    if not tweet_ids:
        return

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(SocialPost).where(SocialPost.tweet_id.in_(tweet_ids)))
        for post in result.scalars().all():
            post.is_pushed = True
        await session.commit()


async def push_tweet_digest(
    posts: list[SocialPost],
    adapter: BotAdapter,
    is_backfill: bool = False,
) -> None:
    """Compatibility wrapper: tweets are now pushed as separate cards."""
    await push_tweet_cards(posts, adapter, is_backfill=is_backfill)


async def push_tweet(post, adapter: BotAdapter):
    """Push a single tweet as a fixed-template card."""
    await push_tweet_card(post, adapter)


async def push_tweet_card(
    post: SocialPost,
    adapter: BotAdapter,
    cross_ref: dict | None = None,
    is_backfill: bool = False,
) -> None:
    """Push one tweet as a rich card and bind the sent message to the post."""
    card = await _build_tweet_card(post, adapter, cross_ref=cross_ref, is_backfill=is_backfill)
    sent_messages = await _push_card_to_admin(adapter, card)
    await _bind_sent_messages(sent_messages, post.id)


async def run_twitter_monitor(
    usernames: list[str],
    adapter: BotAdapter | None = None,
    llm_processor=None,
    notify_no_updates: bool = False,
) -> int:
    """Run one round of Twitter monitoring across all configured users."""
    if not usernames:
        logger.debug("No Twitter accounts configured")
        return 0

    logger.info(f"Checking {len(usernames)} Twitter accounts...")
    tasks = [
        check_and_notify(
            u,
            adapter,
            llm_processor,
            notify_no_updates=notify_no_updates,
            push_notifications=False,
        )
        for u in usernames
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    candidates: list[SocialPost] = []
    for username, r in zip(usernames, results, strict=False):
        if isinstance(r, Exception):
            logger.opt(exception=r).error(
                "Twitter monitor account check failed: username={} exc_type={} error={}",
                username,
                type(r).__name__,
                r,
            )
            continue
        if isinstance(r, list):
            candidates.extend(r)

    pushed_posts: list[SocialPost] = []
    if adapter and candidates:
        pushed_posts = await push_relevant_tweet_cards(candidates, adapter)
        await mark_tweet_posts_pushed(pushed_posts)

    total = len(pushed_posts) if adapter else len(candidates)
    logger.info(
        "Twitter monitor: {} pushed from {} new candidates across {} accounts",
        total,
        len(candidates),
        len(usernames),
    )
    return total


def _format_beijing_time(dt: datetime) -> str:
    """Format a stored UTC timestamp for human-readable notification cards."""
    local_dt = assume_utc(dt).astimezone(BEIJING_TZ)
    return f"北京时间 {local_dt:%Y-%m-%d %H:%M}"


def _seconds_since(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return (datetime.now(UTC) - assume_utc(dt)).total_seconds()


def _epoch_or_zero(value: int | None) -> int:
    return value or 0


def _normalize_timeline_data(data: dict[str, Any], username: str) -> dict[str, Any]:
    tweets = data.get("latest_tweets")
    if not isinstance(tweets, list):
        data["latest_tweets"] = []
        return data

    data["latest_tweets"] = sorted(tweets, key=_tweet_epoch)
    screen_name = data.get("screen_name") or username
    for tweet in data["latest_tweets"]:
        if isinstance(tweet, dict):
            _enrich_tweet(tweet, screen_name, data)
    return data


def _enrich_tweet(tweet: dict[str, Any], username: str, user_data: dict[str, Any]) -> None:
    tweet["user_bio"] = user_data.get("description")
    tweet["is_repost"] = bool(tweet.get("retweetURL") or tweet.get("retweet"))
    tweet["is_quote"] = bool(tweet.get("qrtURL"))
    tweet["is_reply"] = bool(tweet.get("replyingToID") or tweet.get("replyingTo"))
    tweet_url = _tweet_url(username, tweet)
    if tweet_url:
        tweet["tweetURL"] = tweet_url


def _tweet_id(tweet: dict[str, Any]) -> str:
    value = (
        tweet.get("tweetID")
        or tweet.get("id")
        or tweet.get("tweet_id")
        or tweet.get("conversationID")
    )
    return str(value) if value else ""


def _tweet_epoch(tweet: dict[str, Any]) -> int:
    try:
        return int(tweet.get("date_epoch") or tweet.get("created_at_epoch") or 0)
    except (TypeError, ValueError):
        return 0


def _tweet_url(username: str, tweet: dict[str, Any]) -> str | None:
    raw_url = tweet.get("tweetURL") or tweet.get("url")
    if raw_url:
        return normalize_x_url(str(raw_url))
    tweet_id = _tweet_id(tweet)
    if tweet_id:
        return f"https://x.com/{username}/status/{tweet_id}"
    return None


def _tweet_media(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    media_raw = tweet.get("media_extended") or tweet.get("media") or tweet.get("mediaURLs") or []
    if not isinstance(media_raw, list):
        return []

    media: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in media_raw:
        preview_url = None
        video_url = None
        if isinstance(item, str):
            url = item
            media_type = None
            alt_text = None
        elif isinstance(item, dict):
            video_url = _best_video_url(item)
            preview_url = (
                item.get("preview_image_url")
                or item.get("thumbnail_url")
                or item.get("thumb")
                or item.get("poster")
            )
            url = (
                item.get("url")
                or item.get("media_url_https")
                or preview_url
                or video_url
                or item.get("src")
            )
            media_type = item.get("type") or item.get("format")
            if not media_type and video_url:
                media_type = "video"
            elif not media_type and str(url).lower().split("?", maxsplit=1)[0].endswith(".mp4"):
                media_type = "video"
            alt_text = item.get("altText") or item.get("alt_text")
        else:
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        media_item = {
            "url": str(url),
            "type": media_type or "image",
            "alt_text": alt_text,
        }
        if isinstance(item, dict):
            if preview_url and preview_url != url:
                media_item["preview_url"] = str(preview_url)
            if video_url and video_url != url:
                media_item["video_url"] = str(video_url)
        media.append(media_item)
    return media


def _best_video_url(item: dict[str, Any]) -> str | None:
    for key in ("video_url", "videoUrl", "source", "source_url", "mp4"):
        if value := item.get(key):
            return str(value)

    variants = item.get("variants") or item.get("video_info", {}).get("variants")
    if not isinstance(variants, list):
        return None

    candidates: list[tuple[int, str]] = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        url = variant.get("url")
        if not url:
            continue
        content_type = str(variant.get("content_type") or variant.get("type") or "")
        if content_type and "mp4" not in content_type:
            continue
        bitrate = variant.get("bitrate") or variant.get("bit_rate") or 0
        try:
            score = int(bitrate)
        except (TypeError, ValueError):
            score = 0
        candidates.append((score, str(url)))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _referenced_tweets(username: str, tweet: dict[str, Any]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    if quote_url := tweet.get("qrtURL"):
        references.append(_reference_from_url("quote", str(quote_url)))
    if retweet_url := tweet.get("retweetURL"):
        references.append(_reference_from_url("repost", str(retweet_url)))
    if replying_to_id := tweet.get("replyingToID"):
        reply_username = str(tweet.get("replyingTo") or username)
        references.append(
            {
                "type": "reply",
                "username": reply_username,
                "tweet_id": str(replying_to_id),
                "url": f"https://x.com/{reply_username}/status/{replying_to_id}",
            }
        )

    seen: set[tuple[str | None, str | None, str | None]] = set()
    unique: list[dict[str, Any]] = []
    for reference in references:
        key = (reference.get("type"), reference.get("username"), reference.get("tweet_id"))
        if key not in seen:
            unique.append(reference)
            seen.add(key)
    return unique


def _reference_from_url(reference_type: str, url: str) -> dict[str, Any]:
    normalized_url = normalize_x_url(url)
    match = TWITTER_STATUS_URL_RE.match(normalized_url)
    if not match:
        return {"type": reference_type, "url": normalized_url}
    return {
        "type": reference_type,
        "username": match.group(1),
        "tweet_id": match.group(2),
        "url": normalized_url,
    }


async def _hydrate_references(
    session: AsyncSession,
    references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for reference in references:
        username = reference.get("username")
        tweet_id = reference.get("tweet_id")
        if not username or not tweet_id:
            continue
        data = await fetch_tweet(str(username), str(tweet_id))
        if not data:
            continue
        reference["text"] = data.get("text") or ""
        reference["media"] = _tweet_media(data)
        reference["url"] = _tweet_url(str(username), data) or reference.get("url")
        await _upsert_social_post(
            session,
            data.get("screen_name") or str(username),
            data,
            data,
            hydrate_references=False,
        )
    return references


def _tweet_links(
    tweet: dict[str, Any],
    tweet_url: str | None,
    media: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> list[str]:
    excluded: set[str] = set()
    if tweet_url:
        excluded.add(tweet_url)
    excluded.update(str(item["url"]) for item in media if item.get("url"))
    excluded.update(str(item["preview_url"]) for item in media if item.get("preview_url"))
    excluded.update(str(item["video_url"]) for item in media if item.get("video_url"))
    excluded.update(str(reference["url"]) for reference in references if reference.get("url"))

    links: list[str] = []
    for raw_url in URL_RE.findall(tweet.get("text") or ""):
        _append_link(links, raw_url, excluded)
    for field in ("expanded_urls", "urls"):
        raw_links = tweet.get(field)
        if not isinstance(raw_links, list):
            continue
        for item in raw_links:
            if isinstance(item, str):
                _append_link(links, item, excluded)
            elif isinstance(item, dict):
                raw_url = item.get("expanded_url") or item.get("url") or item.get("display_url")
                _append_link(links, raw_url, excluded)
    entities = tweet.get("entities")
    if isinstance(entities, dict):
        entity_urls = entities.get("urls")
        if isinstance(entity_urls, list):
            for item in entity_urls:
                if isinstance(item, dict):
                    raw_url = item.get("expanded_url") or item.get("url")
                    _append_link(links, raw_url, excluded)
    return links


def _append_link(links: list[str], raw_url: Any, excluded: set[str]) -> None:
    if not raw_url:
        return
    url = normalize_x_url(str(raw_url).rstrip(".,;:"))
    if url in excluded or url in links:
        return
    links.append(url)


def _llm_context(
    content: str,
    tweet_url: str | None,
    links: list[str],
    references: list[dict[str, Any]],
) -> str:
    lines = []
    if content:
        lines.append(content)
    if tweet_url:
        lines.append(f"原文链接: {tweet_url}")
    if links:
        lines.append("外部链接: " + ", ".join(links[:5]))
    for reference in references:
        label = _reference_type_label(str(reference.get("type") or "reference"))
        ref_url = reference.get("url")
        ref_text = reference.get("text")
        if ref_url or ref_text:
            lines.append(f"{label}: {ref_text or ''} {ref_url or ''}".strip())
    return "\n".join(lines).strip()


def _format_reference_lines(references: list[dict[str, Any]]) -> list[str]:
    lines = ["关联推文:"]
    for reference in references[:3]:
        label = _reference_type_label(str(reference.get("type") or "reference"))
        url = reference.get("url") or ""
        text = reference.get("text") or ""
        preview = f" — {text[:120]}" if text else ""
        lines.append(f"- {label}: {url}{preview}")
    if len(references) > 3:
        lines.append(f"- 还有 {len(references) - 3} 条关联推文")
    return lines


def _format_media_lines(media: list[dict[str, Any]]) -> list[str]:
    lines = ["媒体:"]
    for item in media[:MAX_PUSHED_MEDIA]:
        media_type = item.get("type") or "media"
        alt_text = item.get("alt_text")
        suffix = f" ({alt_text[:80]})" if alt_text else ""
        lines.append(f"- {media_type}: {item.get('url')}{suffix}")
    if len(media) > MAX_PUSHED_MEDIA:
        lines.append(f"- 还有 {len(media) - MAX_PUSHED_MEDIA} 个媒体")
    return lines


def _format_link_lines(links: list[str]) -> list[str]:
    lines = ["链接:"]
    lines.extend(f"- {link}" for link in links[:5])
    if len(links) > 5:
        lines.append(f"- 还有 {len(links) - 5} 个链接")
    return lines


async def _build_tweet_card(
    post: SocialPost,
    adapter: BotAdapter,
    cross_ref: dict | None = None,
    is_backfill: bool = False,
) -> dict:
    title = _tweet_card_title(post, is_backfill=is_backfill)
    elements: list[dict[str, Any]] = []
    sections: list[str] = []

    metadata = _tweet_metadata_text(post)
    elements.append(_md_div(metadata))
    sections.append(metadata)

    if post.is_noteworthy:
        alert = "**重点关注**"
        if post.attention_reason:
            alert += f"\n{_trim_text(post.attention_reason, 240)}"
        elements.extend([{"tag": "hr"}, _md_div(alert)])
        sections.append(alert)

    if post.summary:
        summary = f"**摘要**\n{_trim_text(post.summary, CARD_SUMMARY_LIMIT)}"
        elements.extend([{"tag": "hr"}, _md_div(summary)])
        sections.append(summary)

    body_limit = CARD_BODY_WITH_SUMMARY_LIMIT if post.summary else CARD_BODY_LIMIT
    body = f"**正文**\n{_trim_text(post.content or '（无正文）', body_limit)}"
    elements.extend([{"tag": "hr"}, _md_div(body)])
    sections.append(body)

    if post.translated_content and (not post.summary or post.is_noteworthy):
        translated = _trim_text(
            _display_translation_text(post.translated_content), CARD_TRANSLATION_LIMIT
        )
        translation = f"**译文**\n{translated}"
        elements.extend([{"tag": "hr"}, _md_div(translation)])
        sections.append(translation)

    if cross_ref and cross_ref.get("matches"):
        match_text = f"**关联持仓/关注**\n{cross_ref['matches']}"
        elements.extend([{"tag": "hr"}, _md_div(match_text)])
        sections.append(match_text)

    if post.media:
        media_elements, media_section = await _tweet_media_card_elements(post.media, adapter)
        if media_elements:
            elements.extend([{"tag": "hr"}, *media_elements])
        if media_section:
            sections.append(media_section)

    reference_text = _reference_footer_text(post.referenced_tweets or [], post.links or [])
    if reference_text:
        elements.extend([{"tag": "hr"}, _md_div(reference_text)])
        sections.append(reference_text)

    elements.extend([{"tag": "hr"}, _note_text("回复这张卡片并 @Reveal，可基于这条更新继续研究。")])

    card: dict[str, Any] = {
        "title": title,
        "sections": sections,
        "footer": "回复这张卡片并 @Reveal，可以基于这条更新继续研究。",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _tweet_card_template(post),
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }
    if post.tweet_url:
        card["card_link"] = {
            "url": post.tweet_url,
            "pc_url": post.tweet_url,
            "ios_url": post.tweet_url,
            "android_url": post.tweet_url,
        }
    return card


def _build_tweet_topic_card(
    posts: list[SocialPost],
    cross_ref: dict[int, dict] | None = None,
) -> dict:
    latest_post = posts[-1]
    label = _topic_label_for_group(posts)
    title = f"X Signal · {label}"
    elements: list[dict[str, Any]] = []
    sections: list[str] = []

    metadata = _topic_metadata_text(posts)
    elements.append(_md_div(metadata))
    sections.append(metadata)

    lead = _topic_lead_text(posts)
    if lead:
        lead_section = f"**事实**\n{lead}"
        elements.extend([{"tag": "hr"}, _md_div(lead_section)])
        sections.append(lead_section)

    viewpoint = _topic_viewpoint_text(posts, cross_ref or {})
    elements.extend([{"tag": "hr"}, _md_div(viewpoint)])
    sections.append(viewpoint)

    reference_text = _topic_reference_text(posts)
    if reference_text:
        elements.extend([{"tag": "hr"}, _md_div(reference_text)])
        sections.append(reference_text)

    elements.extend([{"tag": "hr"}, _note_text("回复这张卡片并 @Reveal，可基于这个主题继续研究。")])

    card: dict[str, Any] = {
        "title": title,
        "sections": sections,
        "footer": "同一主题由多个 X 账号提到；回复这张卡片并 @Reveal，可继续研究。",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _tweet_topic_card_template(posts),
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }
    if link := _topic_card_link(posts):
        card["card_link"] = {
            "url": link,
            "pc_url": link,
            "ios_url": link,
            "android_url": link,
        }
    elif latest_post.tweet_url:
        card["card_link"] = {
            "url": latest_post.tweet_url,
            "pc_url": latest_post.tweet_url,
            "ios_url": latest_post.tweet_url,
            "android_url": latest_post.tweet_url,
        }
    return card


def _topic_metadata_text(posts: list[SocialPost]) -> str:
    latest_post = posts[-1]
    usernames = list(dict.fromkeys(post.username for post in posts))
    lines = [
        f"{len(posts)} 条市场相关更新 · {len(usernames)} 位博主提到 · "
        f"最新 {_format_beijing_time(latest_post.posted_at)}"
    ]

    tickers = _common_values(post.mentioned_tickers for post in posts)
    topics = _common_values(post.topics for post in posts)
    tag_parts: list[str] = []
    if tickers:
        tag_parts.append("Ticker: " + ", ".join(tickers[:8]))
    if topics:
        tag_parts.append("Topic: " + " · ".join(topics[:5]))
    if any(post.is_noteworthy or post.urgency == "high" for post in posts):
        tag_parts.append("标记: 重点关注")
    if all(agent_market_relevance(post) is True for post in posts):
        tag_parts.append("Agent: market relevant")
    if tag_parts:
        lines.append(" · ".join(tag_parts))
    return "\n".join(lines)


def _topic_lead_text(posts: list[SocialPost]) -> str:
    summaries = [post.summary for post in posts if post.summary]
    if summaries:
        return _trim_text(summaries[-1], 280)
    return _trim_text(posts[-1].content or "", 280)


def _topic_viewpoint_text(posts: list[SocialPost], cross_ref: dict[int, dict]) -> str:
    lines = ["**博主与观点**"]
    for post in posts:
        author = f"@{post.username}"
        if post.tweet_url:
            author = f"[@{post.username}]({post.tweet_url})"
        view = post.attention_reason or post.summary or post.content or "（无正文）"
        suffix_parts: list[str] = []
        if sentiment := _sentiment_label(post.sentiment):
            suffix_parts.append(f"情绪: {sentiment}")
        if post.urgency:
            suffix_parts.append(f"优先级: {post.urgency}")
        if post.id and (match := cross_ref.get(post.id)):
            if match.get("matches"):
                suffix_parts.append(f"关联: {match['matches']}")
        suffix = f" ({' · '.join(suffix_parts)})" if suffix_parts else ""
        lines.append(f"- {author}: {_trim_text(view, 180)}{suffix}")
    return "\n".join(lines)


def _topic_reference_text(posts: list[SocialPost]) -> str:
    links: list[str] = []
    references: list[dict[str, Any]] = []
    tweet_links: list[str] = []
    for post in posts:
        for link in post.links or []:
            if link not in links:
                links.append(str(link))
        for reference in post.referenced_tweets or []:
            if reference not in references:
                references.append(reference)
        if post.tweet_url and post.tweet_url not in tweet_links:
            tweet_links.append(post.tweet_url)

    lines: list[str] = []
    if links:
        lines.append("外链: " + " · ".join(f"[{_display_url(link)}]({link})" for link in links[:5]))
    if references:
        ref_parts = []
        for reference in references[:MAX_CARD_REFERENCES]:
            label = _reference_type_label(str(reference.get("type") or "reference"))
            username = reference.get("username")
            url = str(reference.get("url") or "")
            title = label if not username else f"{label} @{username}"
            ref_parts.append(f"[{title}]({url})" if url else title)
        lines.append("引用: " + " · ".join(ref_parts))
    if tweet_links:
        lines.append(
            "原文: "
            + " · ".join(
                f"[@{post.username}]({post.tweet_url})" for post in posts if post.tweet_url
            )
        )
    if not lines:
        return ""
    return "**参考**\n" + "\n".join(lines)


def _topic_card_link(posts: list[SocialPost]) -> str | None:
    for post in posts:
        for link in post.links or []:
            if not is_x_url(str(link)):
                return str(link)
    for post in posts:
        if post.tweet_url:
            return post.tweet_url
    return None


def _tweet_topic_card_template(posts: list[SocialPost]) -> str:
    if any(post.is_noteworthy or post.urgency == "high" for post in posts):
        return "orange"
    if any(post.urgency == "medium" for post in posts):
        return "blue"
    return "green"


def _topic_label_for_group(posts: list[SocialPost]) -> str:
    topics = _common_values(post.topics for post in posts)
    tickers = _common_values(post.mentioned_tickers for post in posts)
    label_parts: list[str] = []
    if tickers:
        label_parts.append(", ".join(tickers[:3]))
    if topics:
        label_parts.append(" · ".join(topics[:3]))
    if label_parts:
        return _trim_text(" · ".join(label_parts), 80)
    if link := _topic_card_link(posts):
        return _display_url(link)
    return _trim_text(posts[-1].summary or posts[-1].content or "相关更新", 80)


def _common_values(value_lists) -> list[str]:
    counter: Counter[str] = Counter()
    for values in value_lists:
        if not values:
            continue
        for value in values:
            if isinstance(value, str) and value.strip():
                counter[value.strip()] += 1
    return [value for value, _count in counter.most_common()]


def _tweet_card_title(post: SocialPost, is_backfill: bool = False) -> str:
    if post.is_noteworthy:
        prefix = "重点关注"
    else:
        prefix = "Twitter Backfill" if is_backfill else "Twitter Update"
    flags = _post_type_label(post)
    return f"{prefix} · @{post.username}{flags}"


def _tweet_card_template(post: SocialPost) -> str:
    if post.is_noteworthy or post.urgency == "high":
        return "orange"
    if post.urgency == "medium":
        return "blue"
    if post.urgency == "low" and (
        post.mentioned_tickers or post.topics or post.sentiment in {"bullish", "bearish", "mixed"}
    ):
        return "green"
    return "grey"


def _tweet_metadata_text(post: SocialPost) -> str:
    user_url = f"https://x.com/{post.username}"
    parts = [f"[@{post.username}]({user_url})", _format_beijing_time(post.posted_at)]
    if labels := _post_type_label(post):
        parts.append(labels.strip(" ()"))
    if post.id:
        parts.append(f"消息 ID #{post.id}")
    if post.tweet_url:
        parts.append(f"[原文]({post.tweet_url})")

    tag_parts: list[str] = []
    if post.mentioned_tickers:
        tag_parts.append("Ticker: " + ", ".join(str(t) for t in post.mentioned_tickers[:8]))
    if post.topics:
        tag_parts.append("Topic: " + " · ".join(str(t) for t in post.topics[:5]))
    if sentiment := _sentiment_label(post.sentiment):
        tag_parts.append(f"情绪: {sentiment}")
    if post.urgency:
        tag_parts.append(f"优先级: {post.urgency}")
    if post.is_noteworthy:
        tag_parts.append("标记: 重点关注")

    lines = [" · ".join(parts)]
    if tag_parts:
        lines.append(" · ".join(tag_parts))
    return "\n".join(lines)


async def _tweet_media_card_elements(
    media: list[dict[str, Any]],
    adapter: BotAdapter,
) -> tuple[list[dict[str, Any]], str]:
    elements: list[dict[str, Any]] = []
    section_lines = ["**媒体**"]
    image_count = 0
    image_total = 0
    video_lines: list[str] = []

    for item in media[:MAX_PUSHED_MEDIA]:
        media_type = str(item.get("type") or "media").lower()
        display_url = str(item.get("video_url") or item.get("url") or "")
        preview_url = _media_preview_url(item)
        alt_text = str(item.get("alt_text") or media_type)

        is_video = media_type.startswith(("video", "gif"))
        if not is_video:
            image_total += 1

        image_key = None
        if preview_url and image_count < MAX_CARD_IMAGES:
            image_key = await _upload_card_image(adapter, preview_url, alt_text)
        if image_key:
            elements.append(
                {
                    "tag": "img",
                    "img_key": image_key,
                    "alt": {"tag": "plain_text", "content": _trim_text(alt_text, 80)},
                }
            )
            image_count += 1

        if is_video:
            video_lines.append(f"- 视频: [{_display_url(display_url)}]({display_url})")

    element_lines: list[str] = []
    if image_count:
        element_lines.append(f"{image_count} 张图片已展示")
    elif image_total:
        element_lines.append(f"{image_total} 张图片已缓存")
    section_lines.extend(f"- {line}" for line in element_lines)
    section_lines.extend(video_lines)

    if len(media) > MAX_PUSHED_MEDIA:
        section_lines.append(f"- 还有 {len(media) - MAX_PUSHED_MEDIA} 个媒体已缓存")
        element_lines.append(f"还有 {len(media) - MAX_PUSHED_MEDIA} 个媒体已缓存")
    if len(section_lines) > 1:
        compact_lines = [f"- {line}" for line in element_lines]
        compact_lines.extend(video_lines)
        elements.append(_md_div("**媒体**\n" + "\n".join(compact_lines)))
    return elements, "\n".join(section_lines)


def _reference_footer_text(
    references: list[dict[str, Any]],
    links: list[str],
) -> str:
    lines: list[str] = []
    if references:
        ref_parts = []
        for reference in references[:MAX_CARD_REFERENCES]:
            label = _reference_type_label(str(reference.get("type") or "reference"))
            username = reference.get("username")
            url = str(reference.get("url") or "")
            title = label if not username else f"{label} @{username}"
            if url:
                ref_parts.append(f"[{title}]({url})")
            else:
                ref_parts.append(title)
        if len(references) > MAX_CARD_REFERENCES:
            ref_parts.append(f"还有 {len(references) - MAX_CARD_REFERENCES} 条")
        lines.append("引用: " + " · ".join(ref_parts))

    if links:
        link_parts = [f"[{_display_url(link)}]({link})" for link in links[:5]]
        if len(links) > 5:
            link_parts.append(f"还有 {len(links) - 5} 个")
        lines.append("外链: " + " · ".join(link_parts))

    if not lines:
        return ""
    return "**参考**\n" + "\n".join(lines)


def _media_preview_url(item: dict[str, Any]) -> str | None:
    media_type = str(item.get("type") or "").lower()
    preview_url = item.get("preview_url")
    if preview_url:
        return str(preview_url)
    url = str(item.get("url") or "")
    if media_type.startswith("video") or url.lower().split("?", maxsplit=1)[0].endswith(".mp4"):
        return None
    return url or None


async def _upload_card_image(
    adapter: BotAdapter,
    image_url: str,
    alt_text: str | None = None,
) -> str | None:
    try:
        return await adapter.upload_image(image_url, alt_text)
    except Exception:
        logger.exception("Card image upload failed: {}", image_url)
        return None


def _md_div(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _note_text(content: str) -> dict[str, Any]:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def _display_translation_text(text: str) -> str:
    context_prefixes = ("原文链接:", "外部链接:", "引用:", "回复:", "转发:")
    lines = [line for line in text.splitlines() if not line.strip().startswith(context_prefixes)]
    return "\n".join(lines).strip() or text


def _trim_text(text: str, limit: int) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def _display_url(url: str) -> str:
    clean = url.replace("https://", "").replace("http://", "")
    return _trim_text(clean, 70)


def _sentiment_label(sentiment: str | None) -> str:
    return {
        "bullish": "看多",
        "bearish": "看空",
        "mixed": "分歧",
        "neutral": "中性",
    }.get(sentiment or "", "")


async def _cross_reference_posts(posts: list[SocialPost]) -> dict[int, dict]:
    """Cross-reference tweet tickers with user's portfolio and watchlist."""
    all_tickers: set[str] = set()
    for post in posts:
        if post.mentioned_tickers:
            all_tickers.update(str(t) for t in post.mentioned_tickers)
    if not all_tickers:
        return {}

    held_tickers: set[str] = set()
    position_info: dict[str, str] = {}
    try:
        from server.journal.service import get_trades_for_period

        trades = await get_trades_for_period("all")
        for trade in trades:
            if trade.exit_price is None:
                held_tickers.add(trade.ticker)
                unrealized = (0.0 - trade.entry_price) * trade.quantity
                try:
                    from server.stock.data import get_current_price

                    price = await get_current_price(trade.ticker)
                    if price:
                        unrealized = (price - trade.entry_price) * trade.quantity
                        if trade.direction == "short":
                            unrealized = -unrealized
                except Exception:
                    logger.exception(
                        "Cross-reference position price fetch failed for {}", trade.ticker
                    )
                position_info[trade.ticker] = (
                    f"{trade.ticker} (持有 {trade.quantity} 股, 浮盈 ${unrealized:+,.0f})"
                )
    except Exception:
        logger.exception("Cross-reference portfolio fetch failed")

    tracked_tickers: set[str] = set()
    try:
        from server.stock.tracker import get_active_tickers

        tracked_tickers = set(await get_active_tickers())
    except Exception:
        logger.exception("Cross-reference tracked tickers fetch failed")

    watchlist: set[str] = set()
    try:
        from server.stock.scanner import DEFAULT_WATCHLIST

        watchlist = set(DEFAULT_WATCHLIST)
    except Exception:
        logger.exception("Cross-reference default watchlist fetch failed")

    user_tickers = held_tickers | tracked_tickers | watchlist

    result: dict[int, dict] = {}
    for post in posts:
        if not post.mentioned_tickers or not post.id:
            continue
        overlap = {str(t) for t in post.mentioned_tickers} & user_tickers
        if not overlap:
            continue
        parts = []
        for ticker in sorted(overlap):
            if ticker in position_info:
                parts.append(position_info[ticker])
            elif ticker in tracked_tickers:
                parts.append(f"{ticker} (追踪中)")
            else:
                parts.append(f"{ticker} (关注列表)")
        result[post.id] = {"matches": ", ".join(parts[:5])}
    return result


async def _push_card_to_admin(adapter: BotAdapter, card: dict) -> list[tuple[str, str]]:
    try:
        sent_messages: list[tuple[str, str]] = []
        chat_ids = _admin_chat_ids(adapter)
        if chat_ids:
            for chat_id in chat_ids:
                message_id = await adapter.send_card_returning_id(chat_id, card)
                if message_id:
                    sent_messages.append((chat_id, message_id))
            return sent_messages
    except Exception:
        logger.exception("Card push failed; falling back to text")
    return await _push_to_admin(adapter, _card_to_text(card))


async def _push_to_admin(adapter: BotAdapter, text: str) -> list[tuple[str, str]]:
    try:
        sent_messages: list[tuple[str, str]] = []
        admin_chat_id = getattr(adapter, "admin_chat_id", None)
        if admin_chat_id:
            message_id = await adapter.send_message_returning_id(admin_chat_id, text)
            if message_id:
                sent_messages.append((str(admin_chat_id), message_id))
        else:
            chat_ids = _admin_chat_ids(adapter)
            if chat_ids:
                for chat_id in chat_ids:
                    message_id = await adapter.send_message_returning_id(chat_id, text)
                    if message_id:
                        sent_messages.append((chat_id, message_id))
            else:
                await adapter.push_to_admin(text)
        return sent_messages
    except Exception:
        logger.exception("Admin text push failed; falling back to adapter.push_to_admin")
        await adapter.push_to_admin(text)
        return []


def _compact_text(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def _compact_metadata(post: SocialPost) -> str:
    parts = []
    if post.links:
        parts.append(f"{len(post.links)} link")
    if post.media:
        parts.append(f"{len(post.media)} media")
    if post.referenced_tweets:
        parts.append(f"{len(post.referenced_tweets)} ref")
    return " / ".join(parts)


def _format_digest_section(index: int, post: SocialPost, cross_ref: dict | None = None) -> str:
    flags = _post_type_label(post)
    urgency_icon = {"high": "🔴", "medium": "🟡"}.get(post.urgency or "", "")
    header = f"{index}. {urgency_icon}#{post.id} {_format_beijing_time(post.posted_at)}{flags}"

    preview = _compact_text(post.summary or post.content or "（无正文）", 220)
    lines = [header, preview]

    if post.topics:
        lines.append("🏷 " + " · ".join(str(t) for t in post.topics[:5]))

    sentiment_label = {"bullish": "📈 看多", "bearish": "📉 看空", "mixed": "⚖️ 分歧"}.get(
        post.sentiment or ""
    )
    if sentiment_label:
        lines.append(sentiment_label)

    if post.mentioned_tickers:
        lines.append("📊 提及: " + ", ".join(str(t) for t in post.mentioned_tickers[:8]))

    if cross_ref and cross_ref.get("matches"):
        lines.append("⚠️ 关联持仓: " + cross_ref["matches"])

    metadata = _compact_metadata(post)
    if metadata:
        lines.append(metadata)
    if post.tweet_url:
        lines.append(f"原文: {post.tweet_url}")
    return "\n".join(lines)


def _build_research_card(title: str, sections: list[str], footer: str) -> dict:
    elements: list[dict[str, Any]] = []
    for section in sections:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": section}})
        elements.append({"tag": "hr"})
    if footer:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": footer}})
    if elements and elements[-1].get("tag") == "hr":
        elements.pop()
    return {
        "title": title,
        "sections": sections,
        "footer": footer,
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def _card_to_text(card: dict) -> str:
    lines = []
    if title := card.get("title"):
        lines.extend([str(title), ""])
    for section in card.get("sections", []):
        lines.extend([str(section), ""])
    if footer := card.get("footer"):
        lines.append(str(footer))
    return "\n".join(lines).strip()


def _admin_chat_ids(adapter: BotAdapter) -> list[str]:
    chat_ids: list[str] = []
    admin_chat_ids = getattr(adapter, "admin_chat_ids", None)
    if isinstance(admin_chat_ids, (list, tuple, set)):
        chat_ids.extend(str(chat_id) for chat_id in admin_chat_ids if chat_id)
    admin_chat_id = getattr(adapter, "admin_chat_id", None)
    if admin_chat_id:
        chat_ids.append(str(admin_chat_id))
    return list(dict.fromkeys(chat_ids))


async def _bind_sent_messages(sent_messages: list[tuple[str, str]], post_id: int | None) -> None:
    if not sent_messages or post_id is None:
        return
    from server.bot.bindings import bind_message_to_source

    for chat_id, message_id in sent_messages:
        await bind_message_to_source(chat_id, message_id, "twitter", post_id)


def _post_type_label(post) -> str:
    labels = []
    if post.is_repost:
        labels.append("转推")
    if post.is_quote:
        labels.append("引用")
    if post.is_reply:
        labels.append("回复")
    return f" ({'/'.join(labels)})" if labels else ""


def _reference_type_label(reference_type: str) -> str:
    return {
        "quote": "引用",
        "repost": "转推",
        "reply": "回复",
    }.get(reference_type, "关联")


def _parse_cache_max_age(cache_control: str) -> float | None:
    match = re.search(r"max-age=(\d+(?:\.\d+)?)", cache_control)
    return float(match.group(1)) if match else None
