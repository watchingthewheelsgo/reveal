"""Persist bindings between IM messages and Reveal source objects."""

from sqlalchemy import desc, select

from server.db.engine import get_session_factory
from server.db.models import BotMessageBinding


async def bind_message_to_source(
    chat_id: str,
    message_id: str | None,
    source_type: str,
    source_id: int | None,
    *,
    platform: str | None = None,
    thread_id: int | None = None,
    role: str | None = None,
) -> None:
    if not chat_id or not message_id or source_id is None:
        return
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
            existing.source_type = source_type
            existing.source_id = source_id
            if platform is not None:
                existing.platform = platform
            if thread_id is not None:
                existing.thread_id = thread_id
            if role is not None:
                existing.role = role
        else:
            session.add(
                BotMessageBinding(
                    chat_id=chat_id,
                    message_id=message_id,
                    source_type=source_type,
                    source_id=source_id,
                    platform=platform,
                    thread_id=thread_id,
                    role=role,
                )
            )
        await session.commit()


async def resolve_message_binding(
    chat_id: str,
    message_id: str | None,
) -> BotMessageBinding | None:
    if not chat_id or not message_id:
        return None
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(BotMessageBinding)
            .where(
                BotMessageBinding.chat_id == chat_id,
                BotMessageBinding.message_id == message_id,
            )
            .order_by(desc(BotMessageBinding.created_at), desc(BotMessageBinding.id))
        )
        return result.scalars().first()
