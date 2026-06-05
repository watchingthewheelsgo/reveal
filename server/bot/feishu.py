# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportUnknownMemberType=false
"""Feishu Bot implementation using lark-oapi WebSocket and HTTP callbacks."""

import asyncio
import hashlib
import hmac
import json
import threading
from io import BytesIO
from typing import Any

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from loguru import logger

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


class FeishuBot(BotAdapter):
    def __init__(self):
        settings = get_settings()
        self.app_id = settings.feishu_app_id
        self.app_secret = settings.feishu_app_secret
        self.verification_token = settings.feishu_verification_token
        self.encrypt_key = settings.feishu_encrypt_key
        self.admin_chat_ids = settings.get_feishu_admin_chat_ids()
        self._router: CommandRouter | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._ws_client: Any | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_stop_future: asyncio.Future[None] | None = None
        self._ws_stopping = False
        self._thread: threading.Thread | None = None
        self._processed_message_ids: set[str] = set()
        self._image_key_cache: dict[str, str] = {}
        self.client = lark.Client.builder().app_id(self.app_id).app_secret(self.app_secret).build()

    def set_router(self, router: CommandRouter):
        self._router = router

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._event_loop = loop

    def start_in_thread(self):
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_ws_message)
            .build()
        )
        self._ws_client = lark.ws.Client(
            app_id=self.app_id,
            app_secret=self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        def _run():
            import lark_oapi.ws.client as ws_mod

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ws_mod.loop = loop
            self._ws_loop = loop
            self._ws_stopping = False
            try:
                loop.run_until_complete(self._run_ws_until_stopped())
            except Exception as e:
                if not self._ws_stopping:
                    logger.exception(f"Feishu bot WebSocket failed: {e}")
            finally:
                self._cancel_remaining_ws_tasks(loop)
                loop.close()
                self._ws_loop = None
                self._ws_stop_future = None

        self._thread = threading.Thread(target=_run, name="feishu-ws", daemon=True)
        self._thread.start()

    def stop(self):
        self._ws_stopping = True
        if self._ws_loop and self._ws_loop.is_running():
            self._ws_loop.call_soon_threadsafe(self._signal_ws_stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("Feishu bot WebSocket thread did not stop within timeout")
        self._thread = None

    async def _run_ws_until_stopped(self) -> None:
        if not self._ws_client:
            return
        loop = asyncio.get_running_loop()
        self._ws_stop_future = loop.create_future()

        await self._ws_client._connect()
        loop.create_task(self._ws_client._ping_loop(), name="feishu-ws-ping")
        try:
            await self._ws_stop_future
        finally:
            self._ws_client._auto_reconnect = False
            await self._cancel_active_ws_tasks()
            await self._ws_client._disconnect()

    def _signal_ws_stop(self) -> None:
        if self._ws_stop_future and not self._ws_stop_future.done():
            self._ws_stop_future.set_result(None)

    async def _cancel_active_ws_tasks(self) -> None:
        current = asyncio.current_task()
        tasks = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _cancel_remaining_ws_tasks(self, loop: asyncio.AbstractEventLoop) -> None:
        tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

    def verify_signature(
        self,
        timestamp: str,
        nonce: str,
        encrypt: str,
        signature: str,
    ) -> bool:
        if not self.verification_token:
            return True
        content = f"{timestamp}{nonce}{encrypt}{self.verification_token}"
        computed = hashlib.sha256(content.encode()).hexdigest()
        return hmac.compare_digest(computed, signature)

    async def handle_event(self, body: dict) -> dict:
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge", "")}

        if body.get("encrypt"):
            return {"error": "Encrypted Feishu callbacks are not supported yet."}

        if body.get("header", {}).get("event_type") != "im.message.receive_v1":
            return {"status": "ignored"}

        event_data = body.get("event", {})
        message = event_data.get("message", {})
        sender = event_data.get("sender", {})
        chat_id = message.get("chat_id")
        content_str = message.get("content")
        if not chat_id or not content_str:
            return {"status": "ignored"}

        text = self._extract_text(content_str)
        sender_id = sender.get("sender_id", {})
        root_id = message.get("root_id") or message.get("parent_id") or ""
        ctx = self._make_context(
            chat_id=chat_id,
            user_id=sender_id.get("open_id", ""),
            text=text,
            message_id=message.get("message_id") or "",
            raw_data={"event": event_data, "body": body},
            reply_to_message_id=root_id,
        )
        if self._router:
            if text.startswith("/"):
                await self._router.handle(ctx)
            else:
                await self._router.handle_message(ctx)
        return {"status": "ok"}

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        loop = asyncio.get_running_loop()
        max_len = 4000
        for i in range(0, len(text), max_len):
            chunk = text[i : i + max_len]
            await loop.run_in_executor(None, self._send_text_sync, chat_id, chunk)

    async def send_card(self, chat_id: str, card: dict) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._send_card_sync, chat_id, card)

    async def send_card_returning_id(self, chat_id: str, card: dict) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._send_card_returning_id_sync, chat_id, card)

    async def send_message_returning_id(self, chat_id: str, text: str) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._send_text_returning_id_sync, chat_id, text)

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._patch_message_sync, message_id, text)

    async def reply_in_thread(self, chat_id: str, message_id: str, text: str) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._reply_in_thread_sync, message_id, text)

    async def reply_card_in_thread(self, chat_id: str, message_id: str, card: dict) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._reply_card_in_thread_sync, message_id, card)

    async def upload_image(self, image_url: str, alt_text: str | None = None) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._upload_image_url_sync, image_url, alt_text)

    async def push_to_admin(self, text: str) -> None:
        for chat_id in self.admin_chat_ids:
            await self.send_message(chat_id, text)

    def is_authorized(self, ctx: BotContext) -> bool:
        return bool(self.admin_chat_ids and ctx.chat_id in set(self.admin_chat_ids))

    def register_command(self, command: str, handler: BotCommandHandler) -> None:
        # Feishu commands are dispatched via _handle_ws_message/handle_event -> router.handle.
        return None

    def register_message_handler(self, handler: BotMessageHandler) -> None:
        # Feishu plain messages are dispatched through the same event entrypoints.
        return None

    def _handle_ws_message(self, data: P2ImMessageReceiveV1) -> None:
        if not data.event or not data.event.message:
            return
        message = data.event.message
        msg_id = message.message_id
        if msg_id and msg_id in self._processed_message_ids:
            return
        if msg_id:
            self._processed_message_ids.add(msg_id)
            if len(self._processed_message_ids) > 5000:
                self._processed_message_ids = set(list(self._processed_message_ids)[-2500:])

        if message.message_type != "text":
            return

        chat_id = message.chat_id or ""
        text = self._extract_text(message.content or "{}")
        if text.startswith("@"):
            parts = text.split(maxsplit=1)
            text = parts[1] if len(parts) > 1 else ""

        sender_id = data.event.sender.sender_id.open_id if data.event.sender else ""
        root_id = getattr(message, "root_id", None) or getattr(message, "parent_id", None) or ""
        ctx = self._make_context(
            chat_id=chat_id,
            user_id=sender_id or "",
            text=text,
            message_id=msg_id or "",
            raw_data={"event": data},
            reply_to_message_id=root_id,
        )
        if self._router and self._event_loop and not self._event_loop.is_closed():
            coro = (
                self._router.handle(ctx)
                if text.startswith("/")
                else self._router.handle_message(ctx)
            )
            future = asyncio.run_coroutine_threadsafe(coro, self._event_loop)
            try:
                future.result(timeout=120)
            except Exception:
                logger.exception("Feishu command handling failed")

    def _make_context(
        self,
        chat_id: str,
        user_id: str,
        text: str,
        raw_data: dict,
        message_id: str = "",
        reply_to_message_id: str = "",
    ) -> BotContext:
        parts = text.strip().split()
        return BotContext(
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            message_id=message_id,
            command=parts[0][1:] if parts and parts[0].startswith("/") else "",
            args=parts[1:] if len(parts) > 1 else [],
            raw_data=raw_data,
            reply_to_message_id=reply_to_message_id,
        )

    def _extract_text(self, content_str: str) -> str:
        try:
            content = json.loads(content_str)
            return content.get("text", "").strip()
        except json.JSONDecodeError:
            logger.exception("Feishu message content JSON parse failed; using raw text")
            return content_str.strip()

    def _send_text_sync(self, chat_id: str, text: str) -> None:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"Feishu send failed: {response.code} - {response.msg}")

    def _send_card_sync(self, chat_id: str, card: dict) -> None:
        self._send_card_returning_id_sync(chat_id, card)

    def _send_card_returning_id_sync(self, chat_id: str, card: dict) -> str | None:
        payload = self._format_feishu_card(card)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(payload))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"Feishu card send failed: {response.code} - {response.msg}")
        if response.data and response.data.message_id:
            return response.data.message_id
        return None

    def _send_text_returning_id_sync(self, chat_id: str, text: str) -> str | None:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"Feishu send failed: {response.code} - {response.msg}")
        if response.data and response.data.message_id:
            return response.data.message_id
        return None

    def _patch_message_sync(self, message_id: str, text: str) -> None:
        payload = self._format_progress_card(text)
        request = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(PatchMessageRequestBody.builder().content(json.dumps(payload)).build())
            .build()
        )
        response = self.client.im.v1.message.patch(request)
        if not response.success():
            logger.error("Feishu patch failed: {} - {}", response.code, response.msg)

    def _reply_in_thread_sync(self, message_id: str, text: str) -> str | None:
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .reply_in_thread(True)
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(f"Feishu reply failed: {response.code} - {response.msg}")
        if response.data and response.data.message_id:
            return response.data.message_id
        return None

    def _reply_card_in_thread_sync(self, message_id: str, card: dict) -> str | None:
        payload = self._format_feishu_card(card)
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content(json.dumps(payload))
                .reply_in_thread(True)
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(f"Feishu card reply failed: {response.code} - {response.msg}")
        if response.data and response.data.message_id:
            return response.data.message_id
        return None

    def _upload_image_url_sync(self, image_url: str, alt_text: str | None = None) -> str | None:
        if not image_url:
            return None
        cached = self._image_key_cache.get(image_url)
        if cached:
            return cached

        try:
            response = httpx.get(
                image_url,
                follow_redirects=True,
                headers={"User-Agent": "Reveal/1.0"},
                timeout=20,
            )
            response.raise_for_status()
        except Exception:
            logger.exception("Feishu image download failed: {}", image_url)
            return None

        image_bytes = response.content
        if not image_bytes:
            return None
        if len(image_bytes) > 10 * 1024 * 1024:
            logger.debug(f"Feishu image skipped because it is too large: {image_url}")
            return None

        image_stream = BytesIO(image_bytes)
        image_stream.name = _image_filename(image_url, alt_text)
        request = (
            CreateImageRequest.builder()
            .request_body(
                CreateImageRequestBody.builder().image_type("message").image(image_stream).build()
            )
            .build()
        )
        upload_response = self.client.im.v1.image.create(request)
        if not upload_response.success():
            logger.error(
                "Feishu image upload failed: {} - {}",
                upload_response.code,
                upload_response.msg,
            )
            return None
        if upload_response.data and upload_response.data.image_key:
            image_key = upload_response.data.image_key
            self._image_key_cache[image_url] = image_key
            if len(self._image_key_cache) > 512:
                self._image_key_cache.pop(next(iter(self._image_key_cache)))
            return image_key
        return None

    def _format_feishu_card(self, card: dict) -> dict:
        if card.get("elements") or card.get("header"):
            allowed_keys = {
                "card_link",
                "config",
                "elements",
                "header",
                "i18n_elements",
                "i18n_header",
            }
            return {key: value for key, value in card.items() if key in allowed_keys}

        title = str(card.get("title") or "Reveal")
        sections = [str(section) for section in card.get("sections", [])]
        footer = str(card.get("footer") or "")
        elements: list[dict] = []
        for section in sections:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": section}})
            elements.append({"tag": "hr"})
        if footer:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": footer}})
        if elements and elements[-1].get("tag") == "hr":
            elements.pop()
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        }

    def _format_progress_card(self, text: str) -> dict:
        template = "green" if text.startswith("✅") else "red" if text.startswith("❌") else "blue"
        title = "Reveal Research"
        return {
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


def _image_filename(image_url: str, alt_text: str | None = None) -> str:
    suffix = image_url.rsplit("/", maxsplit=1)[-1].split("?", maxsplit=1)[0]
    suffix = suffix if "." in suffix else "tweet-image.jpg"
    if alt_text:
        safe_alt = "".join(ch for ch in alt_text[:32] if ch.isalnum() or ch in {"-", "_"})
        if safe_alt:
            return f"{safe_alt}-{suffix}"
    return suffix
