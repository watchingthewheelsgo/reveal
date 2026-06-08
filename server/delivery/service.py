"""Alert delivery with dedupe and message binding."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from server.db.engine import get_session_factory
from server.db.models import AlertDelivery
from server.events.types import AlertCandidate
from server.interactions.threading import bind_message_to_thread, normalize_platform

ThreadFactory = Callable[[str], Awaitable[int | None]]


async def send_alert(
    adapter: Any,
    candidate: AlertCandidate,
    *,
    chat_id: str,
    text: str,
    platform: str | None = None,
    thread_id: int | None = None,
    reason: str = "",
) -> AlertDelivery:
    """Send an alert once per event/platform/chat and persist the delivery state."""
    normalized_platform = normalize_platform(platform or _platform_for_adapter(adapter))
    existing = None
    if candidate.dedupe_policy != "repeat_allowed":
        existing = await get_delivery(candidate.event_key, normalized_platform, chat_id)
        if existing and existing.status == "sent" and candidate.dedupe_policy == "once_per_chat":
            return existing
    else:
        candidate = replace(candidate, event_key=_repeat_event_key(candidate.event_key))

    delivery = existing or await create_pending_delivery(
        candidate,
        chat_id=chat_id,
        platform=normalized_platform,
        thread_id=thread_id,
        reason=reason,
    )
    try:
        message_id = await _send_returning_id(adapter, chat_id, text)
        await mark_delivery_sent(delivery.id, message_id=message_id)
        if thread_id and message_id:
            await bind_message_to_thread(
                chat_id=chat_id,
                message_id=message_id,
                thread_id=thread_id,
                platform=normalized_platform,
                role="source",
                source_type=candidate.event_type,
                source_id=_int_or_none(candidate.source_id),
            )
        refreshed = await get_delivery_by_id(delivery.id)
        assert refreshed is not None
        return refreshed
    except Exception as exc:
        await mark_delivery_failed(delivery.id, str(exc))
        logger.exception(
            "Alert delivery failed: event_key={} platform={} chat_id={}",
            candidate.event_key,
            normalized_platform,
            chat_id,
        )
        raise


async def send_alert_to_admin(
    adapter: Any,
    candidate: AlertCandidate,
    *,
    text: str,
    platform: str | None = None,
    thread_id: int | None = None,
    thread_factory: ThreadFactory | None = None,
    reason: str = "",
) -> list[AlertDelivery]:
    chat_ids = _admin_chat_ids(adapter)
    if not chat_ids:
        await adapter.push_to_admin(text)
        return []
    deliveries = []
    for chat_id in chat_ids:
        effective_thread_id = await thread_factory(chat_id) if thread_factory else thread_id
        deliveries.append(
            await send_alert(
                adapter,
                candidate,
                chat_id=chat_id,
                text=text,
                platform=platform,
                thread_id=effective_thread_id,
                reason=reason,
            )
        )
    return deliveries


async def get_delivery(
    event_key: str,
    platform: str,
    chat_id: str,
) -> AlertDelivery | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(AlertDelivery).where(
                AlertDelivery.event_key == event_key,
                AlertDelivery.platform == platform,
                AlertDelivery.chat_id == chat_id,
            )
        )
        return result.scalar_one_or_none()


async def get_delivery_by_id(delivery_id: int) -> AlertDelivery | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await session.get(AlertDelivery, delivery_id)


async def create_pending_delivery(
    candidate: AlertCandidate,
    *,
    chat_id: str,
    platform: str,
    thread_id: int | None = None,
    reason: str = "",
) -> AlertDelivery:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = AlertDelivery(
            event_type=candidate.event_type,
            event_source_id=str(candidate.source_id) if candidate.source_id is not None else None,
            event_key=candidate.event_key,
            thread_id=thread_id,
            platform=platform,
            chat_id=chat_id,
            status="pending",
            reason=reason,
            severity=candidate.severity,
            payload=candidate.payload,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await get_delivery(candidate.event_key, platform, chat_id)
            if existing is not None:
                return existing
            raise
        await session.refresh(row)
        return row


async def mark_delivery_sent(delivery_id: int, *, message_id: str | None = None) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await session.get(AlertDelivery, delivery_id)
        if row is None:
            return
        now = _utcnow()
        row.status = "sent"
        row.message_id = message_id
        row.sent_at = now
        row.updated_at = now
        row.error = None
        await session.commit()


async def mark_delivery_failed(delivery_id: int, error: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await session.get(AlertDelivery, delivery_id)
        if row is None:
            return
        row.status = "failed"
        row.error = error
        row.updated_at = _utcnow()
        await session.commit()


async def record_skipped_delivery(
    candidate: AlertCandidate,
    *,
    chat_id: str,
    platform: str,
    reason: str,
    thread_id: int | None = None,
) -> AlertDelivery:
    existing = await get_delivery(candidate.event_key, platform, chat_id)
    if existing:
        return existing
    delivery = await create_pending_delivery(
        candidate,
        chat_id=chat_id,
        platform=platform,
        thread_id=thread_id,
        reason=reason,
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await session.get(AlertDelivery, delivery.id)
        assert row is not None
        row.status = "skipped"
        row.updated_at = _utcnow()
        await session.commit()
        await session.refresh(row)
        return row


async def record_sent_delivery(
    candidate: AlertCandidate,
    *,
    chat_id: str,
    platform: str,
    message_id: str | None = None,
    thread_id: int | None = None,
    reason: str = "",
) -> AlertDelivery:
    existing = await get_delivery(candidate.event_key, platform, chat_id)
    if existing:
        if existing.status != "sent":
            await mark_delivery_sent(existing.id, message_id=message_id)
            refreshed = await get_delivery_by_id(existing.id)
            assert refreshed is not None
            return refreshed
        return existing
    delivery = await create_pending_delivery(
        candidate,
        chat_id=chat_id,
        platform=platform,
        thread_id=thread_id,
        reason=reason,
    )
    await mark_delivery_sent(delivery.id, message_id=message_id)
    refreshed = await get_delivery_by_id(delivery.id)
    assert refreshed is not None
    return refreshed


async def _send_returning_id(adapter: Any, chat_id: str, text: str) -> str | None:
    if hasattr(adapter, "send_message_returning_id"):
        return await adapter.send_message_returning_id(chat_id, text)
    await adapter.send_message(chat_id, text)
    return None


def _int_or_none(value: int | str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _platform_for_adapter(adapter: Any) -> str:
    name = adapter.__class__.__name__.lower()
    if "telegram" in name:
        return "telegram"
    if "feishu" in name:
        return "feishu"
    return "auto"


def _admin_chat_ids(adapter: Any) -> list[str]:
    chat_ids: list[str] = []
    admin_chat_ids = getattr(adapter, "admin_chat_ids", None)
    if isinstance(admin_chat_ids, (list, tuple, set)):
        chat_ids.extend(str(chat_id) for chat_id in admin_chat_ids if chat_id)
    admin_chat_id = getattr(adapter, "admin_chat_id", None)
    if admin_chat_id:
        chat_ids.append(str(admin_chat_id))
    return list(dict.fromkeys(chat_ids))


def _repeat_event_key(event_key: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"{event_key}:repeat:{stamp}"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
