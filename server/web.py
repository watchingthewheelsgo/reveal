"""Web workspace routes for Reveal."""

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import desc, select

from server.db.engine import get_session_factory
from server.db.models import (
    ConversationMessage,
    InteractionThread,
    ResearchSession,
    SocialPost,
    TwitterState,
)
from server.events.feed import get_event_detail, list_event_items
from server.research.service import ResearchError, ask_about_post, run_deep_research
from server.runtime.jobs import list_recent_job_runs
from server.system.catalog import get_system_catalog_payload

WEB_CHAT_ID = "web"
STATIC_DIR = Path(__file__).parent / "static"

router = APIRouter()


class ResearchRequest(BaseModel):
    focus: str = ""


class AskRequest(BaseModel):
    question: str


@router.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/accounts")
async def list_accounts():
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(TwitterState).order_by(TwitterState.username.asc()))
        states = result.scalars().all()
    return {
        "accounts": [
            {
                "username": state.username,
                "is_active": state.is_active,
                "last_tweet_epoch": state.last_tweet_epoch,
                "last_check_at": _iso(state.last_check_at),
            }
            for state in states
        ]
    }


@router.get("/api/posts")
async def list_posts(
    limit: int = Query(default=80, ge=1, le=200),
    username: str | None = Query(default=None),
    q: str | None = Query(default=None),
):
    session_factory = get_session_factory()
    async with session_factory() as session:
        statement = select(SocialPost)
        if username:
            statement = statement.where(SocialPost.username == username.strip().lstrip("@"))
        if q:
            pattern = f"%{q.strip()}%"
            statement = statement.where(SocialPost.content.ilike(pattern))
        result = await session.execute(
            statement.order_by(desc(SocialPost.posted_at), desc(SocialPost.id)).limit(limit)
        )
        posts = result.scalars().all()
        research = await _latest_research_by_post(session, [post.id for post in posts])

    return {
        "posts": [_post_summary(post, research.get(post.id)) for post in posts],
        "count": len(posts),
    }


@router.get("/api/events")
async def list_events(
    limit: int = Query(default=80, ge=1, le=200),
    source_type: str | None = Query(default=None),
    ticker: str | None = Query(default=None),
    q: str | None = Query(default=None),
):
    items = await list_event_items(
        limit=limit,
        source_type=source_type,
        ticker=ticker,
        q=q,
    )
    return {"events": [item.to_dict() for item in items], "count": len(items)}


@router.get("/api/events/{source_type}/{source_id}")
async def get_event(source_type: str, source_id: int):
    payload = await get_event_detail(source_type, source_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return payload


@router.get("/api/threads/{thread_id}")
async def get_thread(thread_id: int):
    session_factory = get_session_factory()
    async with session_factory() as session:
        thread = await session.get(InteractionThread, thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")

        research = None
        messages: list[ConversationMessage] = []
        if thread.research_session_id:
            research = await session.get(ResearchSession, thread.research_session_id)
            if research:
                result = await session.execute(
                    select(ConversationMessage)
                    .where(ConversationMessage.session_id == research.id)
                    .order_by(ConversationMessage.created_at, ConversationMessage.id)
                )
                messages = list(result.scalars().all())

    return {
        "thread": _thread_detail(thread),
        "research": _research_detail(research, messages) if research else None,
    }


@router.get("/api/research/{session_id}")
async def get_research(session_id: int):
    session_factory = get_session_factory()
    async with session_factory() as session:
        research = await session.get(ResearchSession, session_id)
        if research is None:
            raise HTTPException(status_code=404, detail="Research session not found")
        result = await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.session_id == research.id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        )
        messages = list(result.scalars().all())
    return {"research": _research_detail(research, messages)}


@router.get("/api/watchlist/stocks")
async def list_stock_watchlist(chat_id: str | None = Query(default=None)):
    from server.stock.watchlist import get_stock_watch_list_payload

    return await get_stock_watch_list_payload(chat_id)


@router.get("/api/system/modules")
async def list_system_modules():
    return get_system_catalog_payload()


@router.get("/api/system/jobs")
async def list_system_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    job_id: str | None = Query(default=None),
):
    return {"jobs": await list_recent_job_runs(limit=limit, job_id=job_id)}


@router.get("/api/posts/{post_id}")
async def get_post(post_id: int):
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(SocialPost).where(SocialPost.id == post_id))
        post = result.scalar_one_or_none()
        if post is None:
            raise HTTPException(status_code=404, detail="Post not found")

        research_result = await session.execute(
            select(ResearchSession)
            .where(ResearchSession.source_type == "twitter", ResearchSession.source_id == post.id)
            .order_by(desc(ResearchSession.updated_at), desc(ResearchSession.created_at))
        )
        research_sessions = research_result.scalars().all()
        messages_by_session: dict[int, list[ConversationMessage]] = {}
        if research_sessions:
            session_ids = [item.id for item in research_sessions]
            message_result = await session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.session_id.in_(session_ids))
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
            )
            for message in message_result.scalars().all():
                messages_by_session.setdefault(message.session_id, []).append(message)

    return {
        "post": _post_detail(post),
        "research_sessions": [
            _research_detail(item, messages_by_session.get(item.id, []))
            for item in research_sessions
        ],
    }


@router.post("/api/posts/{post_id}/deep")
async def deep_research(post_id: int, payload: ResearchRequest):
    try:
        result = await run_deep_research(WEB_CHAT_ID, str(post_id), payload.focus)
    except ResearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Web deep research failed for post_id={}", post_id)
        raise HTTPException(status_code=500, detail="Research agent failed") from exc
    return {
        "session_id": result.session_id,
        "post_id": result.post.id if result.post else post_id,
        "answer": result.answer,
    }


@router.post("/api/posts/{post_id}/ask")
async def ask_post(post_id: int, payload: AskRequest):
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question is required")
    try:
        answer = await ask_about_post(WEB_CHAT_ID, str(post_id), payload.question)
    except ResearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Web ask research failed for post_id={}", post_id)
        raise HTTPException(status_code=500, detail="Research agent failed") from exc
    return {"post_id": post_id, "answer": answer}


async def _latest_research_by_post(session, post_ids: list[int]) -> dict[int, ResearchSession]:
    if not post_ids:
        return {}
    result = await session.execute(
        select(ResearchSession)
        .where(ResearchSession.source_type == "twitter", ResearchSession.source_id.in_(post_ids))
        .order_by(desc(ResearchSession.updated_at), desc(ResearchSession.created_at))
    )
    latest: dict[int, ResearchSession] = {}
    for item in result.scalars().all():
        if item.source_id not in latest:
            latest[item.source_id] = item
    return latest


def _post_summary(post: SocialPost, research: ResearchSession | None) -> dict[str, Any]:
    return {
        "id": post.id,
        "username": post.username,
        "tweet_id": post.tweet_id,
        "tweet_url": post.tweet_url,
        "content": post.content,
        "preview": _compact(post.summary or post.content, 220),
        "summary": post.summary,
        "posted_at": _iso(post.posted_at),
        "created_at": _iso(post.created_at),
        "labels": _post_labels(post),
        "link_count": len(post.links or []),
        "media_count": len(post.media or []),
        "reference_count": len(post.referenced_tweets or []),
        "is_noteworthy": post.is_noteworthy,
        "attention_reason": post.attention_reason,
        "research": _research_summary(research) if research else None,
    }


def _post_detail(post: SocialPost) -> dict[str, Any]:
    payload = _post_summary(post, None)
    payload.update(
        {
            "translated_content": post.translated_content,
            "links": post.links or [],
            "media": post.media or [],
            "referenced_tweets": post.referenced_tweets or [],
            "raw_json": post.raw_json or {},
        }
    )
    return payload


def _research_summary(research: ResearchSession) -> dict[str, Any]:
    return {
        "id": research.id,
        "status": research.status,
        "topic": research.topic,
        "updated_at": _iso(research.updated_at),
        "has_answer": bool(research.answer),
    }


def _research_detail(
    research: ResearchSession,
    messages: list[ConversationMessage],
) -> dict[str, Any]:
    data = _research_summary(research)
    data.update(
        {
            "answer": research.answer,
            "agent_runtime": research.agent_runtime,
            "agent_session_id": research.agent_session_id,
            "created_at": _iso(research.created_at),
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "created_at": _iso(message.created_at),
                }
                for message in messages
            ],
        }
    )
    return data


def _thread_detail(thread: InteractionThread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "platform": thread.platform,
        "chat_id": thread.chat_id,
        "root_message_id": thread.root_message_id,
        "source_type": thread.source_type,
        "source_id": thread.source_id,
        "source_key": thread.source_key,
        "research_session_id": thread.research_session_id,
        "status": thread.status,
        "created_at": _iso(thread.created_at),
        "updated_at": _iso(thread.updated_at),
        "last_activity_at": _iso(thread.last_activity_at),
    }


def _post_labels(post: SocialPost) -> list[str]:
    labels = []
    if post.is_repost:
        labels.append("repost")
    if post.is_quote:
        labels.append("quote")
    if post.is_reply:
        labels.append("reply")
    return labels


def _compact(text: str | None, limit: int) -> str:
    value = " ".join((text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
