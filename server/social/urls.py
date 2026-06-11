"""URL helpers for X/Twitter social sources."""

from __future__ import annotations


def normalize_x_url(url: str) -> str:
    return url.replace("https://twitter.com/", "https://x.com/").replace(
        "http://twitter.com/", "https://x.com/"
    )
