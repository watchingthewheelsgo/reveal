"""Research workflows — supports tweet-anchored, ticker, and freeform research."""

import re
from dataclasses import dataclass

from sqlalchemy import desc, select

from server.db.engine import get_session_factory
from server.db.models import ConversationMessage, ResearchSession, SocialPost
from server.research.claude_sdk_runtime import (
    AgentRunResult,
    AgentRuntimeError,
    ProgressCallback,
    run_agent,
)


class ResearchError(ValueError):
    pass


@dataclass
class ResearchRun:
    session_id: int
    post: SocialPost | None
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


async def run_deep_research(
    chat_id: str,
    post_ref: str,
    focus: str = "",
    on_progress: ProgressCallback | None = None,
) -> ResearchRun:
    post = await resolve_social_post(post_ref)
    result = await _run_new_agent(_deep_prompt(post, focus), on_progress=on_progress)
    session_id = await _create_session(chat_id, post.id, focus or _default_topic(post))
    await _save_answer(session_id, result.answer, result.agent_session_id)
    return ResearchRun(session_id=session_id, post=post, answer=result.answer)


async def start_freeform_research(
    chat_id: str,
    query: str,
    focus: str = "",
    on_progress: ProgressCallback | None = None,
) -> ResearchRun:
    """Start research from a freeform question, not tied to any tweet."""
    prompt = _freeform_prompt(query, focus)
    result = await _run_new_agent(prompt, on_progress=on_progress)
    session_id = await _create_session(
        chat_id, None, query[:300], source_type="freeform", source_query=query
    )
    await _save_answer(session_id, result.answer, result.agent_session_id)
    return ResearchRun(session_id=session_id, post=None, answer=result.answer)


async def research_ticker(
    chat_id: str,
    ticker: str,
    focus: str = "",
    on_progress: ProgressCallback | None = None,
) -> ResearchRun:
    """Start research on a specific stock ticker."""
    prompt = _ticker_prompt(ticker, focus)
    result = await _run_new_agent(prompt, on_progress=on_progress)
    session_id = await _create_session(
        chat_id,
        None,
        f"{ticker} {focus}".strip()[:300],
        source_type="ticker",
        source_query=ticker,
    )
    await _save_answer(session_id, result.answer, result.agent_session_id)
    return ResearchRun(session_id=session_id, post=None, answer=result.answer)


async def ask_about_post(
    chat_id: str,
    post_ref: str,
    question: str,
    on_progress: ProgressCallback | None = None,
) -> str:
    if not question.strip():
        raise ResearchError("请提供问题。")
    post = await resolve_social_post(post_ref)
    session = await _find_active_session_for_post(chat_id, post)
    if session is None:
        result = await _run_new_agent(_ask_prompt(post, question), on_progress=on_progress)
        session_id = await _create_session(chat_id, post.id, _default_topic(post))
    else:
        result = await _run_agent_for_session(
            session, post, _ask_prompt(post, question), on_progress=on_progress
        )
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


async def handle_topic_message(
    chat_id: str,
    message: str,
    on_progress: ProgressCallback | None = None,
) -> str | None:
    if not message.strip():
        return None
    topic = await get_active_topic(chat_id)
    if topic is None:
        return None

    if topic.source_type == "twitter" and topic.source_id:
        post = await resolve_social_post(str(topic.source_id))
        prompt = _topic_prompt(post, message)
    else:
        prompt = _freeform_followup_prompt(topic, message)
        post = None

    result = await _run_agent_for_session(topic, post, prompt, on_progress=on_progress)
    await _append_message(topic.id, "user", message)
    await _append_message(topic.id, "assistant", result.answer, result.agent_session_id)
    return result.answer


async def summarize_topic(chat_id: str, on_progress: ProgressCallback | None = None) -> str:
    topic = await get_active_topic(chat_id)
    if topic is None:
        raise ResearchError("当前没有活跃研究线程。")

    history = await _load_history(topic.id, limit=20)
    history_text = "\n".join(f"{message.role}: {message.content}" for message in history)

    post = None
    if topic.source_type == "twitter" and topic.source_id:
        post = await resolve_social_post(str(topic.source_id))
        if not history:
            return f"当前线程基于 @{post.username} 的更新，还没有进一步对话。"
        source_section = f"原始更新:\n{_post_context(post)}"
    else:
        if not history:
            return f"当前研究线程 '{topic.topic or ''}' 还没有进一步对话。"
        source_section = f"研究主题: {topic.topic or topic.source_query or ''}"

    prompt = f"""请总结当前研究线程。

要求:
1. 用中文输出。
2. 保留关键结论、证据来源、仍需验证的问题。
3. 如果存在不确定性，明确标出。

{source_section}

历史对话:
{history_text}
"""
    result = await _run_agent_for_session(topic, post, prompt, on_progress=on_progress)
    await _append_message(topic.id, "assistant", result.answer, result.agent_session_id)
    return result.answer


async def _create_session(
    chat_id: str,
    source_id: int | None,
    topic: str,
    source_type: str = "twitter",
    source_query: str | None = None,
) -> int:
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
            source_type=source_type,
            source_id=source_id,
            source_query=source_query,
            topic=topic[:300],
            status="active",
        )
        session.add(research_session)
        await session.flush()
        session_id = research_session.id
        await session.commit()
        return session_id


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


async def _run_new_agent(
    prompt: str,
    on_progress: ProgressCallback | None = None,
) -> AgentRunResult:
    try:
        return await run_agent(prompt, on_progress=on_progress)
    except AgentRuntimeError as exc:
        raise ResearchError(exc.user_message) from exc


async def _run_agent_for_session(
    research_session: ResearchSession,
    post: SocialPost | None,
    prompt: str,
    on_progress: ProgressCallback | None = None,
) -> AgentRunResult:
    if not research_session.agent_session_id:
        return await _run_new_agent(prompt, on_progress=on_progress)

    try:
        return await run_agent(
            prompt, resume=research_session.agent_session_id, on_progress=on_progress
        )
    except AgentRuntimeError as exc:
        if not _is_resume_error(exc):
            raise ResearchError(exc.user_message) from exc

    await _set_agent_session_id(research_session.id, None)
    history = await _load_history(research_session.id, limit=20)
    if post:
        return await _run_new_agent(
            _resume_rebuild_prompt(post, history, prompt), on_progress=on_progress
        )
    return await _run_new_agent(
        _resume_rebuild_freeform_prompt(research_session, history, prompt),
        on_progress=on_progress,
    )


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
    tickers = _extract_tickers_from_text(answer)
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
        if tickers:
            existing = research_session.mentioned_tickers or []
            merged = list(dict.fromkeys([*existing, *tickers]))
            research_session.mentioned_tickers = merged
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

如果推文提到了具体股票，请用 stock_quote / technical_analysis 查数据。
用 portfolio 查看用户是否持有相关标的。
用 WebSearch 搜索外部证据，覆盖背景、可信度、潜在影响、反方观点。

原始更新:
{_post_context(post)}

研究重点:
{focus or "背景、可信度、潜在影响、反方观点、后续观察点"}
"""


def _ask_prompt(post: SocialPost, question: str) -> str:
    return f"""请基于下面这条 Twitter/X 更新回答用户问题。

需要数据时使用内部工具 (stock_quote, portfolio 等)，需要外部信息时用 WebSearch。

原始更新:
{_post_context(post)}

用户问题:
{question}
"""


def _topic_prompt(post: SocialPost, message: str) -> str:
    return f"""当前对话绑定下面这条 Twitter/X 更新。请回答用户的新问题。

需要数据时使用内部工具，需要外部信息时用 WebSearch。
保持多轮研究上下文，不要把回答降级成简单摘要。

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


def _freeform_prompt(query: str, focus: str) -> str:
    return f"""用户有一个问题需要你帮忙研究。

请先用 portfolio 工具查看用户持仓，再结合其他工具和 WebSearch 给出个性化回答。

用户问题:
{query}

研究重点:
{focus or "综合分析"}
"""


def _ticker_prompt(ticker: str, focus: str) -> str:
    return f"""请对 {ticker} 做深度研究。

请依次:
1. 用 technical_analysis 查技术指标
2. 用 stock_news 查最近新闻
3. 用 portfolio 查用户是否持有
4. 用 research_history 查过去的研究结论
5. 用 WebSearch 补充最新信息
6. 综合以上给出全面分析

研究重点:
{focus or "综合分析: 技术面、基本面、催化剂、风险"}
"""


def _freeform_followup_prompt(topic: ResearchSession, message: str) -> str:
    return f"""当前对话是一个自由研究线程。

研究主题: {topic.topic or topic.source_query or ""}

需要数据时请使用内部工具 (stock_quote, portfolio 等) 和 WebSearch。
保持多轮研究上下文，不要把回答降级成简单摘要。

用户消息:
{message}
"""


def _resume_rebuild_freeform_prompt(
    research_session: ResearchSession,
    history: list[ConversationMessage],
    prompt: str,
) -> str:
    history_text = "\n".join(f"{message.role}: {message.content}" for message in history)
    if not history_text:
        history_text = "（无历史对话）"
    return f"""上一个 Agent 会话无法恢复。
请基于 Reveal 保存的上下文继续研究，并开启新的 Agent 会话。

研究主题: {research_session.topic or research_session.source_query or ""}

已保存的历史对话:
{history_text}

当前任务:
{prompt}
"""


def _default_topic(post: SocialPost) -> str:
    content = _strip_urls(post.content or "").strip()
    if content:
        return content[:120]
    return f"@{post.username} update {post.tweet_id}"


_KNOWN_TICKERS = {
    "AAPL",
    "MSFT",
    "GOOGL",
    "GOOG",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "NFLX",
    "AMD",
    "CRM",
    "ADBE",
    "ORCL",
    "NOW",
    "INTU",
    "UBER",
    "JPM",
    "BAC",
    "GS",
    "V",
    "MA",
    "JNJ",
    "UNH",
    "PFE",
    "ABBV",
    "MRK",
    "XOM",
    "CVX",
    "COP",
    "HD",
    "NKE",
    "SBUX",
    "MCD",
    "COST",
    "WMT",
    "BA",
    "CAT",
    "GE",
    "RTX",
    "LMT",
    "PLTR",
    "SNOW",
    "DDOG",
    "CRWD",
    "ZS",
    "AVGO",
    "QCOM",
    "TXN",
    "INTC",
    "MU",
    "AMAT",
    "LRCX",
    "KLAC",
    "MRVL",
    "ARM",
    "SMCI",
    "DELL",
    "COIN",
    "SQ",
    "PYPL",
    "SHOP",
    "ROKU",
    "SNAP",
    "PINS",
    "RBLX",
    "ABNB",
    "BABA",
    "JD",
    "PDD",
    "NIO",
    "XPEV",
    "LI",
    "BRK",
    "DIS",
    "CMCSA",
    "T",
    "VZ",
    "TMUS",
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "VIX",
}
_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")


def _extract_tickers_from_text(text: str) -> list[str]:
    """Extract stock tickers from text using pattern matching against known tickers."""
    matches = _TICKER_RE.findall(text)
    found: list[str] = []
    seen: set[str] = set()
    for match in matches:
        if match in _KNOWN_TICKERS and match not in seen:
            found.append(match)
            seen.add(match)
    return found


def _strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text)
