"""Research progress reporter — streams Agent tool calls to IM as status updates."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import monotonic

from loguru import logger

from server.bot.base import BotAdapter
from server.bot.feishu_markdown import markdown_to_card_elements, split_markdown_for_cards

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
        result_cards = _result_cards(result_text, self.step_count, elapsed)
        if anchor_message_id:
            try:
                return await self._send_result_cards(result_cards, anchor_message_id)
            except Exception as exc:
                logger.exception(
                    "Research result thread card reply failed; sending regular card: {}",
                    exc,
                )
        try:
            return await self._send_result_cards(result_cards, None)
        except Exception as exc:
            logger.exception("Research result card send failed; falling back to text: {}", exc)
        await _send_plain_text(self.adapter, self.chat_id, result_text)
        return None

    async def error(self, error_text: str) -> None:
        await self._stop_heartbeat()
        elapsed = monotonic() - self._started_at
        logger.info(
            "Research progress error: chat_id={} steps={} error={}",
            self.chat_id,
            self.step_count,
            error_text,
        )
        if self.status_message_id:
            await self._publish_status(f"❌ {error_text}")
        error_card = _error_card(error_text, self.step_count, elapsed)
        anchor_message_id = self.reply_to_message_id or self.status_message_id
        try:
            if anchor_message_id:
                await self.adapter.reply_card_in_thread(self.chat_id, anchor_message_id, error_card)
            else:
                await self.adapter.send_card_returning_id(self.chat_id, error_card)
        except Exception as exc:
            logger.exception("Research error card send failed; falling back to text: {}", exc)
            await _send_plain_text(
                self.adapter,
                self.chat_id,
                _error_text(error_text, step_count=self.step_count, elapsed_seconds=elapsed),
            )

    async def _send_result_cards(
        self,
        cards: list[dict],
        anchor_message_id: str | None,
    ) -> str | None:
        result_message_id: str | None = None
        for card in cards:
            if anchor_message_id:
                result_message_id = await self.adapter.reply_card_in_thread(
                    self.chat_id,
                    anchor_message_id,
                    card,
                )
            else:
                result_message_id = await self.adapter.send_card_returning_id(self.chat_id, card)
        return result_message_id

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
    return _result_card_part(
        result_text,
        step_count,
        elapsed_seconds,
        part_index=1,
        total_parts=1,
    )


def _result_cards(result_text: str, step_count: int, elapsed_seconds: float) -> list[dict]:
    parts = split_markdown_for_cards(result_text)
    total_parts = len(parts)
    return [
        _result_card_part(
            part,
            step_count,
            elapsed_seconds,
            part_index=index + 1,
            total_parts=total_parts,
        )
        for index, part in enumerate(parts)
    ]


def _result_card_part(
    result_text: str,
    step_count: int,
    elapsed_seconds: float,
    *,
    part_index: int,
    total_parts: int,
) -> dict:
    part_label = f" · 第 {part_index}/{total_parts} 部分" if total_parts > 1 else ""
    metadata = (
        f"{step_count} 个工具步骤 · {elapsed_seconds:.1f}s{part_label} · 继续回复本话题即可追问"
    )
    title = "Reveal · 研究结果" + (f" {part_index}/{total_parts}" if total_parts > 1 else "")
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
        "title": title,
        "sections": [result_text],
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def _result_body_elements(text: str) -> list[dict]:
    return markdown_to_card_elements(text)


def _error_card(error_text: str, step_count: int, elapsed_seconds: float) -> dict:
    content = _error_text(error_text, step_count=step_count, elapsed_seconds=elapsed_seconds)
    return {
        "title": "Reveal · 处理失败",
        "sections": [content],
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": "Reveal · 处理失败"},
        },
        "elements": markdown_to_card_elements(content),
    }


def _error_text(
    error_text: str,
    *,
    step_count: int | None = None,
    elapsed_seconds: float | None = None,
) -> str:
    reason = str(error_text or "").strip() or "未知错误"
    lines = ["**处理失败**", "", f"失败原因: {reason}"]
    if step_count is not None and elapsed_seconds is not None:
        lines.extend(["", f"工具步骤: {step_count}", f"耗时: {elapsed_seconds:.1f}s"])
    return "\n".join(lines)


async def _send_plain_text(adapter: BotAdapter, chat_id: str, text: str) -> None:
    if hasattr(adapter, "send_plain_text"):
        await adapter.send_plain_text(chat_id, text)
        return
    await adapter.send_message(chat_id, text)
