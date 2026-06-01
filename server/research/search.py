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


class GoogleSearchProvider:
    def __init__(self, api_key: str, engine_id: str):
        self.api_key = api_key
        self.engine_id = engine_id

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": self.api_key,
                    "cx": self.engine_id,
                    "q": query,
                    "num": min(max_results, 10),
                },
            )
            response.raise_for_status()
            data = response.json()

        results: list[SearchResult] = []
        for item in data.get("items", []):
            url = item.get("link")
            title = item.get("title")
            if not url or not title:
                continue
            results.append(
                SearchResult(
                    query=query,
                    title=title,
                    url=url,
                    snippet=item.get("snippet", ""),
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
    if settings.search_provider == "google" and settings.is_search_configured():
        return GoogleSearchProvider(
            settings.google_search_api_key, settings.google_search_engine_id
        )
    if settings.search_provider == "brave" and settings.is_search_configured():
        return BraveSearchProvider(settings.brave_search_api_key)
    return NullSearchProvider()
