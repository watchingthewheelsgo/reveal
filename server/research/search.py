"""Search provider adapters for deep research."""

from dataclasses import dataclass
from typing import Protocol

import httpx

from config.settings import get_settings


@dataclass
class SearchResult:
    query: str
    title: str
    url: str
    snippet: str = ""


class SearchProvider(Protocol):
    async def search(self, query: str, max_results: int) -> list[SearchResult]: ...


class NullSearchProvider:
    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        return []


class SearXNGSearchProvider:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general",
                },
            )
            response.raise_for_status()
            data = response.json()

        results: list[SearchResult] = []
        for item in data.get("results", [])[:max_results]:
            url = item.get("url")
            title = item.get("title")
            if not url or not title:
                continue
            results.append(
                SearchResult(
                    query=query,
                    title=title,
                    url=url,
                    snippet=item.get("content") or item.get("snippet") or "",
                )
            )
        return results


class BraveSearchProvider:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(max_results, 20)},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
            )
            response.raise_for_status()
            data = response.json()

        results: list[SearchResult] = []
        for item in data.get("web", {}).get("results", []):
            url = item.get("url")
            title = item.get("title")
            if not url or not title:
                continue
            results.append(
                SearchResult(
                    query=query,
                    title=title,
                    url=url,
                    snippet=item.get("description", ""),
                )
            )
        return results


def get_search_provider() -> SearchProvider:
    settings = get_settings()
    if settings.search_provider == "searxng" and settings.is_search_configured():
        return SearXNGSearchProvider(settings.searxng_base_url)
    if settings.search_provider == "brave" and settings.is_search_configured():
        return BraveSearchProvider(settings.brave_search_api_key)
    return NullSearchProvider()
