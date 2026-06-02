"""Direct X GraphQL timeline client based on BetterTwitFix's parsing approach."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any

import httpx
from loguru import logger

USER_BY_SCREEN_NAME_QUERY_ID = "IGgvgiOx4QZndDHuD3x9TQ"
USER_TWEETS_QUERY_ID = "PNd0vlufvrcIwrAnBYKE9g"
V2_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
REQUEST_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0"
)

USER_BY_SCREEN_NAME_FEATURES = {
    "rweb_xchat_enabled": False,
    "hidden_profile_subscriptions_enabled": True,
    "payments_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

USER_TWEETS_FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": False,
    "responsive_web_grok_share_attachment_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": True,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


async def fetch_user_tweets_graphql(
    username: str,
    auth_tokens: list[str],
    count: int = 20,
    cursor: str | None = None,
) -> dict | None:
    """Fetch a user timeline page directly from X GraphQL using auth_token cookies."""
    tokens = [token.strip() for token in auth_tokens if token.strip()]
    if not tokens:
        return None
    random.shuffle(tokens)

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for auth_token in tokens:
            try:
                user = await _fetch_user(client, username, auth_token)
                page = await _fetch_user_feed(
                    client,
                    user["id"],
                    auth_token,
                    count=count,
                    cursor=cursor,
                )
                user["latest_tweets"] = [_tweet_to_vx_shape(tweet) for tweet in page["tweets"]]
                user["history_cursor"] = page.get("bottom_cursor")
                user["source"] = "x_graphql"
                return user
            except Exception as e:
                logger.debug(f"X GraphQL fallback failed for @{username}: {e}")
                continue
    return None


async def _fetch_user(client: httpx.AsyncClient, username: str, auth_token: str) -> dict:
    response = await client.get(
        f"https://x.com/i/api/graphql/{USER_BY_SCREEN_NAME_QUERY_ID}/UserByScreenName",
        params={
            "variables": _json_param(
                {"screen_name": username.strip().lstrip("@"), "withGrokTranslatedBio": False}
            ),
            "features": _json_param(USER_BY_SCREEN_NAME_FEATURES),
            "fieldToggles": _json_param({"withAuxiliaryUserLabels": True}),
        },
        headers=_auth_headers(auth_token),
    )
    response.raise_for_status()
    payload = response.json()
    _raise_graphql_error(payload)
    result = payload["data"]["user"]["result"]
    if result.get("__typename") == "UserUnavailable":
        raise ValueError(result.get("message") or "user unavailable")
    return _user_to_vx_shape(result)


async def _fetch_user_feed(
    client: httpx.AsyncClient,
    user_id: int,
    auth_token: str,
    count: int,
    cursor: str | None = None,
) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "userId": str(user_id),
        "count": count,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor

    response = await client.get(
        f"https://x.com/i/api/graphql/{USER_TWEETS_QUERY_ID}/UserTweets",
        params={
            "variables": _json_param(variables),
            "features": _json_param(USER_TWEETS_FEATURES),
            "fieldToggles": _json_param({"withArticlePlainText": False}),
        },
        headers=_auth_headers(auth_token),
    )
    response.raise_for_status()
    payload = response.json()
    _raise_graphql_error(payload)
    instructions = payload["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
    tweets: list[dict] = []
    bottom_cursor = None
    for instruction in instructions:
        if instruction.get("type") != "TimelineAddEntries":
            continue
        for entry in instruction.get("entries", []):
            if entry_cursor := _timeline_cursor(entry):
                if entry_cursor["type"].lower() == "bottom":
                    bottom_cursor = entry_cursor["value"]
                continue
            if not str(entry.get("entryId", "")).startswith("tweet-"):
                continue
            result = (
                entry.get("content", {})
                .get("itemContent", {})
                .get("tweet_results", {})
                .get("result")
            )
            result = _unwrap_tweet_result(result)
            if result and result.get("legacy"):
                tweets.append(result)
    return {"tweets": tweets, "bottom_cursor": bottom_cursor}


def _timeline_cursor(entry: dict[str, Any]) -> dict[str, str] | None:
    content = entry.get("content")
    if not isinstance(content, dict):
        return None
    if content.get("entryType") != "TimelineTimelineCursor":
        return None
    cursor_type = content.get("cursorType")
    value = content.get("value")
    if not cursor_type or not value:
        return None
    return {"type": str(cursor_type), "value": str(value)}


def _user_to_vx_shape(user: dict) -> dict:
    legacy = user.get("legacy", {})
    core = user.get("core", {})
    return {
        "id": int(user["rest_id"]),
        "screen_name": core.get("screen_name") or legacy.get("screen_name"),
        "name": core.get("name") or legacy.get("name"),
        "profile_image_url": user.get("avatar", {}).get("image_url")
        or legacy.get("profile_image_url_https"),
        "description": legacy.get("description") or "",
        "location": user.get("location", {}).get("location") or legacy.get("location") or "",
        "followers_count": legacy.get("followers_count") or 0,
        "following_count": legacy.get("friends_count") or 0,
        "tweet_count": legacy.get("statuses_count") or 0,
        "created_at": core.get("created_at") or legacy.get("created_at"),
        "protected": user.get("privacy", {}).get("protected") or legacy.get("protected") or False,
        "fetched_on": int(datetime.now(UTC).timestamp()),
    }


def _tweet_to_vx_shape(tweet: dict) -> dict:
    legacy = tweet.get("legacy", {})
    user = _tweet_user(tweet)
    user_legacy = user.get("legacy", {})
    screen_name = (
        user.get("core", {}).get("screen_name")
        or user_legacy.get("screen_name")
        or legacy.get("user", {}).get("screen_name")
    )
    tweet_id = str(tweet.get("rest_id") or legacy.get("id_str") or legacy.get("id") or "")
    media_extended = _tweet_media(legacy)
    return {
        "tweetID": tweet_id,
        "conversationID": str(legacy.get("conversation_id_str") or ""),
        "text": _tweet_text(legacy),
        "date": legacy.get("created_at"),
        "date_epoch": _parse_twitter_date(legacy.get("created_at")),
        "likes": legacy.get("favorite_count"),
        "retweets": legacy.get("retweet_count"),
        "replies": legacy.get("reply_count"),
        "author_name": user.get("core", {}).get("name") or user_legacy.get("name"),
        "user_screen_name": screen_name,
        "screen_name": screen_name,
        "tweetURL": (
            f"https://x.com/{screen_name}/status/{tweet_id}" if screen_name and tweet_id else None
        ),
        "media_extended": media_extended,
        "mediaURLs": [item["url"] for item in media_extended if item.get("url")],
        "qrtURL": _quote_url(legacy),
        "retweetURL": _retweet_url(legacy),
        "replyingToID": legacy.get("in_reply_to_status_id_str"),
        "replyingTo": legacy.get("in_reply_to_screen_name"),
    }


def _tweet_user(tweet: dict) -> dict:
    core = tweet.get("core", {})
    return (
        core.get("user_result", {}).get("result")
        or core.get("user_results", {}).get("result")
        or {}
    )


def _tweet_text(legacy: dict) -> str:
    note_text = (
        legacy.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {}).get("text")
    )
    return note_text or legacy.get("full_text") or legacy.get("text") or ""


def _tweet_media(legacy: dict) -> list[dict]:
    entities = legacy.get("extended_entities") or legacy.get("entities") or {}
    media = entities.get("media") or []
    output: list[dict] = []
    for item in media:
        media_type = item.get("type") or "image"
        if media_type in {"video", "animated_gif"}:
            variants = item.get("video_info", {}).get("variants") or []
            url = _best_video_url(variants) or item.get("media_url_https")
            output.append(
                {
                    "url": url,
                    "type": "gif" if media_type == "animated_gif" else "video",
                    "preview_url": item.get("media_url_https"),
                    "alt_text": item.get("ext_alt_text"),
                }
            )
        else:
            output.append(
                {
                    "url": item.get("media_url_https"),
                    "type": "image",
                    "alt_text": item.get("ext_alt_text"),
                }
            )
    return [item for item in output if item.get("url")]


def _best_video_url(variants: list[dict]) -> str | None:
    candidates: list[tuple[int, str]] = []
    for variant in variants:
        url = variant.get("url")
        if not url or variant.get("content_type") != "video/mp4":
            continue
        if "/hevc/" in url:
            continue
        candidates.append((int(variant.get("bitrate") or 0), str(url).split("?tag=", 1)[0]))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _quote_url(legacy: dict) -> str | None:
    permalink = legacy.get("quoted_status_permalink") or {}
    return permalink.get("expanded") or permalink.get("url")


def _retweet_url(legacy: dict) -> str | None:
    retweeted = legacy.get("retweeted_status_result", {}).get("result")
    retweeted = _unwrap_tweet_result(retweeted)
    if not retweeted:
        return None
    retweeted_legacy = retweeted.get("legacy", {})
    user = _tweet_user(retweeted)
    screen_name = user.get("core", {}).get("screen_name") or user.get("legacy", {}).get(
        "screen_name"
    )
    tweet_id = retweeted.get("rest_id") or retweeted_legacy.get("id_str")
    if not screen_name or not tweet_id:
        return None
    return f"https://x.com/{screen_name}/status/{tweet_id}"


def _unwrap_tweet_result(result: dict | None) -> dict | None:
    if not isinstance(result, dict):
        return None
    if result.get("__typename") == "TweetWithVisibilityResults":
        return result.get("tweet")
    if result.get("__typename") == "TweetUnavailable":
        return None
    return result


def _parse_twitter_date(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y").timestamp())
    except ValueError:
        return 0


def _auth_headers(auth_token: str) -> dict[str, str]:
    csrf = _csrf_from_auth_token(auth_token)
    return {
        "Authorization": V2_BEARER,
        "Cookie": f"auth_token={auth_token}; ct0={csrf};",
        "x-csrf-token": csrf,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "User-Agent": REQUEST_USER_AGENT,
    }


def _csrf_from_auth_token(auth_token: str) -> str:
    token = "".join(ch for ch in auth_token if ch.isalnum())
    return (token[:32] or "reveal").ljust(32, "0")


def _json_param(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


def _raise_graphql_error(payload: dict) -> None:
    if errors := payload.get("errors"):
        first = errors[0]
        raise ValueError(f"{first.get('code')}: {first.get('message')}")
