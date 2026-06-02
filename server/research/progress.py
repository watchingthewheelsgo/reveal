"""Research progress reporter — streams Agent tool calls to IM as status updates."""

from collections.abc import Awaitable, Callable

from loguru import logger

from server.bot.base import BotAdapter

ProgressCallback = Callable[[str, str], Awaitable[None]]


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

    async def start(self, title: str) -> None:
        text = f"🔎 {title}"
        try:
            if self.reply_to_message_id:
                self.status_message_id = await self.adapter.reply_in_thread(
                    self.chat_id, self.reply_to_message_id, text
                )
            else:
                self.status_message_id = await self.adapter.send_message_returning_id(
                    self.chat_id, text
                )
        except Exception as e:
            logger.debug(f"Progress start failed, falling back: {e}")
            await self.adapter.send_message(self.chat_id, text)

    async def on_progress(self, event_type: str, detail: str) -> None:
        if event_type == "tool_use":
            self.step_count += 1
            status = f"🔎 研究中 (步骤 {self.step_count})...\n{detail}"
            await self._update_status(status)

    async def finish(self, result_text: str) -> None:
        if self.status_message_id:
            await self._update_status(f"✅ 研究完成 ({self.step_count} 步)")
            try:
                await self.adapter.reply_in_thread(
                    self.chat_id, self.status_message_id, result_text
                )
                return
            except Exception as e:
                logger.debug(f"Thread reply failed, sending as regular message: {e}")
        await self.adapter.send_message(self.chat_id, result_text)

    async def error(self, error_text: str) -> None:
        if self.status_message_id:
            await self._update_status(f"❌ {error_text}")
        else:
            await self.adapter.send_message(self.chat_id, f"❌ {error_text}")

    async def _update_status(self, text: str) -> None:
        if not self.status_message_id:
            return
        try:
            await self.adapter.edit_message(self.chat_id, self.status_message_id, text)
        except Exception as e:
            logger.debug(f"Message edit failed (non-fatal): {e}")
