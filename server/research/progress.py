"""Research progress reporter — streams Agent tool calls to IM as status updates."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import monotonic

from loguru import logger
from markdown_it import MarkdownIt
from markdown_it.token import Token

from server.bot.base import BotAdapter

ProgressCallback = Callable[[str, str], Awaitable[None]]
HEARTBEAT_INTERVAL_SECONDS = 30.0
CARD_MARKDOWN_CHUNK_SIZE = 1600
MARKDOWN_PARSER = MarkdownIt("commonmark", {"breaks": True}).enable("table")


class ResearchProgressReporter:
    """Manages progress updates for a single research session."""

    def __init__(
        self,
        adapter: BotAdapter,
        chat_id: str,
        reply_to_message_id: str = "",
    ):
        self.adapter = adapter
        self.chat_id = chat_id
        self.reply_to_message_id = reply_to_message_id
        self.status_message_id: str | None = None
        self.step_count = 0
        self._started_at = monotonic()
        self._heartbeat_task: asyncio.Task | None = None

    async def start(self, title: str) -> None:
        text = f"🔎 {title}"
        logger.info(
            "Research progress start: chat_id={} reply_to={} title={}",
            self.chat_id,
            self.reply_to_message_id or "-",
            title,
        )
        try:
            card = _status_card(text)
            if self.reply_to_message_id:
                self.status_message_id = await self.adapter.reply_card_in_thread(
                    self.chat_id, self.reply_to_message_id, card
                )
            else:
                self.status_message_id = await self.adapter.send_card_returning_id(
                    self.chat_id, card
                )
        except Exception:
            logger.exception("Research progress start failed; falling back to text message")
            await self.adapter.send_message(self.chat_id, text)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def on_progress(self, event_type: str, detail: str) -> None:
        if event_type == "tool_use":
            self.step_count += 1
            logger.info(
                "Research tool use: chat_id={} step={} detail={}",
                self.chat_id,
                self.step_count,
                detail,
            )
            status = f"🔎 研究中 (步骤 {self.step_count})...\n{detail}"
            await self._publish_status(status)

    async def finish(self, result_text: str) -> str | None:
        await self._stop_heartbeat()
        elapsed = monotonic() - self._started_at
        logger.info(
            "Research progress finish: chat_id={} steps={} elapsed={:.1f}s",
            self.chat_id,
            self.step_count,
            elapsed,
        )
        if self.status_message_id and getattr(self.adapter, "supports_message_edit", True):
            await self._publish_status(f"✅ 研究完成 ({self.step_count} 步)")
        anchor_message_id = self.reply_to_message_id or self.status_message_id
        result_card = _result_card(result_text, self.step_count, elapsed)
        if anchor_message_id:
            try:
                return await self.adapter.reply_card_in_thread(
                    self.chat_id, anchor_message_id, result_card
                )
            except Exception:
                logger.exception("Research result thread card reply failed; sending regular card")
        try:
            return await self.adapter.send_card_returning_id(self.chat_id, result_card)
        except Exception:
            logger.exception("Research result card send failed; falling back to text")
        await self.adapter.send_message(self.chat_id, result_text)
        return None

    async def error(self, error_text: str) -> None:
        await self._stop_heartbeat()
        logger.info(
            "Research progress error: chat_id={} steps={} error={}",
            self.chat_id,
            self.step_count,
            error_text,
        )
        if self.status_message_id:
            await self._publish_status(f"❌ {error_text}")
        else:
            await self.adapter.send_message(self.chat_id, f"❌ {error_text}")

    async def _publish_status(self, text: str) -> None:
        anchor_message_id = self.reply_to_message_id or self.status_message_id
        if not anchor_message_id:
            await self.adapter.send_message(self.chat_id, text)
            return
        if not getattr(self.adapter, "supports_message_edit", True):
            try:
                await self.adapter.reply_in_thread(self.chat_id, anchor_message_id, text)
            except Exception:
                logger.exception("Research progress thread reply failed")
            return
        if not self.status_message_id:
            return
        try:
            await self.adapter.edit_message(self.chat_id, self.status_message_id, text)
        except Exception:
            logger.exception("Research progress message edit failed")

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            elapsed = int(monotonic() - self._started_at)
            logger.info(
                "Research progress heartbeat: chat_id={} steps={} elapsed={}s",
                self.chat_id,
                self.step_count,
                elapsed,
            )
            await self._publish_status(
                f"🔎 仍在研究中... 已运行 {elapsed}s，已观察到 {self.step_count} 个工具步骤。"
            )

    async def _stop_heartbeat(self) -> None:
        if not self._heartbeat_task:
            return
        self._heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._heartbeat_task
        self._heartbeat_task = None


def _status_card(text: str) -> dict:
    template = "green" if text.startswith("✅") else "red" if text.startswith("❌") else "blue"
    title = (
        "Reveal · 研究完成"
        if text.startswith("✅")
        else "Reveal · 处理失败"
        if text.startswith("❌")
        else "Reveal · 研究中"
    )
    return {
        "title": title,
        "sections": [text],
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": text},
            }
        ],
    }


def _result_card(result_text: str, step_count: int, elapsed_seconds: float) -> dict:
    metadata = f"{step_count} 个工具步骤 · {elapsed_seconds:.1f}s · 继续回复本话题即可追问"
    elements: list[dict] = [
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": metadata,
                }
            ],
        },
        {"tag": "hr"},
    ]
    body_elements = _result_body_elements(result_text)
    for index, element in enumerate(body_elements):
        if index:
            elements.append({"tag": "hr"})
        elements.append(element)
    return {
        "title": "Reveal · 研究结果",
        "sections": [result_text],
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "Reveal · 研究结果"},
        },
        "elements": elements,
    }


def _result_body_elements(text: str) -> list[dict]:
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
            _append_code_elements(elements, _format_table(rows))
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


def _format_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    column_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
    widths = [
        max(len(row[column_index]) for row in normalized_rows)
        for column_index in range(column_count)
    ]

    def format_row(row: list[str]) -> str:
        return " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip()

    lines = [format_row(normalized_rows[0])]
    if len(normalized_rows) > 1:
        lines.append("-+-".join("-" * width for width in widths))
        lines.extend(format_row(row) for row in normalized_rows[1:])
    return "\n".join(lines)


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
