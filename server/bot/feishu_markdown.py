"""Feishu card Markdown rendering helpers."""

import re

CARD_MARKDOWN_CHUNK_SIZE = 2800
MAX_TABLES_PER_CARD = 1

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def markdown_to_card_elements(text: str) -> list[dict]:
    """Render Markdown as Feishu Card JSON 2.0 markdown components."""
    return [
        {"tag": "markdown", "content": chunk}
        for chunk in _split_markdown(text, chunk_size=CARD_MARKDOWN_CHUNK_SIZE)
    ]


def _split_markdown(text: str, chunk_size: int = 2800) -> list[str]:
    normalized = text.strip() or "（无内容）"
    chunks: list[str] = []
    current = ""
    for paragraph in normalized.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > chunk_size:
            chunks.append(paragraph[:chunk_size])
            paragraph = paragraph[chunk_size:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def split_markdown_for_cards(
    text: str,
    *,
    chunk_size: int = CARD_MARKDOWN_CHUNK_SIZE,
    max_tables_per_card: int = MAX_TABLES_PER_CARD,
) -> list[str]:
    """Split Markdown into card-safe chunks while preserving tables.

    Feishu renders Markdown tables as card table components and enforces a per-card
    table limit. Keep each table block in a separate chunk so long research
    answers can be sent as consecutive cards.
    """
    blocks = _markdown_blocks(text.strip() or "（无内容）")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    current_tables = 0

    def flush() -> None:
        nonlocal current, current_len, current_tables
        if not current:
            return
        chunks.append("\n\n".join(part for part in current if part).strip())
        current = []
        current_len = 0
        current_tables = 0

    for block in blocks:
        block_text = block["text"]
        block_tables = 1 if block["type"] == "table" else 0
        if block["type"] == "table":
            table_parts = _split_table_block(block_text, chunk_size=chunk_size)
        elif len(block_text) > chunk_size:
            table_parts = _split_markdown(block_text, chunk_size=chunk_size)
        else:
            table_parts = [block_text]

        for part in table_parts:
            part_tables = block_tables
            separator_len = 2 if current else 0
            would_exceed_length = current_len + separator_len + len(part) > chunk_size
            would_exceed_tables = current_tables + part_tables > max_tables_per_card
            if current and (would_exceed_length or would_exceed_tables):
                flush()
            if len(part) > chunk_size and not part_tables:
                chunks.extend(_split_markdown(part, chunk_size=chunk_size))
                continue
            current.append(part)
            current_len = current_len + separator_len + len(part)
            current_tables += part_tables

    flush()
    return chunks or ["（无内容）"]


def _markdown_blocks(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    blocks: list[dict[str, str]] = []
    current: list[str] = []
    in_fence = False
    index = 0

    def flush_text() -> None:
        nonlocal current
        content = "\n".join(current).strip()
        if content:
            blocks.append({"type": "text", "text": content})
        current = []

    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            current.append(line)
            index += 1
            continue

        if (
            not in_fence
            and index + 1 < len(lines)
            and _looks_like_table_row(line)
            and _is_table_separator(lines[index + 1])
        ):
            flush_text()
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and _looks_like_table_row(lines[index]):
                table_lines.append(lines[index])
                index += 1
            blocks.append({"type": "table", "text": "\n".join(table_lines)})
            continue

        current.append(line)
        index += 1

    flush_text()
    return blocks


def _split_table_block(table_text: str, *, chunk_size: int) -> list[str]:
    lines = table_text.splitlines()
    if len(table_text) <= chunk_size or len(lines) <= 2:
        return [table_text]
    header = lines[:2]
    rows = lines[2:]
    parts: list[str] = []
    current = header[:]
    current_len = len("\n".join(current))
    for row in rows:
        row_len = len(row) + 1
        if len(current) > 2 and current_len + row_len > chunk_size:
            parts.append("\n".join(current))
            current = header[:]
            current_len = len("\n".join(current))
        current.append(row)
        current_len += row_len
    if len(current) > 2:
        parts.append("\n".join(current))
    return parts or [table_text]


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and bool(stripped.strip("|").strip())


def _is_table_separator(line: str) -> bool:
    return bool(_TABLE_SEPARATOR_RE.match(line))
