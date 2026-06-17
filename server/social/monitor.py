"""Twitter/X monitor using vxTwitter API with persistence and LLM integration."""

import asyncio
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, cast
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
from server.social.impact import build_personal_impact_lines
from server.social.relevance import (
    group_similar_social_posts,
    is_relevant_social_post,
    is_x_url,
)
from server.social.twitter_graphql import fetch_user_tweets_graphql
from server.social.urls import normalize_x_url

TWITTER_STATUS_URL_RE = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/([^/\s]+)/status/(\d+)")
URL_RE = re.compile(r"https?://[^\s<>)\]]+")
INITIAL_WATCH_BACKFILL_LIMIT = 10
MAX_CARD_REFERENCES = 3
CARD_SIGNAL_FACT_LIMIT = 1000
CARD_SIGNAL_VIEW_LIMIT = 240
STORY_CLUSTER_CONTENT_LIMIT = 500
STORY_CLUSTER_REFERENCE_LIMIT = 180
STORY_CLUSTER_MAX_BATCH_POSTS = 12
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
                    "canonical_event": {
                        "id": analysis.canonical_event_id,
                        "title": analysis.canonical_event_title,
                        "summary": analysis.canonical_event_summary,
                    },
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
    llm_processor=None,
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
    grouped_posts = await _group_relevant_social_posts(relevant_posts, llm_processor)
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


async def _group_relevant_social_posts(
    posts: list[SocialPost],
    llm_processor=None,
) -> list[list[SocialPost]]:
    ordered_posts = sorted(posts, key=lambda post: post.posted_at)
    if len(ordered_posts) <= 1:
        return [ordered_posts] if ordered_posts else []

    if len(ordered_posts) > STORY_CLUSTER_MAX_BATCH_POSTS and callable(
        getattr(llm_processor, "cluster_stories", None)
    ):
        groups: list[list[SocialPost]] = []
        for chunk in _chunk_social_posts(ordered_posts, STORY_CLUSTER_MAX_BATCH_POSTS):
            groups.extend(await _group_relevant_social_posts(chunk, llm_processor))
        return sorted(groups, key=lambda group: group[-1].posted_at)

    agent_groups = await _cluster_social_posts_with_agent(ordered_posts, llm_processor)
    if agent_groups is not None:
        return agent_groups

    return group_similar_social_posts(ordered_posts)


def _chunk_social_posts(
    posts: Sequence[SocialPost],
    size: int,
) -> list[list[SocialPost]]:
    return [list(posts[index : index + size]) for index in range(0, len(posts), size)]


async def _cluster_social_posts_with_agent(
    posts: list[SocialPost],
    llm_processor=None,
) -> list[list[SocialPost]] | None:
    cluster_stories = getattr(llm_processor, "cluster_stories", None)
    if not callable(cluster_stories):
        return None

    try:
        cluster_stories_func = cast(
            Callable[[list[dict[str, Any]]], Awaitable[Sequence[Any] | None]],
            cluster_stories,
        )
        clusters = await cluster_stories_func([_story_cluster_input(post) for post in posts])
    except Exception:
        logger.exception("Tweet story clustering failed; falling back to exact grouping")
        return None

    if clusters is None:
        return None

    return _groups_from_story_clusters(posts, clusters)


def _story_cluster_input(post: SocialPost) -> dict[str, Any]:
    raw = post.raw_json if isinstance(post.raw_json, dict) else {}
    raw_analysis = raw.get("reveal_analysis")
    analysis = raw_analysis if isinstance(raw_analysis, dict) else {}
    return {
        "id": _story_cluster_post_id(post),
        "author": post.username,
        "tweet_id": post.tweet_id,
        "tweet_url": post.tweet_url,
        "time": _format_beijing_time(post.posted_at),
        "content": (post.content or "")[:STORY_CLUSTER_CONTENT_LIMIT],
        "summary": post.summary or "",
        "attention_reason": post.attention_reason or "",
        "tickers": post.mentioned_tickers or [],
        "topics": post.topics or [],
        "canonical_event": analysis.get("canonical_event") or {},
        "links": post.links or [],
        "referenced_tweets": [
            {
                "type": reference.get("type"),
                "username": reference.get("username"),
                "url": reference.get("url"),
                "text": str(reference.get("text") or "")[:STORY_CLUSTER_REFERENCE_LIMIT],
            }
            for reference in (post.referenced_tweets or [])
            if isinstance(reference, dict)
        ],
    }


def _groups_from_story_clusters(
    posts: list[SocialPost],
    clusters: Sequence[Any],
) -> list[list[SocialPost]]:
    post_by_id = {_story_cluster_post_id(post): post for post in posts}
    used: set[str] = set()
    groups: list[list[SocialPost]] = []

    for cluster in clusters:
        post_ids = _cluster_post_ids(cluster)
        candidates = [post_by_id[post_id] for post_id in post_ids if post_id in post_by_id]
        candidates = [post for post in candidates if _story_cluster_post_id(post) not in used]
        if not candidates:
            continue

        confidence = str(getattr(cluster, "confidence", "") or "").strip().lower()
        if len(candidates) > 1 and confidence == "high":
            group = sorted(candidates, key=lambda post: post.posted_at)
            groups.append(group)
            used.update(_story_cluster_post_id(post) for post in group)
            continue

        for post in sorted(candidates, key=lambda item: item.posted_at):
            groups.append([post])
            used.add(_story_cluster_post_id(post))

    for post in posts:
        post_id = _story_cluster_post_id(post)
        if post_id not in used:
            groups.append([post])

    return sorted(groups, key=lambda group: group[-1].posted_at)


def _cluster_post_ids(cluster: Any) -> list[str]:
    if isinstance(cluster, dict):
        raw_ids = cluster.get("post_ids") or []
    else:
        raw_ids = getattr(cluster, "post_ids", []) or []
    return [str(post_id or "").strip() for post_id in raw_ids if str(post_id or "").strip()]


def _story_cluster_post_id(post: SocialPost) -> str:
    return str(post.id or post.tweet_id)


async def push_tweet_topic_card(
    posts: list[SocialPost],
    adapter: BotAdapter,
    cross_ref: dict[int, dict] | None = None,
) -> None:
    """Push a single story-centered card for multiple related tweets."""
    ordered_posts = sorted(posts, key=lambda post: post.posted_at)
    card = await _build_tweet_signal_card(ordered_posts, cross_ref=cross_ref or {})
    sent_messages = await _push_card_to_admin(adapter, card)
    await _bind_sent_messages(
        sent_messages,
        ordered_posts[-1].id,
        _social_post_source_type(ordered_posts[-1]),
    )


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
    await _bind_sent_messages(sent_messages, post.id, _social_post_source_type(post))


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
        pushed_posts = await push_relevant_tweet_cards(
            candidates,
            adapter,
            llm_processor=llm_processor,
        )
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


async def _build_tweet_card(
    post: SocialPost,
    _adapter: BotAdapter,
    cross_ref: dict | None = None,
    is_backfill: bool = False,
) -> dict:
    normalized_cross_ref = {post.id: cross_ref} if post.id and cross_ref else {}
    return await _build_tweet_signal_card(
        [post],
        cross_ref=normalized_cross_ref,
        is_backfill=is_backfill,
    )


async def _build_tweet_signal_card(
    posts: list[SocialPost],
    cross_ref: dict[int, dict] | None = None,
    is_backfill: bool = False,
) -> dict:
    ordered_posts = sorted(posts, key=lambda post: post.posted_at)
    latest_post = ordered_posts[-1]
    cross_ref = cross_ref or {}
    title = _tweet_signal_card_title(ordered_posts, is_backfill=is_backfill)
    elements: list[dict[str, Any]] = []
    sections: list[str] = []

    section_builders = [
        _tweet_signal_time_text(ordered_posts),
        _tweet_signal_market_text(ordered_posts, cross_ref),
        _tweet_signal_fact_text(ordered_posts),
        _tweet_signal_viewpoint_text(ordered_posts, cross_ref),
        _tweet_signal_impact_advice_text(ordered_posts, cross_ref),
        _tweet_signal_reference_text(ordered_posts),
    ]
    for section in section_builders:
        if not section:
            continue
        if elements:
            elements.append({"tag": "hr"})
        elements.append(_md_div(section))
        sections.append(section)

    elements.extend([{"tag": "hr"}, _note_text("回复这张卡片并 @Reveal，可基于这个主题继续研究。")])

    card: dict[str, Any] = {
        "title": title,
        "sections": sections,
        "footer": "回复这张卡片并 @Reveal，可以基于这个主题继续研究。",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _tweet_signal_card_template(ordered_posts),
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }
    if link := _topic_card_link(ordered_posts):
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


def _tweet_signal_card_title(posts: list[SocialPost], is_backfill: bool = False) -> str:
    label = _trim_text(_topic_label_for_group(posts), 72)
    authors = _tweet_signal_author_list(posts)
    prefix = "Twitter Backfill · " if is_backfill else ""
    return _trim_text(f"{prefix}{label} · {authors} 提到", 120)


def _tweet_signal_author_list(posts: list[SocialPost], limit: int = 4) -> str:
    usernames = list(dict.fromkeys(post.username for post in posts if post.username))
    if not usernames:
        return "@unknown"
    visible = ", ".join(f"@{username}" for username in usernames[:limit])
    if len(usernames) > limit:
        visible += f" 等 {len(usernames)} 人"
    return visible


def _tweet_signal_time_text(posts: list[SocialPost]) -> str:
    latest_post = posts[-1]
    lines = ["**时间**", _format_beijing_time(latest_post.posted_at)]
    if len(posts) > 1:
        usernames = list(dict.fromkeys(post.username for post in posts if post.username))
        lines.append(f"{len(posts)} 条更新 · {len(usernames)} 位博主提到")
    return "\n".join(lines)


def _tweet_signal_market_text(posts: list[SocialPost], cross_ref: dict[int, dict]) -> str:
    lines = ["**市场**"]
    tickers = _common_values(post.mentioned_tickers for post in posts)
    topics = _common_values(post.topics for post in posts)
    matched = _tweet_signal_cross_ref_matches(posts, cross_ref)

    if topics:
        lines.append("相关领域: " + " · ".join(topics[:6]))
    if tickers:
        lines.append("相关股票: " + ", ".join(tickers[:8]))
    if matched:
        lines.append("关注/持仓: " + " · ".join(matched[:8]))
    if len(lines) == 1:
        lines.append("相关领域: 市场消息")
    return "\n".join(lines)


def _tweet_signal_fact_text(posts: list[SocialPost]) -> str:
    candidates = [post.summary for post in posts if post.summary]
    if not candidates:
        candidates = [post.content for post in posts if post.content]
    facts = _unique_compact_texts(candidates, limit=4)
    if not facts:
        fact_text = "暂无明确事实摘要。"
    elif len(facts) == 1:
        fact_text = facts[0]
    else:
        fact_text = "；".join(facts)
    return "**事实**\n" + _trim_text(fact_text, CARD_SIGNAL_FACT_LIMIT)


def _tweet_signal_viewpoint_text(posts: list[SocialPost], cross_ref: dict[int, dict]) -> str:
    lines = ["**观点**"]
    for post in posts:
        author = f"@{post.username}"
        if post.tweet_url:
            author = f"[@{post.username}]({post.tweet_url})"
        view = post.attention_reason or post.summary or post.content or "（无正文）"
        suffix_parts: list[str] = []
        if sentiment := _sentiment_label(post.sentiment):
            if sentiment != "中性":
                suffix_parts.append(sentiment)
        if post.id and (match := cross_ref.get(post.id)):
            if match.get("matches"):
                suffix_parts.append(str(match["matches"]))
        suffix = f"（{' · '.join(suffix_parts)}）" if suffix_parts else ""
        lines.append(f"- {author}: {_trim_text(view, CARD_SIGNAL_VIEW_LIMIT)}{suffix}")
    return "\n".join(lines)


def _tweet_signal_impact_advice_text(posts: list[SocialPost], cross_ref: dict[int, dict]) -> str:
    lines = ["**影响和建议**"]
    impact_lines = _tweet_signal_impact_lines(posts, cross_ref)
    if impact_lines:
        lines.extend(impact_lines[:5])
    else:
        reason = _tweet_signal_primary_reason(posts)
        if reason:
            lines.append("影响: " + _trim_text(reason, 260))
        else:
            tickers = _common_values(post.mentioned_tickers for post in posts)
            topics = _common_values(post.topics for post in posts)
            targets = ", ".join(tickers[:6]) or " · ".join(topics[:4])
            if targets:
                lines.append(f"影响: 可能影响 {targets} 相关预期或风险偏好。")
            else:
                lines.append("影响: 市场影响暂不明确。")

    if _tweet_signal_has_personal_match(posts, cross_ref) and _tweet_signal_is_high_priority(posts):
        lines.append("强提醒: 与你的持仓/关注相关，先核验来源和量价反应，再决定是否调整交易计划。")
    elif _tweet_signal_is_high_priority(posts):
        lines.append("建议: 优先核验原始来源与后续价格、成交量反应，再决定是否行动。")
    else:
        lines.append("建议: 暂不形成明确操作信号，继续观察官方确认和市场反应。")
    return "\n".join(lines)


def _tweet_signal_reference_text(posts: list[SocialPost]) -> str:
    return _topic_reference_text(posts)


def _tweet_signal_card_template(posts: list[SocialPost]) -> str:
    if _tweet_signal_is_high_priority(posts):
        return "orange"
    if any(post.urgency == "medium" for post in posts):
        return "blue"
    return "green"


def _tweet_signal_is_high_priority(posts: list[SocialPost]) -> bool:
    return any(post.is_noteworthy or post.urgency == "high" for post in posts)


def _tweet_signal_cross_ref_matches(
    posts: list[SocialPost],
    cross_ref: dict[int, dict],
) -> list[str]:
    matches: list[str] = []
    for post in posts:
        if not post.id:
            continue
        match = cross_ref.get(post.id) or {}
        raw_items = match.get("items")
        items = raw_items if isinstance(raw_items, list) else []
        for item in items:
            if not isinstance(item, dict) or item.get("relation") == "default_watchlist":
                continue
            detail = str(item.get("detail") or "").strip()
            if detail:
                matches.append(detail)
        if items:
            continue
        value = str(match.get("matches") or "").strip()
        if value:
            matches.append(value)
    return _unique_compact_texts(matches, limit=12)


def _tweet_signal_impact_lines(
    posts: list[SocialPost],
    cross_ref: dict[int, dict],
) -> list[str]:
    lines: list[str] = []
    for post in posts:
        if not post.id:
            continue
        match = cross_ref.get(post.id) or {}
        for line in match.get("impact_lines") or []:
            lines.append(str(line))
    return _unique_compact_texts(lines, limit=8)


def _tweet_signal_has_personal_match(posts: list[SocialPost], cross_ref: dict[int, dict]) -> bool:
    return bool(_tweet_signal_cross_ref_matches(posts, cross_ref))


def _tweet_signal_primary_reason(posts: list[SocialPost]) -> str:
    for post in posts:
        if post.attention_reason:
            return post.attention_reason
    for post in posts:
        reason = _tweet_signal_stored_urgency_reason(post)
        if reason:
            return reason
    return ""


def _tweet_signal_stored_urgency_reason(post: SocialPost) -> str:
    raw = post.raw_json if isinstance(post.raw_json, dict) else {}
    analysis = raw.get("reveal_analysis")
    if not isinstance(analysis, dict):
        return ""
    return str(analysis.get("urgency_reason") or "")


def _unique_compact_texts(values: Sequence[str | None], limit: int) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
        if len(results) >= limit:
            break
    return results


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
    if fallback := posts[-1].summary or posts[-1].content:
        return _trim_text(fallback, 80)
    if link := _topic_card_link(posts):
        return _display_url(link)
    return "相关更新"


def _common_values(value_lists) -> list[str]:
    counter: Counter[str] = Counter()
    for values in value_lists:
        if not values:
            continue
        for value in values:
            if isinstance(value, str) and value.strip():
                counter[value.strip()] += 1
    return [value for value, _count in counter.most_common()]


def _md_div(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _note_text(content: str) -> dict[str, Any]:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


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
            all_tickers.update(
                ticker
                for ticker in (_normalize_cross_ref_ticker(t) for t in post.mentioned_tickers)
                if ticker
            )
    if not all_tickers:
        return {}

    held_tickers: set[str] = set()
    position_info: dict[str, str] = {}
    try:
        from server.journal.service import get_trades_for_period

        trades = await get_trades_for_period("all")
        for trade in trades:
            if trade.exit_price is None:
                ticker = _normalize_cross_ref_ticker(trade.ticker)
                if not ticker:
                    continue
                held_tickers.add(ticker)

                unrealized = (0.0 - trade.entry_price) * trade.quantity
                try:
                    from server.stock.data import get_current_price

                    price = await get_current_price(ticker)
                    if price:
                        unrealized = (price - trade.entry_price) * trade.quantity
                        if trade.direction == "short":
                            unrealized = -unrealized
                except Exception:
                    logger.exception("Cross-reference position price fetch failed for {}", ticker)
                position_info[ticker] = (
                    f"{ticker} (持有 {trade.quantity} 股, 浮盈 ${unrealized:+,.0f})"
                )
    except Exception:
        logger.exception("Cross-reference portfolio fetch failed")

    marker_tickers: set[str] = set()
    try:
        from server.portfolio.markers import get_portfolio_holding_marker_tickers

        marker_tickers = {
            ticker
            for ticker in (
                _normalize_cross_ref_ticker(t) for t in await get_portfolio_holding_marker_tickers()
            )
            if ticker
        }
        for ticker in marker_tickers - held_tickers:
            position_info[ticker] = f"{ticker} (持仓关注标记，未记录数量/成本)"
    except Exception:
        logger.exception("Cross-reference portfolio marker fetch failed")

    tracked_tickers: set[str] = set()
    try:
        from server.stock.tracker import get_active_tickers

        tracked_tickers = {
            ticker
            for ticker in (_normalize_cross_ref_ticker(t) for t in await get_active_tickers())
            if ticker
        }
    except Exception:
        logger.exception("Cross-reference tracked tickers fetch failed")

    manual_watchlist: set[str] = set()
    try:
        from server.stock.watchlist import get_manual_stock_watch_tickers

        manual_watchlist = {
            ticker
            for ticker in (
                _normalize_cross_ref_ticker(t) for t in await get_manual_stock_watch_tickers()
            )
            if ticker
        }
    except Exception:
        logger.exception("Cross-reference manual watchlist fetch failed")

    watchlist: set[str] = set()
    try:
        from server.stock.scanner import DEFAULT_WATCHLIST

        watchlist = {
            ticker
            for ticker in (_normalize_cross_ref_ticker(t) for t in DEFAULT_WATCHLIST)
            if ticker
        }
    except Exception:
        logger.exception("Cross-reference default watchlist fetch failed")

    user_tickers = held_tickers | marker_tickers | tracked_tickers | manual_watchlist | watchlist

    result: dict[int, dict] = {}
    for post in posts:
        if not post.mentioned_tickers or not post.id:
            continue
        post_tickers = {
            ticker
            for ticker in (_normalize_cross_ref_ticker(t) for t in post.mentioned_tickers)
            if ticker
        }
        overlap = post_tickers & user_tickers
        if not overlap:
            continue
        parts: list[str] = []
        items: list[dict[str, str]] = []
        for ticker in sorted(overlap):
            if ticker in position_info:
                detail = position_info[ticker]
                item = {
                    "ticker": ticker,
                    "relation": "holding",
                    "label": "持仓",
                    "detail": detail,
                }
                parts.append(detail)
            elif ticker in tracked_tickers:
                item = {
                    "ticker": ticker,
                    "relation": "tracking",
                    "label": "追踪中",
                    "detail": f"{ticker} (追踪中)",
                }
                parts.append(item["detail"])
            elif ticker in manual_watchlist:
                item = {
                    "ticker": ticker,
                    "relation": "watchlist",
                    "label": "观察列表",
                    "detail": f"{ticker} (观察列表)",
                }
                parts.append(item["detail"])
            else:
                item = {
                    "ticker": ticker,
                    "relation": "default_watchlist",
                    "label": "默认池",
                    "detail": f"{ticker} (默认池)",
                }
                parts.append(item["detail"])
            items.append(item)

        impact_items = [item for item in items if item["relation"] != "default_watchlist"]
        result[post.id] = {
            "matches": ", ".join(parts[:5]),
            "items": items,
            "impact_lines": build_personal_impact_lines(post, impact_items),
        }
    return result


def _normalize_cross_ref_ticker(value: object) -> str:
    ticker = str(value or "").strip().upper().lstrip("$")
    return ticker if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker) else ""


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


async def _bind_sent_messages(
    sent_messages: list[tuple[str, str]],
    post_id: int | None,
    source_type: str = "twitter",
) -> None:
    if not sent_messages or post_id is None:
        return
    from server.bot.bindings import bind_message_to_source

    for chat_id, message_id in sent_messages:
        await bind_message_to_source(chat_id, message_id, source_type, post_id)


def _social_post_source_type(post: SocialPost) -> str:
    if str(post.tweet_id or "").startswith("reddit:"):
        return "reddit"
    return "twitter"


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
