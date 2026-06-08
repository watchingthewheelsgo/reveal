"""Report card builders."""

from __future__ import annotations

from server.bot.cards._base import card_shell, markdown_elements, note


def report_card(title: str, body: str, *, footer: str = "") -> dict:
    elements = markdown_elements(body)
    if footer:
        elements.append({"tag": "hr"})
        elements.append(note(footer))
    return card_shell(title, elements, template="blue")
