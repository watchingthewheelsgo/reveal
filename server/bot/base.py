"""Bot command router — abstract interface for both Telegram and Feishu."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger

CommandHandler = Callable[["BotContext"], Awaitable[None]]
MessageHandler = Callable[["BotContext"], Awaitable[None]]


@dataclass
class BotContext:
    """Normalized message context across Telegram and Feishu."""

    chat_id: str
    user_id: str
    text: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)
    reply_to_message_id: str = ""


class BotAdapter(ABC):
    """Abstract bot adapter that both TelegramBot and FeishuBot implement."""

    @abstractmethod
    async def send_message(self, chat_id: str, text: str, **kwargs) -> None: ...

    @abstractmethod
    async def send_card(self, chat_id: str, card: dict) -> None: ...

    @abstractmethod
    def register_command(self, command: str, handler: CommandHandler) -> None: ...

    def register_message_handler(self, handler: MessageHandler) -> None:
        return None

    @abstractmethod
    async def push_to_admin(self, text: str) -> None: ...

    async def send_message_returning_id(self, chat_id: str, text: str) -> str | None:
        """Send a message and return its ID for later editing."""
        await self.send_message(chat_id, text)
        return None

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        """Edit a previously sent message in-place."""

    async def reply_in_thread(self, chat_id: str, message_id: str, text: str) -> str | None:
        """Reply inside a thread anchored to message_id."""
        await self.send_message(chat_id, text)
        return None

    def is_authorized(self, ctx: BotContext) -> bool:
        return True


class CommandRouter:
    """Routes incoming commands to registered handlers."""

    def __init__(self, adapter: BotAdapter):
        self.adapter = adapter
        self._handlers: dict[str, CommandHandler] = {}
        self._message_handler: MessageHandler | None = None

    def register(self, command: str, handler: CommandHandler):
        self._handlers[command] = handler
        self.adapter.register_command(command, handler)

    def register_many(self, commands: dict[str, CommandHandler]):
        for cmd, handler in commands.items():
            self.register(cmd, handler)

    def register_message_handler(self, handler: MessageHandler):
        self._message_handler = handler
        self.adapter.register_message_handler(handler)

    async def handle(self, ctx: BotContext):
        if not self.adapter.is_authorized(ctx):
            logger.warning(
                "Unauthorized bot command: command={} chat_id={} user_id={}",
                ctx.command,
                ctx.chat_id,
                ctx.user_id,
            )
            await self.adapter.send_message(ctx.chat_id, "未授权访问。")
            return

        handler = self._handlers.get(ctx.command)
        if handler:
            await handler(ctx)
        else:
            await self.adapter.send_message(
                ctx.chat_id, f"未知命令: /{ctx.command}\n输入 /help 查看可用命令。"
            )

    async def handle_message(self, ctx: BotContext):
        if not self.adapter.is_authorized(ctx):
            logger.warning(
                "Unauthorized bot message: chat_id={} user_id={}",
                ctx.chat_id,
                ctx.user_id,
            )
            return
        if self._message_handler:
            await self._message_handler(ctx)
