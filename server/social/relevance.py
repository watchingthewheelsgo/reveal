"""Market relevance and story grouping for social posts."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from server.db.models import SocialPost
from server.social.urls import normalize_x_url

SOCIAL_RELEVANCE_KEYWORDS = frozenset(
    {
        "$",
        "10-k",
        "10-q",
        "after-hours",
        "ai capex",
        "attack",
        "bank",
        "banking",
        "bitcoin",
        "bond",
        "bonds",
        "btc",
        "buyback",
        "cbo",
        "ceasefire",
        "central bank",
        "ceo",
        "china",
        "congress",
        "cpi",
        "crypto",
        "defense",
        "department of defense",
        "dollar",
        "dow",
        "drone",
        "earnings",
        "economy",
        "election",
        "equities",
        "equity",
        "executive order",
        "export control",
        "fed",
        "fda",
        "fomc",
        "forex",
        "futures",
        "gaza",
        "gdp",
        "gold",
        "guidance",
        "inflation",
        "ipo",
        "iran",
        "israel",
        "jobs report",
        "market",
        "merger",
        "military",
        "missile",
        "nasdaq",
        "nato",
        "oil",
        "options",
        "opec",
        "palestine",
        "payrolls",
        "pce",
        "pentagon",
        "policy",
        "politics",
        "powell",
        "president",
        "ppi",
        "premarket",
        "rate cut",
        "rate hike",
        "recession",
        "regulation",
        "revenue",
        "russia",
        "s&p",
        "sanction",
        "sanctions",
        "sec",
        "senate",
        "shares",
        "sp500",
        "stock",
        "stocks",
        "strike",
        "supreme court",
        "taiwan",
        "tariff",
        "tariffs",
        "treasury",
        "trump",
        "ukraine",
        "unemployment",
        "vix",
        "war",
        "white house",
        "yield",
        "yields",
        "上证",
        "下跌",
        "个股",
        "中东",
        "以色列",
        "企业",
        "估值",
        "俄乌",
        "俄罗斯",
        "停火",
        "关税",
        "军事",
        "军工",
        "制裁",
        "加息",
        "北约",
        "升息",
        "原油",
        "台海",
        "国会",
        "国债",
        "国防",
        "地缘",
        "失业",
        "总统",
        "战争",
        "指数",
        "收益率",
        "政策",
        "政治",
        "无人机",
        "日本央行",
        "暴涨",
        "暴跌",
        "期权",
        "期货",
        "板块",
        "欧洲央行",
        "比特币",
        "油价",
        "法案",
        "港股",
        "白宫",
        "监管",
        "石油",
        "美国大选",
        "美债",
        "美军",
        "美股",
        "美元",
        "联储",
        "股价",
        "股市",
        "股票",
        "英伟达",
        "营收",
        "衰退",
        "议会",
        "财报",
        "财政",
        "贸易",
        "通胀",
        "选举",
        "道指",
        "金融",
        "降息",
        "非农",
        "韩国央行",
        "预期",
        "黄金",
    }
)

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
    if post.mentioned_tickers:
        return True

    search_text = social_post_search_text(post)
    if contains_relevance_keyword(search_text):
        return True

    if post.is_noteworthy or post.urgency == "high":
        topic_text = " ".join(str(topic) for topic in (post.topics or []))
        if contains_relevance_keyword(topic_text):
            return True

    return False


def contains_relevance_keyword(text: str) -> bool:
    normalized = text.lower()
    if re.search(r"\$[A-Z]{1,6}\b", text):
        return True
    return any(keyword_in_text(normalized, keyword) for keyword in SOCIAL_RELEVANCE_KEYWORDS)


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
    normalized = text.lower()
    for keyword in SOCIAL_RELEVANCE_KEYWORDS:
        if (
            len(keyword) >= 3
            and keyword_in_text(normalized, keyword)
            and keyword not in SOCIAL_TOPIC_STOPWORDS
        ):
            tokens.add(keyword)

    for raw in re.findall(r"\$?[A-Za-z][A-Za-z0-9&.-]{2,}", text):
        token = raw.strip("$").strip(".-").lower()
        if len(token) < 3 or token in SOCIAL_TOPIC_STOPWORDS:
            continue
        tokens.add(token)

    for raw in re.findall(r"[\u4e00-\u9fff]{2,8}", text):
        if raw in SOCIAL_RELEVANCE_KEYWORDS:
            tokens.add(raw)
    return tokens


def keyword_in_text(normalized_text: str, keyword: str) -> bool:
    if keyword == "$":
        return "$" in normalized_text
    if re.fullmatch(r"[a-z0-9&.-]+", keyword):
        pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None
    return keyword in normalized_text


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
