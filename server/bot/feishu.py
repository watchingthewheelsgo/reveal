# pyright: reportAttributeAccessIssue=false, reportMissingImports=false, reportOptionalMemberAccess=false
"""Feishu Bot implementation using lark-oapi WebSocket."""

import asyncio
import json
import threading

import lark_oapi as lark
from lark_oapi.adapter.ws import WSClient
from lark_oapi.ws import MessageReceive

from config.settings import get_settings
from server.bot.base import (
    BotAdapter,
    BotContext,
    CommandRouter,
)
from server.bot.base import (
    CommandHandler as BotCommandHandler,
)


class FeishuBot(BotAdapter):
    def __init__(self):
        settings = get_settings()
        self.app_id = settings.feishu_app_id
        self.app_secret = settings.feishu_app_secret
        self.admin_chat_ids = settings.get_feishu_admin_chat_ids()
        self._client: WSClient | None = None
        self._router: CommandRouter | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def set_router(self, router: CommandRouter):
        self._router = router

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._event_loop = loop

    def start_in_thread(self):
        def _run():
            asyncio.run(self._start_ws())

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._client:
            self._client.stop()

    async def _start_ws(self):
        builder = (
            lark.ws.Client.Builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .event_handler(lark.im.v1.P2ImMessageReceiveV1, self._on_message)
            .event_handler(
                lark.im.v1.P2ImMessageReceiveV1Data,
                self._on_message,
            )
        )
        self._client = builder.build()
        self._client.start()

    async def _on_message(self, event: MessageReceive):
        msg = event.event.message
        if msg is None:
            return
        chat_id = msg.chat_id
        content_str = msg.content
        if not content_str or not chat_id:
            return

        try:
            content = json.loads(content_str)
            text = content.get("text", "")
        except json.JSONDecodeError:
            text = content_str

        if not text:
            return

        parts = text.strip().split()
        command = ""
        args: list[str] = []
        if parts[0].startswith("/"):
            command = parts[0][1:]
            args = parts[1:]

        ctx = BotContext(
            chat_id=chat_id,
            user_id=event.event.sender.sender_id.open_id or "",
            text=text,
            command=command,
            args=args,
            raw_data={"event": event},
        )

        if self._router and command:
            await self._router.handle(ctx)

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        client = lark.Client.builder().app_id(self.app_id).app_secret(self.app_secret).build()
        max_len = 4000
        for i in range(0, len(text), max_len):
            chunk = text[i : i + max_len]
            await lark.im.v1.message.create_async(
                client,
                lark.im.v1.CreateMessageReq(
                    receive_id_type="chat_id",
                    body=lark.im.v1.CreateMessageReqBody(
                        receive_id=chat_id,
                        msg_type=lark.im.v1.MsgType.text,
                        content=json.dumps({"text": chunk}),
                    ),
                ),
            )

    async def send_card(self, chat_id: str, card: dict) -> None:
        client = lark.Client.builder().app_id(self.app_id).app_secret(self.app_secret).build()
        await lark.im.v1.message.create_async(
            client,
            lark.im.v1.CreateMessageReq(
                receive_id_type="chat_id",
                body=lark.im.v1.CreateMessageReqBody(
                    receive_id=chat_id,
                    msg_type=lark.im.v1.MsgType.interactive,
                    content=json.dumps(card),
                ),
            ),
        )

    async def push_to_admin(self, text: str) -> None:
        for chat_id in self.admin_chat_ids:
            await self.send_message(chat_id, text)

    def is_authorized(self, ctx: BotContext) -> bool:
        return bool(self.admin_chat_ids and ctx.chat_id in set(self.admin_chat_ids))

    def register_command(self, command: str, handler: BotCommandHandler) -> None:
        # Feishu commands are dispatched via _on_message -> router.handle
        pass
