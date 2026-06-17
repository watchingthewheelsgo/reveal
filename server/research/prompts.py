"""Centralized prompt builders and Agent tool access."""

from __future__ import annotations

from server.capabilities.registry import (
    BUILTIN_AGENT_TOOLS,
    agent_mcp_tool_names,
    format_agent_tool_catalog,
)
from server.db.models import ConversationMessage, ResearchSession, SocialPost
from server.events.types import compact_event_context
from server.research.market_skills import market_skill_prompt_context
from server.social.events import event_from_social_post

_ALL_MCP_TOOLS = agent_mcp_tool_names()
_AGENT_TOOLS = [*BUILTIN_AGENT_TOOLS, *_ALL_MCP_TOOLS]


def agent_allowed_tools() -> list[str]:
    return list(dict.fromkeys(_AGENT_TOOLS))


def agent_system_prompt(allowed_tools: list[str]) -> str:
    return (
        "你是 Reveal 美股交易助手的研究代理。\n\n"
        "你可以使用已授权的 Reveal MCP 工具和 built-in web tools。"
        "先基于用户意图制定 plan，再选择真实工具执行；不要用关键词猜测任务类型。\n\n"
        f"{format_agent_tool_catalog(allowed_tools=allowed_tools)}\n\n"
        "工作原则:\n"
        "1. 简单状态/列表/添加/取消类任务，直接调用对应 Reveal MCP 工具，不要做泛化研究。\n"
        "2. 研究类任务先 plan，再用内部工具获取精确数据，并用 WebSearch/WebFetch 补充最新信息。\n"
        "3. 结合用户持仓和关注标的给出个性化影响判断。\n"
        "4. 区分事实、推断和不确定性；不要把观点当事实。\n"
        "5. 输出中文，末尾列出真实使用过的来源 URL；没有来源则说明未使用外部来源。\n"
        "6. 不要读取本地文件、运行命令或修改文件。\n"
        "7. 用户明确说持有某 ticker 但不想记录数量/成本、只为后续消息提醒时，"
        "才可调用持仓关注标记工具；它不代表真实交易。\n"
        "8. 必须通过真实工具调用获取数据；不要在正文中输出 JSON 形式的 tool/arguments 伪调用。\n"
        "9. 你正在处理当前请求；除非用户询问命令用法，不要建议用户改用 /research、/topic "
        "或其他命令来完成同一个问题。\n"
        "10. 最终答案末尾追加一行机器可读元数据，格式必须是 "
        'REVEAL_METADATA: {"mentioned_tickers":["NVDA"],"confidence":"high"}。'
        "mentioned_tickers 只放你在工具结果或证据中确认相关的 ticker；如果没有则为空数组。"
    )


def deep_prompt(post: SocialPost, focus: str) -> str:
    return f"""请围绕下面这条 Twitter/X 更新做深度研究。

如果推文提到了具体股票，请用 stock_quote / technical_analysis 查数据。
用 portfolio 查看用户是否持有相关标的。
用 WebSearch 搜索外部证据，覆盖背景、可信度、潜在影响、反方观点。

原始更新:
{post_context(post)}

研究重点:
{focus or "背景、可信度、潜在影响、反方观点、后续观察点"}
"""


def ask_prompt(post: SocialPost, question: str) -> str:
    return f"""请基于下面这条 Twitter/X 更新回答用户问题。

需要数据时使用内部工具 (stock_quote, portfolio 等)，需要外部信息时用 WebSearch。

原始更新:
{post_context(post)}

用户问题:
{question}
"""


def topic_prompt(post: SocialPost, message: str) -> str:
    return f"""当前对话绑定下面这条 Twitter/X 更新。请回答用户的新问题。

需要数据时使用内部工具，需要外部信息时用 WebSearch。
保持多轮研究上下文，不要把回答降级成简单摘要。

原始更新:
{post_context(post)}

用户消息:
{message}
"""


def freeform_prompt(query: str, focus: str) -> str:
    return f"""用户有一个问题需要你帮忙研究。

请先用 portfolio 工具查看用户持仓，再结合其他工具和 WebSearch 给出个性化回答。

用户问题:
{query}

研究重点:
{focus or "综合分析"}
"""


def ticker_prompt(ticker: str, focus: str) -> str:
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


def freeform_followup_prompt(topic: ResearchSession, message: str) -> str:
    label = "Agent 会话" if topic.source_type == "agent" else "自由研究线程"
    return f"""当前对话是一个{label}。

研究主题: {topic.topic or topic.source_query or ""}

需要数据时请使用内部工具 (stock_quote, portfolio 等) 和 WebSearch。
保持多轮研究上下文，不要把回答降级成简单摘要。

用户消息:
{message}
"""


def agent_message_prompt(topic: ResearchSession, message: str, platform: str = "auto") -> str:
    return f"""用户正在 Reveal 的 IM Agent 会话里发送自然语言请求。

Reveal 的能力以 MCP tools 暴露给你。请根据用户意图选择真实工具执行，而不是输出工具调用文本。

原则:
1. 如果用户要执行系统操作，例如添加/移除 Twitter watch list、添加/移除 Reddit subreddit watch list、
添加/移除股票观察列表、
添加/移除持仓关注标记（用户说持有某股票但不想记录数量/成本，只为后续消息考虑影响）、
创建/查看/取消未来定时任务、获取某用户最新推文、获取 subreddit 最新帖子、
搜索本地推文/Reddit 帖子、查询股票、查看持仓、
查询交易日记或系统状态，
直接调用对应 Reveal MCP 工具。
   - 如果用户说“2小时后”、“今晚7点”、“明天早上”等未来时间后再推送/提醒/查询，
     调用 scheduled_task_create，而不是现在直接完成任务。
2. 如果用户要研究、解释、比较、验证事实或需要最新外部证据，
结合 Reveal MCP 工具和 WebSearch/WebFetch。
3. 如果意图不明确，向用户追问一个具体澄清问题。
4. 最终只返回用户可读的中文结果，不要输出伪 function_calls/XML/JSON 工具调用文本。

会话主题: {topic.topic or topic.source_query or ""}

当前会话上下文:
chat_id: {topic.chat_id}
platform: {platform}
如果调用 stock_watch_add、stock_watch_remove 或 stock_watch_list，必须使用上面的 chat_id；
添加/移除时也要传 platform。这样后续价格异动 alert 才能发回当前会话。
如果用户说“我持有/有仓位/后续关注某股票”但没有给价格数量，调用 portfolio_holding_add；
这个工具只用于后续消息个性化提醒，不表示真实买入/卖出。
如果用户说不再持有或取消这个持仓关注标记，调用 portfolio_holding_remove。
如果用户说“监控 r/stocks / 添加 subreddit / Reddit 关注列表”，调用 reddit_watch_add、
reddit_watch_remove 或 reddit_watch_list。Reddit 后台只推送 market/stock 强相关内容。
如果调用 scheduled_task_create、scheduled_task_list 或 scheduled_task_cancel，也必须使用
上面的 chat_id；创建任务时传 platform，并把用户原始时间短语放入 run_at_text。

用户消息:
{message}
"""


def resume_rebuild_prompt(
    post: SocialPost,
    history: list[ConversationMessage],
    prompt: str,
) -> str:
    history_text = conversation_history_text(history)
    return f"""上一个 Agent 会话无法恢复。
请基于 Reveal 保存的上下文继续研究，并开启新的 Agent 会话。

原始更新:
{post_context(post)}

已保存的历史对话:
{history_text}

当前任务:
{prompt}
"""


def resume_rebuild_freeform_prompt(
    research_session: ResearchSession,
    history: list[ConversationMessage],
    prompt: str,
) -> str:
    history_text = conversation_history_text(history)
    return f"""上一个 Agent 会话无法恢复。
请基于 Reveal 保存的上下文继续研究，并开启新的 Agent 会话。

研究主题: {research_session.topic or research_session.source_query or ""}

已保存的历史对话:
{history_text}

当前任务:
{prompt}
"""


def conversation_history_text(history: list[ConversationMessage]) -> str:
    history_text = "\n".join(f"{message.role}: {message.content}" for message in history)
    return history_text or "（无历史对话）"


def post_context(post: SocialPost) -> str:
    event = event_from_social_post(post)
    lines = [
        f"post_id: {post.id}",
        f"source: {event.source}",
        f"author: @{post.username}",
        f"event_id: {post.tweet_id}",
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
    lines.extend(["", "canonical_event:", compact_event_context(event)])
    if skill_context := market_skill_prompt_context(event):
        lines.extend(["", skill_context])
    return "\n".join(lines)
