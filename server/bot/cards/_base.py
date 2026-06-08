"""Shared card helpers.

The returned shape is intentionally adapter-neutral and compatible with the
existing Feishu adapter normalizer.
"""

from __future__ import annotations

from server.bot.feishu_markdown import markdown_to_card_elements


def card_shell(
    title: str,
    elements: list[dict],
    template: str = "blue",
    *,
    wide: bool = True,
) -> dict:
    return {
        "title": title,
        "sections": [_sections_text(elements)],
        "config": {"wide_screen_mode": wide},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def markdown_elements(text: str) -> list[dict]:
    return markdown_to_card_elements(text or "")


def note(text: str) -> dict:
    return {
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": text}],
    }


def markdown_div(text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _sections_text(elements: list[dict]) -> str:
    parts: list[str] = []
    for element in elements:
        if element.get("tag") == "markdown":
            parts.append(str(element.get("content") or ""))
        text = element.get("text")
        if isinstance(text, dict):
            parts.append(str(text.get("content") or ""))
    return "\n\n".join(part for part in parts if part).strip()
