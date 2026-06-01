"""Research workflows anchored to a specific social update."""

import re
from dataclasses import dataclass
from typing import cast

from loguru import logger
from openai.types.chat import ChatCompletionMessageParam
from sqlalchemy import desc, select

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import ConversationMessage, ResearchSession, ResearchSource, SocialPost
from server.llm.client import get_llm_client
from server.research.fetcher import fetch_page_text
from server.research.search import SearchResult, get_search_provider


class ResearchError(ValueError):
    pass


@dataclass
class ResearchRun:
    session_id: int
    post: SocialPost
    answer: str


async def resolve_social_post(ref: str) -> SocialPost:
    ref = ref.strip()
    if not ref:
        raise ResearchError("缺少消息 ID。")

    session_factory = get_session_factory()
    async with session_factory() as session:
        if ref == "latest":
            result = await session.execute(
                select(SocialPost).order_by(desc(SocialPost.created_at), desc(SocialPost.posted_at))
            )
            post = result.scalars().first()
            if post:
                return post
            raise ResearchError("还没有可研究的 Twitter 更新。")

        post = None
        if ref.isdigit():
            result = await session.execute(select(SocialPost).where(SocialPost.id == int(ref)))
            post = result.scalar_one_or_none()
        if post is None:
            result = await session.execute(select(SocialPost).where(SocialPost.tweet_id == ref))
            post = result.scalar_one_or_none()
        if post is None:
            raise ResearchError(f"找不到消息: {ref}")
        return post


async def run_deep_research(chat_id: str, post_ref: str, focus: str = "") -> ResearchRun:
    post = await resolve_social_post(post_ref)
    session_id = await _create_session(chat_id, post.id, focus or _default_topic(post))
    sources = await _collect_sources(session_id, post, focus)
    answer = await _synthesize_report(post, sources, focus)
    await _save_answer(session_id, answer)
    return ResearchRun(session_id=session_id, post=post, answer=answer)


async def ask_about_post(chat_id: str, post_ref: str, question: str) -> str:
    if not question.strip():
        raise ResearchError("请提供问题。")
    post = await resolve_social_post(post_ref)
    session = await _get_or_create_session(chat_id, post, _default_topic(post))
    sources = await _load_sources(session.id)
    if not sources:
        sources = await _collect_sources(session.id, post, "")
    answer = await _answer_with_context(post, sources, question)
    await _append_message(session.id, "user", question)
    await _append_message(session.id, "assistant", answer)
    return answer


async def start_topic(chat_id: str, post_ref: str, focus: str = "") -> ResearchSession:
    post = await resolve_social_post(post_ref)
    session_id = await _create_session(chat_id, post.id, focus or _default_topic(post))
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession).where(ResearchSession.id == session_id)
        )
        topic = result.scalar_one()
        session.add(
            ConversationMessage(
                session_id=session_id,
                role="assistant",
                content=f"已基于 @{post.username} 的更新开启研究线程。",
            )
        )
        await session.commit()
        return topic


async def stop_topic(chat_id: str) -> bool:
    topic = await get_active_topic(chat_id)
    if topic is None:
        return False
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession).where(ResearchSession.id == topic.id)
        )
        current = result.scalar_one_or_none()
        if current is None:
            return False
        current.status = "closed"
        await session.commit()
    return True


async def get_active_topic(chat_id: str) -> ResearchSession | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession)
            .where(ResearchSession.chat_id == chat_id, ResearchSession.status == "active")
            .order_by(desc(ResearchSession.updated_at), desc(ResearchSession.created_at))
        )
        return result.scalars().first()


async def handle_topic_message(chat_id: str, message: str) -> str | None:
    topic = await get_active_topic(chat_id)
    if topic is None:
        return None
    post = await resolve_social_post(str(topic.source_id))
    sources = await _load_sources(topic.id)
    history = await _load_history(topic.id, limit=8)
    answer = await _continue_conversation(post, sources, history, message)
    await _append_message(topic.id, "user", message)
    await _append_message(topic.id, "assistant", answer)
    return answer


async def summarize_topic(chat_id: str) -> str:
    topic = await get_active_topic(chat_id)
    if topic is None:
        raise ResearchError("当前没有活跃研究线程。")
    post = await resolve_social_post(str(topic.source_id))
    history = await _load_history(topic.id, limit=20)
    if not history:
        return f"当前线程基于 @{post.username} 的更新，还没有进一步对话。"
    llm = get_llm_client()
    text = "\n".join(f"{m.role}: {m.content}" for m in history)
    if llm is None:
        return text[-1500:]
    return await llm.chat(
        [
            {"role": "system", "content": "用中文总结这段研究对话，保留结论、证据和待验证问题。"},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
    )


async def _create_session(chat_id: str, source_id: int, topic: str) -> int:
    session_factory = get_session_factory()
    async with session_factory() as session:
        previous = await session.execute(
            select(ResearchSession).where(
                ResearchSession.chat_id == chat_id,
                ResearchSession.status == "active",
            )
        )
        for item in previous.scalars().all():
            item.status = "closed"

        research_session = ResearchSession(
            chat_id=chat_id,
            source_type="twitter",
            source_id=source_id,
            topic=topic[:300],
            status="active",
        )
        session.add(research_session)
        await session.flush()
        session_id = research_session.id
        await session.commit()
        return session_id


async def _get_or_create_session(chat_id: str, post: SocialPost, topic: str) -> ResearchSession:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession)
            .where(
                ResearchSession.chat_id == chat_id,
                ResearchSession.source_id == post.id,
                ResearchSession.status == "active",
            )
            .order_by(desc(ResearchSession.updated_at), desc(ResearchSession.created_at))
        )
        existing = result.scalars().first()
        if existing:
            return existing

    session_id = await _create_session(chat_id, post.id, topic)
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession).where(ResearchSession.id == session_id)
        )
        return result.scalar_one()


async def _collect_sources(session_id: int, post: SocialPost, focus: str) -> list[ResearchSource]:
    settings = get_settings()
    search_provider = get_search_provider()
    search_results: list[SearchResult] = []
    if settings.is_search_configured():
        for query in _build_queries(post, focus)[:3]:
            try:
                search_results.extend(
                    await search_provider.search(query, settings.search_max_results)
                )
            except Exception as e:
                logger.warning(f"Search failed for query={query!r}: {e}")

    raw_sources = _base_sources(post)
    seen_urls = {source.url for source in raw_sources}
    for result in search_results:
        if result.url in seen_urls:
            continue
        raw_sources.append(
            ResearchSource(
                session_id=session_id,
                query=result.query,
                title=result.title[:500],
                url=result.url[:1000],
                snippet=result.snippet,
            )
        )
        seen_urls.add(result.url)

    pages_left = settings.research_fetch_max_pages
    for source in raw_sources:
        if pages_left <= 0 or source.extracted_text:
            continue
        title, text = await fetch_page_text(source.url)
        if title and source.title == source.url:
            source.title = title[:500]
        if text:
            source.extracted_text = text
            pages_left -= 1

    session_factory = get_session_factory()
    async with session_factory() as session:
        for source in raw_sources:
            source.session_id = session_id
            session.add(source)
        await session.commit()

        result = await session.execute(
            select(ResearchSource).where(ResearchSource.session_id == session_id)
        )
        return list(result.scalars().all())


def _base_sources(post: SocialPost) -> list[ResearchSource]:
    sources = [
        ResearchSource(
            session_id=0,
            query=None,
            title=f"@{post.username} 原始更新",
            url=post.tweet_url or f"tweet:{post.tweet_id}",
            snippet=post.content,
            extracted_text=_post_context(post),
        )
    ]
    if post.links:
        for link in post.links[:5]:
            sources.append(
                ResearchSource(
                    session_id=0,
                    query="source link",
                    title=str(link),
                    url=str(link),
                    snippet="原推包含的外部链接",
                )
            )
    if post.referenced_tweets:
        for ref in post.referenced_tweets[:3]:
            url = str(ref.get("url") or "")
            if not url:
                continue
            sources.append(
                ResearchSource(
                    session_id=0,
                    query="referenced tweet",
                    title=f"关联推文: {ref.get('type', 'reference')}",
                    url=url,
                    snippet=str(ref.get("text") or ""),
                    extracted_text=str(ref.get("text") or ""),
                )
            )
    return sources


async def _load_sources(session_id: int) -> list[ResearchSource]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSource).where(ResearchSource.session_id == session_id)
        )
        return list(result.scalars().all())


async def _load_history(session_id: int, limit: int) -> list[ConversationMessage]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.session_id == session_id)
            .order_by(desc(ConversationMessage.created_at), desc(ConversationMessage.id))
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))


async def _save_answer(session_id: int, answer: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession).where(ResearchSession.id == session_id)
        )
        research_session = result.scalar_one()
        research_session.answer = answer
        research_session.status = "active"
        session.add(ConversationMessage(session_id=session_id, role="assistant", content=answer))
        await session.commit()


async def _append_message(session_id: int, role: str, content: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession).where(ResearchSession.id == session_id)
        )
        research_session = result.scalar_one_or_none()
        if research_session:
            research_session.status = "active"
        session.add(ConversationMessage(session_id=session_id, role=role, content=content))
        await session.commit()


async def _synthesize_report(post: SocialPost, sources: list[ResearchSource], focus: str) -> str:
    llm = get_llm_client()
    if llm is None:
        return _fallback_report(post, sources, focus)

    prompt = f"""原始更新:
{_post_context(post)}

研究重点:
{focus or "请从背景、可信度、潜在影响、风险和后续关注点做深度解析。"}

证据材料:
{_sources_context(sources)}
"""
    return await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "你是 Reveal 的研究助手。基于给定材料做中文深度解析。"
                    "必须区分事实、推断和不确定性；不要编造来源；最后列出来源 URL。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )


async def _answer_with_context(
    post: SocialPost,
    sources: list[ResearchSource],
    question: str,
) -> str:
    llm = get_llm_client()
    if llm is None:
        return "LLM 未配置，无法基于研究材料回答问题。请先配置 OPENAI_API_KEY。"
    prompt = f"""原始更新:
{_post_context(post)}

证据材料:
{_sources_context(sources)}

问题:
{question}
"""
    return await llm.chat(
        [
            {
                "role": "system",
                "content": "基于原始更新和证据材料回答用户问题。用中文，标明推断和不确定性。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )


async def _continue_conversation(
    post: SocialPost,
    sources: list[ResearchSource],
    history: list[ConversationMessage],
    message: str,
) -> str:
    llm = get_llm_client()
    if llm is None:
        return "LLM 未配置，无法继续研究对话。请先配置 OPENAI_API_KEY。"

    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "system",
            "content": (
                "你是 Reveal 的研究助手。当前对话绑定一条 Twitter/X 更新。"
                "基于原始更新、已收集来源和对话历史回答；不要凭空编造。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"原始更新:\n{_post_context(post)}\n\n证据材料:\n{_sources_context(sources)}"
            ),
        },
    ]
    for item in history:
        role = "assistant" if item.role == "assistant" else "user"
        messages.append(cast(ChatCompletionMessageParam, {"role": role, "content": item.content}))
    messages.append(cast(ChatCompletionMessageParam, {"role": "user", "content": message}))
    return await llm.chat(messages, temperature=0.35)


def _fallback_report(post: SocialPost, sources: list[ResearchSource], focus: str) -> str:
    lines = [
        f"*深度解析 #{post.id}*",
        "",
        f"原始更新: @{post.username}",
        post.content[:800] if post.content else "（无正文）",
    ]
    if focus:
        lines.extend(["", f"研究重点: {focus}"])
    lines.extend(
        [
            "",
            "LLM 未配置，以下是已收集的材料索引，尚未生成综合判断。",
            "",
            "来源:",
        ]
    )
    for idx, source in enumerate(sources, start=1):
        lines.append(f"{idx}. {source.title} — {source.url}")
        if source.snippet:
            lines.append(f"   {source.snippet[:220]}")
    return "\n".join(lines)


def _build_queries(post: SocialPost, focus: str) -> list[str]:
    text = _strip_urls(post.content or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text[:180]
    queries = []
    if focus:
        queries.append(f"{focus} {text}".strip())
        queries.append(f"{focus} @{post.username}".strip())
    if text:
        queries.append(text)
        queries.append(f"@{post.username} {text}".strip())
    if post.tweet_url:
        queries.append(post.tweet_url)

    unique: list[str] = []
    for query in queries:
        if query and query not in unique:
            unique.append(query)
    return unique


def _post_context(post: SocialPost) -> str:
    lines = [
        f"post_id: {post.id}",
        f"author: @{post.username}",
        f"tweet_id: {post.tweet_id}",
    ]
    if post.tweet_url:
        lines.append(f"url: {post.tweet_url}")
    labels = []
    if post.is_quote:
        labels.append("quote")
    if post.is_repost:
        labels.append("repost")
    if post.is_reply:
        labels.append("reply")
    if labels:
        lines.append("type: " + ", ".join(labels))
    lines.extend(["content:", post.content or "（无正文）"])
    if post.links:
        lines.append("links: " + ", ".join(str(link) for link in post.links[:8]))
    if post.media:
        lines.append("media: " + ", ".join(str(item.get("url")) for item in post.media[:4]))
    if post.referenced_tweets:
        lines.append("referenced:")
        for ref in post.referenced_tweets[:3]:
            lines.append(f"- {ref.get('type')}: {ref.get('url')} {ref.get('text', '')}")
    return "\n".join(lines)


def _sources_context(sources: list[ResearchSource]) -> str:
    blocks = []
    for idx, source in enumerate(sources[:10], start=1):
        text = source.extracted_text or source.snippet or ""
        blocks.append(
            "\n".join(
                [
                    f"[{idx}] {source.title}",
                    f"URL: {source.url}",
                    f"Query: {source.query or 'source'}",
                    f"Text: {text[:2500]}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _default_topic(post: SocialPost) -> str:
    content = _strip_urls(post.content or "").strip()
    if content:
        return content[:120]
    return f"@{post.username} update {post.tweet_id}"


def _strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text)
