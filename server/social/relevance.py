"""Agent relevance and exact-source story grouping for social posts."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from server.db.models import SocialPost
from server.social.urls import normalize_x_url

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
                }
            )
            continue
        matched_group["posts"].append(post)
        matched_group["keys"].update(fingerprint["keys"])

    grouped = [group["posts"] for group in groups]
    return sorted(grouped, key=lambda group: group[-1].posted_at)


def is_similar_story(fingerprint: dict[str, set[str]], group: dict[str, Any]) -> bool:
    keys = fingerprint["keys"]
    return bool(keys and keys & group["keys"])


def story_fingerprint(post: SocialPost) -> dict[str, set[str]]:
    keys: set[str] = set()

    if canonical_event_id := agent_canonical_event_id(post):
        if is_specific_canonical_event_id(canonical_event_id):
            keys.add(f"agent-event:{canonical_event_id}")

    for link in post.links or []:
        canonical_link = canonical_story_url(str(link))
        if canonical_link and is_groupable_story_link(canonical_link):
            keys.add(f"link:{canonical_link}")

    for reference in post.referenced_tweets or []:
        url = str(reference.get("url") or "")
        if url:
            keys.add(f"ref:{normalize_x_url(url)}")

    if not keys:
        keys.add(f"post:{post.tweet_id or post.id}")
    return {"keys": keys}


def agent_canonical_event_id(post: SocialPost) -> str:
    """Return the Agent-provided canonical event id used for story grouping."""

    analysis = _raw_agent_analysis(post) or {}
    event = analysis.get("canonical_event")
    if isinstance(event, dict):
        value = event.get("id")
    else:
        value = analysis.get("canonical_event_id")
    return normalize_agent_event_id(value)


def normalize_agent_event_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    normalized = "".join(ch if ch.isalnum() else "-" for ch in raw)
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:96]


def is_specific_canonical_event_id(value: str) -> bool:
    normalized = normalize_agent_event_id(value)
    parts = [part for part in normalized.split("-") if part]
    return len(parts) >= 3 and len(normalized) >= 12


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


def is_groupable_story_link(url: str) -> bool:
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return False
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    return host not in {"x.com", "twitter.com", "t.co"}


def is_x_url(url: str) -> bool:
    try:
        host = urlsplit(normalize_x_url(url)).netloc.lower()
    except ValueError:
        return False
    return host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}
