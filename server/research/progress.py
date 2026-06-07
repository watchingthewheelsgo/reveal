"""Research progress reporter — streams Agent tool calls to IM as status updates."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import monotonic

from loguru import logger

from server.bot.base import BotAdapter

ProgressCallback = Callable[[str, str], Awaitable[None]]
HEARTBEAT_INTERVAL_SECONDS = 30.0


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
    chunks = _split_markdown(result_text)
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
    for index, chunk in enumerate(chunks):
        if index:
            elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": chunk}})
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
