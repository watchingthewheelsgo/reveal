"""Small HTML text fetcher used by research synthesis."""

import re
from html.parser import HTMLParser

import httpx


class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._title_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title" and self._title_depth > 0:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._title_depth:
            self.title_parts.append(text)
        if self._skip_depth == 0:
            self.text_parts.append(text)

    @property
    def title(self) -> str:
        return _clean_text(" ".join(self.title_parts))

    @property
    def text(self) -> str:
        return _clean_text(" ".join(self.text_parts))


async def fetch_page_text(url: str, max_chars: int = 12000) -> tuple[str, str]:
    """Fetch a web page and return title + readable text best-effort."""
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={
                "User-Agent": "RevealResearch/0.1 (+https://github.com)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return "", ""
            html = response.text
    except Exception:
        return "", ""

    parser = HTMLTextExtractor()
    parser.feed(html)
    return parser.title, parser.text[:max_chars]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
