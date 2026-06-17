"""Reddit/subreddit monitor backed by PRAW and Reveal social events."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import praw
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select

from config.settings import get_settings
from server.bot.base import BotAdapter
from server.db.engine import get_session_factory
from server.db.models import RedditState, SocialPost
from server.db.time import to_naive_utc, utc_now_naive
from server.social.monitor import mark_tweet_posts_pushed, push_relevant_tweet_cards

INITIAL_REDDIT_BACKFILL_LIMIT = 10


class RedditPost(BaseModel):
    """Normalized Reddit listing item."""

    id: str
    subreddit: str
    title: str
    permalink: str
    url: str | None = None
    author: str | None = None
    selftext: str = ""
    score: int | None = None
    upvote_ratio: float | None = None
    num_comments: int | None = None
    created_utc: int
    flair: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def full_permalink(self) -> str:
        if self.permalink.startswith("http"):
            return self.permalink
        return f"https://www.reddit.com{self.permalink}"

    @property
    def content(self) -> str:
        text = self.selftext.strip()
        if text:
            return f"{self.title.strip()}\n\n{text}"
        return self.title.strip()


async def list_active_reddit_subreddits(
    configured_subreddits: list[str] | None = None,
) -> list[str]:
    configured_subreddits = [_normalize_subreddit(item) for item in configured_subreddits or []]
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(RedditState))
        states = result.scalars().all()

    disabled = {state.subreddit for state in states if not state.is_active}
    subreddits: list[str] = []
    seen: set[str] = set()

    for subreddit in configured_subreddits:
        if subreddit and subreddit not in disabled and subreddit not in seen:
            subreddits.append(subreddit)
            seen.add(subreddit)

    for state in states:
        if state.is_active and state.subreddit not in seen:
            subreddits.append(state.subreddit)
            seen.add(state.subreddit)

    return subreddits


async def set_reddit_subreddit_active(subreddit: str, is_active: bool) -> None:
    normalized = _normalize_subreddit(subreddit)
    if not normalized:
        raise ValueError("subreddit is required")

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(RedditState).where(RedditState.subreddit == normalized)
        )
        state = result.scalar_one_or_none()
        if state:
            state.is_active = is_active
        else:
            session.add(RedditState(subreddit=normalized, is_active=is_active))
        await session.commit()


async def fetch_subreddit_posts(
    subreddit: str,
    *,
    listing: str = "new",
    limit: int | None = None,
) -> list[RedditPost]:
    settings = get_settings()
    if not settings.is_reddit_configured():
        raise RuntimeError("Reddit is not configured")

    normalized = _normalize_subreddit(subreddit)
    if not normalized:
        raise ValueError("subreddit is required")

    limit = _clamp_limit(limit or settings.reddit_post_fetch_limit, max_limit=100)
    listing = _normalize_listing(listing)
    posts = await asyncio.to_thread(
        _fetch_subreddit_posts_sync,
        normalized,
        listing,
        limit,
        settings.reddit_client_id,
        settings.reddit_client_secret,
        settings.reddit_user_agent,
    )
    return sorted(posts, key=lambda post: post.created_utc)


async def check_subreddit_and_notify(
    subreddit: str,
    adapter: BotAdapter | None = None,
    llm_processor=None,
    notify_no_updates: bool = False,
    push_notifications: bool = True,
) -> list[SocialPost]:
    subreddit_key = _normalize_subreddit(subreddit)
    session_factory = get_session_factory()
    last_epoch = 0
    first_check = False

    async with session_factory() as session:
        result = await session.execute(
            select(RedditState).where(RedditState.subreddit == subreddit_key)
        )
        state = result.scalar_one_or_none()
        last_epoch = state.last_post_epoch if state else 0
        first_check = last_epoch <= 0
        last_check_at = state.last_check_at if state else None

    check_age = _seconds_since(last_check_at)
    min_interval = get_settings().reddit_fetch_min_interval
    if check_age is not None and check_age < min_interval:
        logger.info(
            "r/{}: skipped Reddit fetch due to cooldown age={:.0f}s min_interval={}s",
            subreddit_key,
            check_age,
            min_interval,
        )
        if adapter and notify_no_updates:
            await adapter.push_to_admin(
                f"r/{subreddit_key} {int(check_age)} 秒前刚检查过，已跳过拉取以避免触发限流。"
            )
        return []

    posts = await fetch_subreddit_posts(subreddit_key)
    push_candidate_ids = (
        {post.id for post in posts[-INITIAL_REDDIT_BACKFILL_LIMIT:]} if first_check else None
    )
    max_cached_epoch = last_epoch
    newest_cached_post_id: str | None = None
    posts_to_push: list[SocialPost] = []

    async with session_factory() as session:
        result = await session.execute(
            select(RedditState).where(RedditState.subreddit == subreddit_key)
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = RedditState(subreddit=subreddit_key)
            session.add(state)

        for reddit_post in posts:
            if reddit_post.created_utc > max_cached_epoch:
                max_cached_epoch = reddit_post.created_utc
                newest_cached_post_id = reddit_post.id

            push_candidate = reddit_post.created_utc > last_epoch and (
                push_candidate_ids is None or reddit_post.id in push_candidate_ids
            )
            should_analyze = bool(push_candidate and llm_processor)
            post = await _upsert_reddit_social_post(
                session,
                reddit_post,
                llm_processor=llm_processor,
                should_analyze=should_analyze,
            )
            if push_candidate and post and not post.is_pushed:
                posts_to_push.append(post)

        state.last_post_epoch = max(state.last_post_epoch or 0, max_cached_epoch)
        if newest_cached_post_id:
            state.newest_post_id = newest_cached_post_id
        state.last_check_at = utc_now_naive()
        await session.commit()

    if adapter and posts_to_push and push_notifications:
        pushed_posts = await push_relevant_tweet_cards(
            posts_to_push, adapter, llm_processor=llm_processor
        )
        await mark_tweet_posts_pushed(pushed_posts)
    elif adapter and notify_no_updates and not posts_to_push:
        await adapter.push_to_admin(f"r/{subreddit_key} 没有新的 Reddit 更新。")

    return posts_to_push


async def run_reddit_monitor(
    subreddits: list[str],
    adapter: BotAdapter | None = None,
    llm_processor=None,
    notify_no_updates: bool = False,
) -> int:
    normalized_subreddits = [
        subreddit for subreddit in (_normalize_subreddit(s) for s in subreddits) if subreddit
    ]
    if not normalized_subreddits:
        logger.debug("No Reddit subreddits configured")
        return 0

    logger.info("Checking {} Reddit subreddits...", len(normalized_subreddits))
    tasks = [
        check_subreddit_and_notify(
            subreddit,
            adapter,
            llm_processor,
            notify_no_updates=notify_no_updates,
            push_notifications=False,
        )
        for subreddit in normalized_subreddits
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    candidates: list[SocialPost] = []
    for subreddit, result in zip(normalized_subreddits, results, strict=False):
        if isinstance(result, BaseException):
            logger.opt(exception=result).error(
                "Reddit monitor subreddit check failed: subreddit={} exc_type={} error={}",
                subreddit,
                type(result).__name__,
                result,
            )
            continue
        candidates.extend(cast(list[SocialPost], result))

    pushed_posts: list[SocialPost] = []
    if adapter and candidates:
        pushed_posts = await push_relevant_tweet_cards(
            candidates, adapter, llm_processor=llm_processor
        )
        await mark_tweet_posts_pushed(pushed_posts)

    total = len(pushed_posts) if adapter else len(candidates)
    logger.info(
        "Reddit monitor: {} pushed from {} new candidates across {} subreddits",
        total,
        len(candidates),
        len(normalized_subreddits),
    )
    return total


async def cache_subreddit_posts(
    subreddit: str, count: int = 10, llm_processor=None
) -> list[SocialPost]:
    normalized = _normalize_subreddit(subreddit)
    reddit_posts = await fetch_subreddit_posts(normalized, limit=count)
    cached_posts: list[SocialPost] = []
    session_factory = get_session_factory()
    async with session_factory() as session:
        for reddit_post in reddit_posts:
            post = await _upsert_reddit_social_post(
                session,
                reddit_post,
                llm_processor=llm_processor,
                should_analyze=bool(llm_processor),
            )
            if post:
                cached_posts.append(post)
        await session.commit()
    return cached_posts


async def _upsert_reddit_social_post(
    session,
    reddit_post: RedditPost,
    llm_processor=None,
    should_analyze: bool = False,
) -> SocialPost | None:
    post_id = _social_post_id(reddit_post)
    existing = await session.execute(select(SocialPost).where(SocialPost.tweet_id == post_id))
    post = existing.scalar_one_or_none()

    content = reddit_post.content
    links = _reddit_links(reddit_post)
    raw = {
        **reddit_post.raw,
        "source": "reddit",
        "subreddit": reddit_post.subreddit,
        "reddit_id": reddit_post.id,
        "author": reddit_post.author,
        "score": reddit_post.score,
        "upvote_ratio": reddit_post.upvote_ratio,
        "num_comments": reddit_post.num_comments,
        "flair": reddit_post.flair,
        "permalink": reddit_post.full_permalink,
    }

    translated = post.translated_content if post else None
    summary = post.summary if post else None
    post_tickers = post.mentioned_tickers if post else None
    post_topics = post.topics if post else None
    post_sentiment = post.sentiment if post else None
    post_urgency = post.urgency if post else None
    post_is_noteworthy = bool(post.is_noteworthy) if post else False
    post_attention_reason = post.attention_reason if post else None

    if should_analyze and llm_processor and content and not summary:
        try:
            analysis = await llm_processor.analyze(
                _reddit_analysis_context(reddit_post),
                f"r/{reddit_post.subreddit}",
            )
            if analysis:
                raw["reveal_analysis"] = {
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
            logger.exception("LLM processing failed for r/{}", reddit_post.subreddit)

    fields = {
        "username": f"r/{reddit_post.subreddit}",
        "tweet_url": reddit_post.full_permalink,
        "content": content,
        "translated_content": translated,
        "summary": summary,
        "media": [],
        "links": links,
        "referenced_tweets": [],
        "raw_json": raw,
        "is_reply": False,
        "is_repost": False,
        "is_quote": False,
        "mentioned_tickers": post_tickers,
        "topics": post_topics,
        "sentiment": post_sentiment,
        "urgency": post_urgency,
        "is_noteworthy": post_is_noteworthy,
        "attention_reason": post_attention_reason,
        "posted_at": to_naive_utc(datetime.fromtimestamp(reddit_post.created_utc, tz=UTC)),
    }

    if post:
        for key, value in fields.items():
            setattr(post, key, value)
        return post

    post = SocialPost(tweet_id=post_id, is_pushed=False, **fields)
    session.add(post)
    return post


def _fetch_subreddit_posts_sync(
    subreddit: str,
    listing: str,
    limit: int,
    client_id: str,
    client_secret: str,
    user_agent: str,
) -> list[RedditPost]:
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )
    subreddit_ref = reddit.subreddit(subreddit)
    listing_method = getattr(subreddit_ref, listing)
    return [
        _post_from_submission(submission, subreddit) for submission in listing_method(limit=limit)
    ]


def _post_from_submission(submission: Any, fallback_subreddit: str) -> RedditPost:
    author = getattr(submission, "author", None)
    subreddit = getattr(getattr(submission, "subreddit", None), "display_name", None)
    created_utc = int(getattr(submission, "created_utc", 0) or 0)
    post_id = str(getattr(submission, "id", "") or "")
    permalink = str(getattr(submission, "permalink", "") or "")
    title = str(getattr(submission, "title", "") or "")
    return RedditPost(
        id=post_id,
        subreddit=_normalize_subreddit(str(subreddit or fallback_subreddit)),
        title=title,
        permalink=permalink,
        url=str(getattr(submission, "url", "") or "") or None,
        author=str(author) if author else None,
        selftext=str(getattr(submission, "selftext", "") or ""),
        score=_safe_int(getattr(submission, "score", None)),
        upvote_ratio=_safe_float(getattr(submission, "upvote_ratio", None)),
        num_comments=_safe_int(getattr(submission, "num_comments", None)),
        created_utc=created_utc,
        flair=str(getattr(submission, "link_flair_text", "") or "") or None,
        raw={
            "id": post_id,
            "subreddit": str(subreddit or fallback_subreddit),
            "title": title,
            "permalink": permalink,
            "url": str(getattr(submission, "url", "") or ""),
            "author": str(author) if author else None,
            "selftext": str(getattr(submission, "selftext", "") or ""),
            "score": getattr(submission, "score", None),
            "upvote_ratio": getattr(submission, "upvote_ratio", None),
            "num_comments": getattr(submission, "num_comments", None),
            "created_utc": created_utc,
            "link_flair_text": getattr(submission, "link_flair_text", None),
        },
    )


def _reddit_analysis_context(post: RedditPost) -> str:
    lines = [
        "Source: Reddit",
        f"Subreddit: r/{post.subreddit}",
        f"Author: u/{post.author}" if post.author else "Author: unknown",
        f"Score: {post.score}" if post.score is not None else "Score: unknown",
        f"Comments: {post.num_comments}" if post.num_comments is not None else "Comments: unknown",
    ]
    if post.upvote_ratio is not None:
        lines.append(f"Upvote ratio: {post.upvote_ratio}")
    if post.flair:
        lines.append(f"Flair: {post.flair}")
    lines.extend(
        [
            f"Permalink: {post.full_permalink}",
            "",
            "Title:",
            post.title,
        ]
    )
    if post.selftext.strip():
        lines.extend(["", "Body:", post.selftext.strip()])
    if post.url and post.url != post.full_permalink:
        lines.extend(["", f"External URL: {post.url}"])
    return "\n".join(lines)


def _reddit_links(post: RedditPost) -> list[str]:
    links = [post.full_permalink]
    if post.url and post.url not in links:
        links.append(post.url)
    return links


def _social_post_id(post: RedditPost) -> str:
    return f"reddit:{post.subreddit}:{post.id}"


def _normalize_subreddit(value: str) -> str:
    normalized = str(value or "").strip().strip("/")
    if normalized.lower().startswith("r/"):
        normalized = normalized[2:]
    return normalized.strip()


def _normalize_listing(value: str) -> str:
    normalized = value.lower().strip()
    if normalized not in {"new", "hot", "rising", "top"}:
        return "new"
    return normalized


def _clamp_limit(limit: int, default: int = 25, max_limit: int = 100) -> int:
    try:
        return max(1, min(max_limit, int(limit)))
    except (TypeError, ValueError):
        return default


def _seconds_since(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return (datetime.now(UTC) - dt.replace(tzinfo=UTC)).total_seconds()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
