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
        except Exception as e:
            logger.debug(f"Progress start failed, falling back: {e}")
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

    async def finish(self, result_text: str) -> None:
        await self._stop_heartbeat()
        logger.info(
            "Research progress finish: chat_id={} steps={} elapsed={:.1f}s",
            self.chat_id,
            self.step_count,
            monotonic() - self._started_at,
        )
        if self.status_message_id and getattr(self.adapter, "supports_message_edit", True):
            await self._publish_status(f"✅ 研究完成 ({self.step_count} 步)")
        anchor_message_id = self.reply_to_message_id or self.status_message_id
        if anchor_message_id:
            try:
                await self.adapter.reply_in_thread(self.chat_id, anchor_message_id, result_text)
                return
            except Exception as e:
                logger.debug(f"Thread reply failed, sending as regular message: {e}")
        await self.adapter.send_message(self.chat_id, result_text)

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
            with suppress(Exception):
                await self.adapter.reply_in_thread(self.chat_id, anchor_message_id, text)
            return
        if not self.status_message_id:
            return
        try:
            await self.adapter.edit_message(self.chat_id, self.status_message_id, text)
        except Exception as e:
            logger.debug(f"Message edit failed (non-fatal): {e}")

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
    return {
        "title": "Reveal Research",
        "sections": [text],
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": "Reveal Research"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": text},
            }
        ],
    }
