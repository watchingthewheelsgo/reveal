"""Telegram Bot implementation using python-telegram-bot."""

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config.settings import get_settings
from server.bot.base import (
    BotAdapter,
    BotContext,
    CommandRouter,
)
from server.bot.base import (
    CommandHandler as BotCommandHandler,
)
from server.bot.base import (
    MessageHandler as BotMessageHandler,
)


class TelegramBot(BotAdapter):
    def __init__(self):
        settings = get_settings()
        self.token = settings.telegram_bot_token
        self.admin_chat_ids = settings.get_telegram_admin_chat_ids()
        self._app: Application | None = None
        self._router: CommandRouter | None = None

    @property
    def app(self) -> Application:
        if self._app is None:
            raise RuntimeError("TelegramBot not initialized.")
        return self._app

    def set_router(self, router: CommandRouter):
        self._router = router

    async def initialize(self):
        self._app = Application.builder().token(self.token).build()

    async def start_polling(self):
        await self.app.initialize()
        if self.app.updater is None:
            raise RuntimeError("Telegram updater is not available.")
        await self.app.updater.start_polling()
        await self.app.start()

    async def stop(self):
        if self._app is None:
            return
        if self._app.updater and self._app.updater.running:
            await self._app.updater.stop()
        if self._app.running:
            await self._app.stop()
        await self._app.shutdown()

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        max_len = 4000
        for i in range(0, len(text), max_len):
            chunk = text[i : i + max_len]
            await self.app.bot.send_message(chat_id=chat_id, text=chunk, **kwargs)

    async def send_card(self, chat_id: str, card: dict) -> None:
        # Telegram uses Markdown messages; card dict is converted to formatted text
        text = self._format_card(card)
        await self.send_message(chat_id, text, parse_mode="Markdown")

    async def send_card_returning_id(self, chat_id: str, card: dict) -> str | None:
        text = self._format_card(card)
        msg = await self.app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        return str(msg.message_id)

    async def send_message_returning_id(self, chat_id: str, text: str) -> str | None:
        msg = await self.app.bot.send_message(chat_id=chat_id, text=text)
        return str(msg.message_id)

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        await self.app.bot.edit_message_text(chat_id=chat_id, message_id=int(message_id), text=text)

    async def reply_in_thread(self, chat_id: str, message_id: str, text: str) -> str | None:
        msg = await self.app.bot.send_message(
            chat_id=chat_id, text=text, reply_to_message_id=int(message_id)
        )
        return str(msg.message_id)

    async def push_to_admin(self, text: str) -> None:
        for chat_id in self.admin_chat_ids:
            await self.send_message(chat_id, text)

    def is_authorized(self, ctx: BotContext) -> bool:
        allowed = set(self.admin_chat_ids)
        return bool(allowed and (ctx.chat_id in allowed or ctx.user_id in allowed))

    def register_command(self, command: str, handler: BotCommandHandler) -> None:
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if update.effective_chat is None or update.message is None:
                return
            text = update.message.text or ""
            parts = text.split()
            ctx = BotContext(
                chat_id=str(update.effective_chat.id),
                user_id=str(update.effective_user.id if update.effective_user else ""),
                text=text,
                command=command,
                args=parts[1:] if len(parts) > 1 else [],
                raw_data={"update": update, "context": context},
            )
            await handler(ctx)

        self.app.add_handler(CommandHandler(command, wrapper))

    def register_message_handler(self, handler: BotMessageHandler) -> None:
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if update.effective_chat is None or update.message is None:
                return
            text = update.message.text or ""
            ctx = BotContext(
                chat_id=str(update.effective_chat.id),
                user_id=str(update.effective_user.id if update.effective_user else ""),
                text=text,
                command="",
                args=[],
                raw_data={"update": update, "context": context},
            )
            await handler(ctx)

        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wrapper))

    def _format_card(self, card: dict) -> str:
        title = card.get("title", "")
        lines = [f"*{title}*"] if title else []
        for section in card.get("sections", []):
            lines.append(f"\n{section}")
        if footer := card.get("footer"):
            lines.append(f"\n_{footer}_")
        return "\n".join(lines)
