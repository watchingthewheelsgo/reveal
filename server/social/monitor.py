"""Twitter/X monitor using vxTwitter API with persistence and LLM integration."""

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from loguru import logger

from server.bot.base import BotAdapter
from server.db.engine import get_session_factory
from server.db.models import SocialPost, TwitterState

TWITTER_STATUS_URL_RE = re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/([^/\s]+)/status/(\d+)")
URL_RE = re.compile(r"https?://[^\s<>)\]]+")
MAX_PUSHED_MEDIA = 4
INITIAL_WATCH_BACKFILL_LIMIT = 10
DIGEST_PREVIEW_LIMIT = 5


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
            screen_name = data.get("screen_name") or username
            for tweet in data["latest_tweets"]:
                _enrich_tweet(tweet, screen_name, data)

            if cache_control := resp.headers.get("cache-control"):
                if max_age := _parse_cache_max_age(cache_control):
                    data["max_age"] = max_age

            return data
    except Exception as e:
        logger.debug(f"vxTwitter fetch error for @{username}: {e}")
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
    except Exception as e:
        logger.debug(f"vxTwitter fetch error for @{username}/{tweet_id}: {e}")
        return None

    if not data:
        return None
    _enrich_tweet(data, data.get("screen_name") or username, data)
    return data


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

    account_key = username.strip().lstrip("@")
    display_username = data.get("screen_name") or account_key
    session_factory = get_session_factory()
    posts_to_push = []
    max_seen_epoch = 0
    last_epoch = 0
    first_check = False

    async with session_factory() as session:
        from sqlalchemy import select

        # Get last seen epoch
        result = await session.execute(
            select(TwitterState).where(TwitterState.username == account_key)
        )
        state = result.scalar_one_or_none()

        last_epoch = state.last_tweet_epoch if state else 0
        max_seen_epoch = last_epoch
        if last_epoch <= 0:
            first_check = True
            tweets = tweets[-INITIAL_WATCH_BACKFILL_LIMIT:]
            logger.info(
                f"@{account_key}: first watch check; backfilling up to "
                f"{INITIAL_WATCH_BACKFILL_LIMIT} tweets"
            )

        for tweet in tweets:
            _enrich_tweet(tweet, display_username, data)
            tweet_epoch = tweet.get("date_epoch", 0)
            tweet_id = _tweet_id(tweet)
            if not tweet_id:
                logger.warning(f"@{account_key}: skipped tweet with missing id: {tweet}")
                continue

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

            content = tweet.get("text") or ""
            tweet_url = _tweet_url(display_username, tweet)
            media = _tweet_media(tweet)
            references = await _hydrate_references(_referenced_tweets(display_username, tweet))
            links = _tweet_links(tweet, tweet_url, media, references)

            # Run LLM structured analysis
            translated = None
            summary = None
            post_tickers: list[str] | None = None
            post_topics: list[str] | None = None
            post_sentiment: str | None = None
            post_urgency: str | None = None
            llm_context = _llm_context(content, tweet_url, links, references)
            if llm_processor and llm_context and not first_check:
                try:
                    analysis = await llm_processor.analyze(llm_context, display_username)
                    if analysis:
                        summary = analysis.summary
                        translated = analysis.translation
                        post_tickers = analysis.mentioned_tickers or None
                        post_topics = analysis.topics or None
                        post_sentiment = analysis.sentiment
                        post_urgency = analysis.urgency
                except Exception as e:
                    logger.warning(f"LLM processing failed for @{account_key}: {e}")

            post = SocialPost(
                username=display_username,
                tweet_id=tweet_id,
                tweet_url=tweet_url,
                content=content,
                translated_content=translated,
                summary=summary,
                media=media,
                links=links,
                referenced_tweets=references,
                raw_json=tweet,
                is_reply=bool(tweet.get("is_reply")),
                is_repost=bool(tweet.get("is_repost")),
                is_quote=bool(tweet.get("is_quote")),
                mentioned_tickers=post_tickers,
                topics=post_topics,
                sentiment=post_sentiment,
                urgency=post_urgency,
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
        try:
            await push_tweet_digest(posts_to_push, adapter, is_backfill=first_check)
            successful_tweet_ids = [post.tweet_id for post in posts_to_push]
        except Exception as e:
            push_failed = True
            logger.warning(f"Tweet digest push failed for @{account_key}: {e}")
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
            select(TwitterState).where(TwitterState.username == account_key)
        )
        state = result.scalar_one_or_none()
        next_epoch = last_epoch if push_failed else max_seen_epoch
        if state:
            state.last_tweet_epoch = max(state.last_tweet_epoch, next_epoch)
            state.last_check_at = datetime.now(UTC)
        else:
            session.add(
                TwitterState(
                    username=account_key,
                    last_tweet_epoch=next_epoch,
                    last_check_at=datetime.now(UTC),
                )
            )
        await session.commit()

    if posts_to_push:
        logger.info(f"@{account_key}: {len(successful_tweet_ids)} tweets notified")

    return posts_to_push


async def push_tweet_digest(
    posts: list[SocialPost],
    adapter: BotAdapter,
    is_backfill: bool = False,
) -> None:
    """Push a compact digest for a batch of stored tweets."""
    if not posts:
        return

    cross_ref = await _cross_reference_posts(posts) if not is_backfill else {}
    ordered_posts = sorted(posts, key=lambda post: post.posted_at)
    latest_posts = list(reversed(ordered_posts))[:DIGEST_PREVIEW_LIMIT]
    username = ordered_posts[-1].username

    has_high = any(post.urgency == "high" for post in latest_posts)
    icon = "🔴" if has_high else "🐦"
    title = (
        f"🐦 @{username} 已缓存最近 {len(posts)} 条更新"
        if is_backfill
        else f"{icon} @{username} 有 {len(posts)} 条新更新"
    )
    sections: list[str] = []
    if len(posts) > DIGEST_PREVIEW_LIMIT:
        sections.append(f"显示最近 {DIGEST_PREVIEW_LIMIT} 条，其余已入库。")

    for index, post in enumerate(latest_posts, start=1):
        sections.append(_format_digest_section(index, post, cross_ref.get(post.id)))

    footer = "完整正文、引用、外链和媒体已缓存到 Reveal；飞书消息就是即时研究入口。"
    card = _build_research_card(title=title, sections=sections, footer=footer)
    await _push_card_to_admin(adapter, card)


async def push_tweet(post, adapter: BotAdapter):
    """Push a single tweet as a formatted message."""
    flags = _post_type_label(post)
    lines = [
        f"🐦 *@{post.username}* — {_time_ago(post.posted_at)}{flags}",
        "",
        post.content[:500] if post.content else "（无正文）",
    ]
    if post.tweet_url:
        lines.append("")
        lines.append(f"原文: {post.tweet_url}")
    if post.translated_content:
        lines.append("")
        lines.append(f"🌐 {post.translated_content[:300]}")
    if post.summary:
        lines.append("")
        lines.append(f"📝 摘要: {post.summary[:300]}")
    if post.referenced_tweets:
        lines.append("")
        lines.extend(_format_reference_lines(post.referenced_tweets))
    if post.media:
        lines.append("")
        lines.extend(_format_media_lines(post.media))
    if post.links:
        lines.append("")
        lines.extend(_format_link_lines(post.links))
    if post.id:
        lines.append("")
        lines.extend(_format_action_lines(post.id))

    text = "\n".join(lines)

    await _push_to_admin(adapter, text)


async def run_twitter_monitor(
    usernames: list[str],
    adapter: BotAdapter | None = None,
    llm_processor=None,
) -> int:
    """Run one round of Twitter monitoring across all configured users."""
    if not usernames:
        logger.debug("No Twitter accounts configured")
        return 0

    logger.info(f"Checking {len(usernames)} Twitter accounts...")
    tasks = [check_and_notify(u, adapter, llm_processor) for u in usernames]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total = 0
    for r in results:
        if isinstance(r, list):
            total += len(r)
    logger.info(f"Twitter monitor: {total} new tweets across {len(usernames)} accounts")
    return total


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


def _tweet_url(username: str, tweet: dict[str, Any]) -> str | None:
    raw_url = tweet.get("tweetURL") or tweet.get("url")
    if raw_url:
        return _normalize_x_url(str(raw_url))
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
        if isinstance(item, str):
            url = item
            media_type = None
            alt_text = None
        elif isinstance(item, dict):
            url = (
                item.get("url")
                or item.get("media_url_https")
                or item.get("preview_image_url")
                or item.get("src")
            )
            media_type = item.get("type") or item.get("format")
            alt_text = item.get("altText") or item.get("alt_text")
        else:
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        media.append(
            {
                "url": str(url),
                "type": media_type,
                "alt_text": alt_text,
            }
        )
    return media


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
    normalized_url = _normalize_x_url(url)
    match = TWITTER_STATUS_URL_RE.match(normalized_url)
    if not match:
        return {"type": reference_type, "url": normalized_url}
    return {
        "type": reference_type,
        "username": match.group(1),
        "tweet_id": match.group(2),
        "url": normalized_url,
    }


async def _hydrate_references(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    url = _normalize_x_url(str(raw_url).rstrip(".,;:"))
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


def _format_action_lines(post_id: int) -> list[str]:
    return [
        f"消息 ID: {post_id}",
        "继续操作:",
        f"- /research {post_id} 建立研究话题",
        f"- /deep {post_id} 让 Agent 主动深挖",
        f"- /ask {post_id} 你的问题",
    ]


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
                    pass
                position_info[trade.ticker] = (
                    f"{trade.ticker} (持有 {trade.quantity} 股, 浮盈 ${unrealized:+,.0f})"
                )
    except Exception:
        pass

    tracked_tickers: set[str] = set()
    try:
        from server.stock.tracker import get_active_tickers

        tracked_tickers = set(await get_active_tickers())
    except Exception:
        pass

    from server.stock.scanner import DEFAULT_WATCHLIST

    watchlist = set(DEFAULT_WATCHLIST)
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


async def _push_card_to_admin(adapter: BotAdapter, card: dict) -> None:
    try:
        chat_ids = _admin_chat_ids(adapter)
        if chat_ids:
            for chat_id in chat_ids:
                await adapter.send_card(chat_id, card)
            return
    except Exception as e:
        logger.warning(f"Card push failed, falling back to text: {e}")
    await _push_to_admin(adapter, _card_to_text(card))


async def _push_to_admin(adapter: BotAdapter, text: str) -> None:
    try:
        admin_chat_id = getattr(adapter, "admin_chat_id", None)
        if admin_chat_id:
            await adapter.send_message(admin_chat_id, text)
        else:
            await adapter.push_to_admin(text)
    except Exception:
        await adapter.push_to_admin(text)


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
    header = f"{index}. {urgency_icon}#{post.id} {_time_ago(post.posted_at)}{flags}"

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
    if post.id:
        lines.append(_format_research_actions(post.id))
    return "\n".join(lines)


def _format_research_actions(post_id: int) -> str:
    return f"操作: /research {post_id} 建话题 | /deep {post_id} 深挖 | /ask {post_id} 你的问题"


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


def _normalize_x_url(url: str) -> str:
    return url.replace("https://twitter.com/", "https://x.com/").replace(
        "http://twitter.com/", "https://x.com/"
    )


def _parse_cache_max_age(cache_control: str) -> float | None:
    match = re.search(r"max-age=(\d+(?:\.\d+)?)", cache_control)
    return float(match.group(1)) if match else None
