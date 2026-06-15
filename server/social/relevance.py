"""Agent relevance and story grouping for social posts."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from server.db.models import SocialPost
from server.social.urls import normalize_x_url

SOCIAL_TOPIC_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "because",
        "before",
        "being",
        "could",
        "from",
        "have",
        "into",
        "just",
        "market",
        "markets",
        "more",
        "news",
        "over",
        "said",
        "says",
        "stock",
        "stocks",
        "than",
        "that",
        "their",
        "there",
        "this",
        "today",
        "trump",
        "were",
        "with",
        "would",
    }
)

TRACKING_QUERY_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "s",
    }
)


def is_relevant_social_post(post: SocialPost) -> bool:
    """Return whether Agent analysis judged the post market relevant."""

    return agent_market_relevance(post) is True


def agent_market_relevance(post: SocialPost) -> bool | None:
    """Return the persisted Agent market-relevance verdict.

    New rows store the explicit verdict in raw_json.reveal_analysis. Older rows
    did not have that field, so we infer only from Agent-derived structured
    fields, never from raw tweet text.
    """

    analysis = _raw_agent_analysis(post)
    if analysis and "is_market_relevant" in analysis:
        return bool(analysis.get("is_market_relevant"))

    if not _has_legacy_agent_analysis(post):
        return None

    return bool(post.is_noteworthy or post.mentioned_tickers)


def _raw_agent_analysis(post: SocialPost) -> dict[str, Any] | None:
    raw = post.raw_json if isinstance(post.raw_json, dict) else {}
    analysis = raw.get("reveal_analysis")
    return analysis if isinstance(analysis, dict) else None


def _has_legacy_agent_analysis(post: SocialPost) -> bool:
    return bool(
        post.summary
        or post.translated_content
        or post.mentioned_tickers
        or post.topics
        or post.sentiment
        or post.urgency
        or post.attention_reason
        or post.is_noteworthy
    )


def social_post_search_text(post: SocialPost) -> str:
    parts: list[str] = [
        post.content or "",
        post.summary or "",
        post.attention_reason or "",
        " ".join(str(topic) for topic in (post.topics or [])),
        " ".join(str(ticker) for ticker in (post.mentioned_tickers or [])),
    ]
    for reference in post.referenced_tweets or []:
        parts.append(str(reference.get("text") or ""))
        parts.append(str(reference.get("url") or ""))
    parts.extend(str(link) for link in (post.links or []))
    return "\n".join(part for part in parts if part)


def group_similar_social_posts(posts: list[SocialPost]) -> list[list[SocialPost]]:
    groups: list[dict[str, Any]] = []
    for post in sorted(posts, key=lambda item: item.posted_at):
        fingerprint = story_fingerprint(post)
        matched_group: dict[str, Any] | None = None
        for group in groups:
            if is_similar_story(fingerprint, group):
                matched_group = group
                break
        if matched_group is None:
            groups.append(
                {
                    "posts": [post],
                    "keys": set(fingerprint["keys"]),
                    "tokens": set(fingerprint["tokens"]),
                }
            )
            continue
        matched_group["posts"].append(post)
        matched_group["keys"].update(fingerprint["keys"])
        matched_group["tokens"].update(fingerprint["tokens"])

    grouped = [group["posts"] for group in groups]
    return sorted(grouped, key=lambda group: group[-1].posted_at)


def is_similar_story(fingerprint: dict[str, set[str]], group: dict[str, Any]) -> bool:
    keys = fingerprint["keys"]
    if keys and keys & group["keys"]:
        return True

    tokens = fingerprint["tokens"]
    group_tokens = group["tokens"]
    if not tokens or not group_tokens:
        return False

    overlap = tokens & group_tokens
    if len(overlap) >= 3:
        return True
    if len(overlap) >= 2:
        denominator = max(len(tokens | group_tokens), 1)
        return len(overlap) / denominator >= 0.4
    return False


def story_fingerprint(post: SocialPost) -> dict[str, set[str]]:
    keys: set[str] = set()
    tokens: set[str] = set()

    for link in post.links or []:
        canonical_link = canonical_story_url(str(link))
        if canonical_link:
            keys.add(f"link:{canonical_link}")
            tokens.update(tokenize_story_text(canonical_link.replace("/", " ")))

    for reference in post.referenced_tweets or []:
        url = str(reference.get("url") or "")
        if url:
            keys.add(f"ref:{normalize_x_url(url)}")
        if text := reference.get("text"):
            tokens.update(tokenize_story_text(str(text)))

    for ticker in post.mentioned_tickers or []:
        ticker_text = str(ticker).strip().upper()
        if ticker_text:
            tokens.add(f"ticker:{ticker_text}")

    for topic in post.topics or []:
        topic_text = str(topic).strip().lower()
        if topic_text:
            tokens.add(f"topic:{topic_text}")

    tokens.update(tokenize_story_text(social_post_search_text(post)))
    return {"keys": keys, "tokens": tokens}


def tokenize_story_text(text: str) -> set[str]:
    tokens: set[str] = set()

    for raw in re.findall(r"\$?[A-Za-z][A-Za-z0-9&.-]{2,}", text):
        token = raw.strip("$").strip(".-").lower()
        if len(token) < 3 or token in SOCIAL_TOPIC_STOPWORDS:
            continue
        tokens.add(token)

    for raw in re.findall(r"[\u4e00-\u9fff]{2,12}", text):
        tokens.update(_cjk_story_tokens(raw))
    return tokens


def _cjk_story_tokens(text: str) -> set[str]:
    if len(text) < 2:
        return set()
    if len(text) <= 6:
        return {text}
    tokens: set[str] = set()
    for size in (3, 4, 5):
        for index in range(0, len(text) - size + 1):
            tokens.add(text[index : index + size])
    return tokens


def canonical_story_url(url: str) -> str:
    normalized = normalize_x_url(url.rstrip(".,;:"))
    try:
        parts = urlsplit(normalized)
    except ValueError:
        return normalized
    if not parts.netloc:
        return normalized

    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/") or "/"
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        clean_key = key.lower()
        if clean_key.startswith("utm_") or clean_key in TRACKING_QUERY_PARAMS:
            continue
        query_items.append((key, value))
    query = urlencode(query_items)
    return urlunsplit((scheme, netloc, path, query, ""))


def is_x_url(url: str) -> bool:
    try:
        host = urlsplit(normalize_x_url(url)).netloc.lower()
    except ValueError:
        return False
    return host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}
