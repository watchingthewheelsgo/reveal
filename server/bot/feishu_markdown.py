"""Markdown-to-Feishu-card rendering helpers."""

from markdown_it import MarkdownIt
from markdown_it.token import Token

CARD_MARKDOWN_CHUNK_SIZE = 1600
MARKDOWN_PARSER = MarkdownIt("commonmark", {"breaks": True}).enable("table")


def markdown_to_card_elements(text: str) -> list[dict]:
    normalized = text.strip() or "（无内容）"
    elements: list[dict] = []
    tokens = MARKDOWN_PARSER.parse(normalized)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        token_type = token.type
        if token_type == "heading_open":
            content, index = _collect_inline_block(tokens, index, "heading_close")
            _append_markdown_elements(elements, f"**{content}**")
            continue
        if token_type == "paragraph_open":
            content, index = _collect_inline_block(tokens, index, "paragraph_close")
            _append_markdown_elements(elements, content)
            continue
        if token_type in {"bullet_list_open", "ordered_list_open"}:
            content, index = _collect_list_block(tokens, index)
            _append_markdown_elements(elements, content)
            continue
        if token_type == "table_open":
            rows, index = _collect_table_block(tokens, index)
            elements.extend(_table_elements(rows))
            continue
        if token_type in {"fence", "code_block"}:
            _append_code_elements(elements, token.content)
        elif token_type == "html_block":
            _append_markdown_elements(elements, token.content)
        index += 1

    return elements or [_md_div("（无内容）")]


def _collect_inline_block(
    tokens: list[Token], start_index: int, close_type: str
) -> tuple[str, int]:
    parts: list[str] = []
    index = start_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token.type == close_type:
            return _compact_lines(parts), index + 1
        if token.type == "inline":
            parts.append(token.content)
        elif token.type in {"fence", "code_block"}:
            parts.append(token.content)
        index += 1
    return _compact_lines(parts), index


def _collect_list_block(tokens: list[Token], start_index: int) -> tuple[str, int]:
    opening = tokens[start_index]
    ordered = opening.type == "ordered_list_open"
    close_type = "ordered_list_close" if ordered else "bullet_list_close"
    first_number = _ordered_list_start(opening)
    items: list[str] = []
    item_parts: list[str] = []
    item_depth = 0
    index = start_index + 1

    while index < len(tokens):
        token = tokens[index]
        if token.type == close_type and item_depth == 0:
            break
        if token.type == "list_item_open":
            if item_depth == 0:
                item_parts = []
            item_depth += 1
        elif token.type == "list_item_close":
            item_depth -= 1
            if item_depth == 0:
                item = _compact_lines(item_parts)
                if item:
                    items.append(item)
        elif item_depth > 0 and token.type == "inline":
            item_parts.append(token.content)
        elif item_depth > 0 and token.type in {"fence", "code_block"}:
            item_parts.append(token.content)
        index += 1

    lines = []
    for offset, item in enumerate(items):
        if ordered:
            lines.append(f"{first_number + offset}. {item}")
        else:
            lines.append(f"• {item}")
    return "\n".join(lines), index + 1


def _ordered_list_start(token: Token) -> int:
    raw_start = token.attrGet("start")
    if raw_start is None:
        return 1
    try:
        return int(raw_start)
    except ValueError:
        return 1


def _collect_table_block(tokens: list[Token], start_index: int) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    current_row: list[str] | None = None
    index = start_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token.type == "table_close":
            return rows, index + 1
        if token.type == "tr_open":
            current_row = []
        elif token.type == "tr_close":
            if current_row is not None:
                rows.append(current_row)
            current_row = None
        elif token.type == "inline" and current_row is not None:
            current_row.append(" ".join(token.content.split()))
        index += 1
    return rows, index


def _table_elements(rows: list[list[str]]) -> list[dict]:
    if not rows:
        return []
    headers = rows[0]
    body_rows = rows[1:] if len(rows) > 1 else rows
    elements: list[dict] = []
    for row in body_rows:
        fields = []
        for index, cell in enumerate(row):
            header = headers[index] if index < len(headers) and len(rows) > 1 else f"列 {index + 1}"
            fields.append(
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{header or f'列 {index + 1}'}**\n{cell or '—'}",
                    },
                }
            )
        if fields:
            elements.append({"tag": "div", "fields": fields})
    return elements


def _compact_lines(parts: list[str]) -> str:
    return "\n".join(part.strip() for part in parts if part.strip()).strip()


def _append_markdown_elements(elements: list[dict], content: str) -> None:
    for chunk in _split_markdown(content, chunk_size=CARD_MARKDOWN_CHUNK_SIZE):
        elements.append(_md_div(chunk))


def _append_code_elements(elements: list[dict], content: str) -> None:
    for chunk in _split_plain_text(content, chunk_size=CARD_MARKDOWN_CHUNK_SIZE):
        elements.append(_md_div(f"```\n{chunk}\n```"))


def _md_div(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


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


def _split_plain_text(text: str, chunk_size: int = 2800) -> list[str]:
    normalized = text.strip() or "（无内容）"
    chunks: list[str] = []
    current = ""
    for line in normalized.splitlines():
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > chunk_size:
            chunks.append(line[:chunk_size])
            line = line[chunk_size:]
        current = line
    if current:
        chunks.append(current)
    return chunks
