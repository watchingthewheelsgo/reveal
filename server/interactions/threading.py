"""Persistence helpers for IM/Web interaction threads."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select

from server.db.engine import get_session_factory
from server.db.models import BotMessageBinding, InteractionThread


def normalize_platform(platform: str | None) -> str:
    value = (platform or "auto").strip().lower()
    if value in {"telegram", "feishu", "web", "auto"}:
        return value
    return "auto"


async def get_or_create_thread_for_source(
    *,
    chat_id: str,
    platform: str = "auto",
    source_type: str = "agent",
    source_id: int | None = None,
    source_key: str | None = None,
    root_message_id: str | None = None,
    research_session_id: int | None = None,
) -> InteractionThread:
    """Return an active thread matching the source/root, creating one if needed."""
    normalized_platform = normalize_platform(platform)
    session_factory = get_session_factory()
    async with session_factory() as session:
        statement = select(InteractionThread).where(
            InteractionThread.chat_id == chat_id,
            InteractionThread.platform == normalized_platform,
            InteractionThread.status == "active",
        )
        if root_message_id:
            statement = statement.where(InteractionThread.root_message_id == root_message_id)
        elif source_id is not None:
            statement = statement.where(
                InteractionThread.source_type == source_type,
                InteractionThread.source_id == source_id,
            )
        elif source_key:
            statement = statement.where(
                InteractionThread.source_type == source_type,
                InteractionThread.source_key == source_key,
            )
        else:
            statement = statement.where(
                InteractionThread.source_type == source_type,
                InteractionThread.source_id.is_(None),
                InteractionThread.source_key.is_(None),
            )

        result = await session.execute(
            statement.order_by(desc(InteractionThread.last_activity_at), desc(InteractionThread.id))
        )
        thread = result.scalars().first()
        now = _utcnow()
        if thread is None:
            thread = InteractionThread(
                chat_id=chat_id,
                platform=normalized_platform,
                root_message_id=root_message_id,
                source_type=source_type,
                source_id=source_id,
                source_key=source_key,
                research_session_id=research_session_id,
                last_activity_at=now,
            )
            session.add(thread)
            await session.flush()
        else:
            thread.last_activity_at = now
            thread.updated_at = now
            if research_session_id is not None:
                thread.research_session_id = research_session_id
            if root_message_id and not thread.root_message_id:
                thread.root_message_id = root_message_id
            if source_key and not thread.source_key:
                thread.source_key = source_key
        await session.commit()
        await session.refresh(thread)
        return thread


async def create_agent_thread(
    *,
    chat_id: str,
    platform: str = "auto",
    root_message_id: str | None = None,
    research_session_id: int | None = None,
) -> InteractionThread:
    return await get_or_create_thread_for_source(
        chat_id=chat_id,
        platform=platform,
        source_type="agent",
        source_id=research_session_id,
        root_message_id=root_message_id,
        research_session_id=research_session_id,
    )


async def bind_message_to_thread(
    *,
    chat_id: str,
    message_id: str | None,
    thread_id: int | None,
    platform: str = "auto",
    role: str = "source",
    source_type: str | None = None,
    source_id: int | None = None,
) -> None:
    """Bind an IM message id to an interaction thread.

    `source_type/source_id` remain populated for backward compatibility with the
    original `BotMessageBinding` schema and fallback routing.
    """
    if not chat_id or not message_id or thread_id is None:
        return
    normalized_platform = normalize_platform(platform)
    session_factory = get_session_factory()
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(BotMessageBinding).where(
                    BotMessageBinding.chat_id == chat_id,
                    BotMessageBinding.message_id == message_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.platform = normalized_platform
            existing.thread_id = thread_id
            existing.role = role
            if source_type:
                existing.source_type = source_type
            if source_id is not None:
                existing.source_id = source_id
        else:
            session.add(
                BotMessageBinding(
                    platform=normalized_platform,
                    chat_id=chat_id,
                    message_id=message_id,
                    source_type=source_type or "thread",
                    source_id=source_id if source_id is not None else thread_id,
                    thread_id=thread_id,
                    role=role,
                )
            )
        await session.commit()


async def resolve_thread_by_message(
    chat_id: str,
    message_id: str | None,
) -> InteractionThread | None:
    if not chat_id or not message_id:
        return None
    session_factory = get_session_factory()
    async with session_factory() as session:
        binding = (
            (
                await session.execute(
                    select(BotMessageBinding)
                    .where(
                        BotMessageBinding.chat_id == chat_id,
                        BotMessageBinding.message_id == message_id,
                        BotMessageBinding.thread_id.isnot(None),
                    )
                    .order_by(desc(BotMessageBinding.created_at), desc(BotMessageBinding.id))
                )
            )
            .scalars()
            .first()
        )
        if binding is None or binding.thread_id is None:
            return None
        thread = await session.get(InteractionThread, binding.thread_id)
        if thread is None:
            return None
        thread.last_activity_at = _utcnow()
        await session.commit()
        await session.refresh(thread)
        return thread


async def attach_research_session(thread_id: int | None, session_id: int | None) -> None:
    if thread_id is None or session_id is None:
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        thread = await session.get(InteractionThread, thread_id)
        if thread is None:
            return
        now = _utcnow()
        thread.research_session_id = session_id
        thread.last_activity_at = now
        thread.updated_at = now
        await session.commit()


async def touch_thread(thread_id: int | None, *, status: str | None = None) -> None:
    if thread_id is None:
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        thread = await session.get(InteractionThread, thread_id)
        if thread is None:
            return
        now = _utcnow()
        thread.last_activity_at = now
        thread.updated_at = now
        if status:
            thread.status = status
        await session.commit()


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
