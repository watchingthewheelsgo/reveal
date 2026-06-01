"""Research workflows anchored to a specific social update."""

import re
from dataclasses import dataclass

from sqlalchemy import desc, select

from server.db.engine import get_session_factory
from server.db.models import ConversationMessage, ResearchSession, SocialPost
from server.research.claude_sdk_runtime import AgentRunResult, AgentRuntimeError, run_agent


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
    result = await _run_new_agent(_deep_prompt(post, focus))
    session_id = await _create_session(chat_id, post.id, focus or _default_topic(post))
    await _save_answer(session_id, result.answer, result.agent_session_id)
    return ResearchRun(session_id=session_id, post=post, answer=result.answer)


async def ask_about_post(chat_id: str, post_ref: str, question: str) -> str:
    if not question.strip():
        raise ResearchError("请提供问题。")
    post = await resolve_social_post(post_ref)
    session = await _find_active_session_for_post(chat_id, post)
    if session is None:
        result = await _run_new_agent(_ask_prompt(post, question))
        session_id = await _create_session(chat_id, post.id, _default_topic(post))
    else:
        result = await _run_agent_for_session(session, post, _ask_prompt(post, question))
        session_id = session.id
    await _append_message(session_id, "user", question)
    await _append_message(session_id, "assistant", result.answer, result.agent_session_id)
    return result.answer


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
    if not message.strip():
        return None
    topic = await get_active_topic(chat_id)
    if topic is None:
        return None
    post = await resolve_social_post(str(topic.source_id))
    result = await _run_agent_for_session(topic, post, _topic_prompt(post, message))
    await _append_message(topic.id, "user", message)
    await _append_message(topic.id, "assistant", result.answer, result.agent_session_id)
    return result.answer


async def summarize_topic(chat_id: str) -> str:
    topic = await get_active_topic(chat_id)
    if topic is None:
        raise ResearchError("当前没有活跃研究线程。")
    post = await resolve_social_post(str(topic.source_id))
    history = await _load_history(topic.id, limit=20)
    if not history:
        return f"当前线程基于 @{post.username} 的更新，还没有进一步对话。"

    history_text = "\n".join(f"{message.role}: {message.content}" for message in history)
    prompt = f"""请总结当前研究线程。

要求:
1. 用中文输出。
2. 保留关键结论、证据来源、仍需验证的问题。
3. 如果存在不确定性，明确标出。

原始更新:
{_post_context(post)}

历史对话:
{history_text}
"""
    result = await _run_agent_for_session(topic, post, prompt)
    await _append_message(topic.id, "assistant", result.answer, result.agent_session_id)
    return result.answer


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
            agent_runtime="claude_sdk",
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
    existing = await _find_active_session_for_post(chat_id, post)
    if existing:
        return existing

    session_id = await _create_session(chat_id, post.id, topic)
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession).where(ResearchSession.id == session_id)
        )
        return result.scalar_one()


async def _find_active_session_for_post(chat_id: str, post: SocialPost) -> ResearchSession | None:
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
        return existing


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


async def _run_new_agent(prompt: str) -> AgentRunResult:
    try:
        return await run_agent(prompt)
    except AgentRuntimeError as exc:
        raise ResearchError(exc.user_message) from exc


async def _run_agent_for_session(
    research_session: ResearchSession,
    post: SocialPost,
    prompt: str,
) -> AgentRunResult:
    if not research_session.agent_session_id:
        return await _run_new_agent(prompt)

    try:
        return await run_agent(prompt, resume=research_session.agent_session_id)
    except AgentRuntimeError as exc:
        if not _is_resume_error(exc):
            raise ResearchError(exc.user_message) from exc

    await _set_agent_session_id(research_session.id, None)
    history = await _load_history(research_session.id, limit=20)
    return await _run_new_agent(_resume_rebuild_prompt(post, history, prompt))


async def _set_agent_session_id(session_id: int, agent_session_id: str | None) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession).where(ResearchSession.id == session_id)
        )
        research_session = result.scalar_one_or_none()
        if research_session:
            research_session.agent_session_id = agent_session_id
            await session.commit()


def _is_resume_error(exc: AgentRuntimeError) -> bool:
    text = f"{exc} {exc.user_message}".lower()
    markers = ["resume", "session", "conversation", "not found", "does not exist", "invalid"]
    return any(marker in text for marker in markers)


def _resume_rebuild_prompt(
    post: SocialPost,
    history: list[ConversationMessage],
    prompt: str,
) -> str:
    history_text = "\n".join(f"{message.role}: {message.content}" for message in history)
    if not history_text:
        history_text = "（无历史对话）"
    return f"""上一个 Agent 会话无法恢复。
请基于 Reveal 保存的上下文继续研究，并开启新的 Agent 会话。

原始更新:
{_post_context(post)}

已保存的历史对话:
{history_text}

当前任务:
{prompt}
"""


async def _save_answer(
    session_id: int,
    answer: str,
    agent_session_id: str | None = None,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession).where(ResearchSession.id == session_id)
        )
        research_session = result.scalar_one()
        research_session.answer = answer
        research_session.status = "active"
        if agent_session_id:
            research_session.agent_session_id = agent_session_id
        session.add(ConversationMessage(session_id=session_id, role="assistant", content=answer))
        await session.commit()


async def _append_message(
    session_id: int,
    role: str,
    content: str,
    agent_session_id: str | None = None,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ResearchSession).where(ResearchSession.id == session_id)
        )
        research_session = result.scalar_one_or_none()
        if research_session:
            research_session.status = "active"
            if agent_session_id:
                research_session.agent_session_id = agent_session_id
        session.add(ConversationMessage(session_id=session_id, role=role, content=content))
        await session.commit()


def _deep_prompt(post: SocialPost, focus: str) -> str:
    return f"""请围绕下面这条 Twitter/X 更新做深度研究。

要求:
1. 使用 WebSearch / WebFetch 获取外部证据。
2. 覆盖背景、可信度、潜在影响、反方观点、后续观察点。
3. 区分事实、推断和不确定性。
4. 输出中文。
5. 末尾列出来源 URL。
6. 不要读取本地文件、运行命令或修改文件。

原始更新:
{_post_context(post)}

研究重点:
{focus or "背景、可信度、潜在影响、反方观点、后续观察点"}
"""


def _ask_prompt(post: SocialPost, question: str) -> str:
    return f"""请基于下面这条 Twitter/X 更新回答用户问题。

要求:
1. 可以使用 WebSearch / WebFetch 补充外部证据。
2. 如果问题需要事实核验，先搜索再回答。
3. 区分事实、推断和不确定性。
4. 输出中文，并列出来源 URL。
5. 不要读取本地文件、运行命令或修改文件。

原始更新:
{_post_context(post)}

用户问题:
{question}
"""


def _topic_prompt(post: SocialPost, message: str) -> str:
    return f"""当前对话绑定下面这条 Twitter/X 更新。请回答用户的新问题。

要求:
1. 需要额外事实时，使用 WebSearch / WebFetch 做外部研究。
2. 保持多轮研究上下文，不要把回答降级成简单摘要。
3. 区分事实、推断和不确定性。
4. 输出中文，并列出来源 URL。
5. 不要读取本地文件、运行命令或修改文件。

原始更新:
{_post_context(post)}

用户消息:
{message}
"""


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


def _default_topic(post: SocialPost) -> str:
    content = _strip_urls(post.content or "").strip()
    if content:
        return content[:120]
    return f"@{post.username} update {post.tweet_id}"


def _strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text)
