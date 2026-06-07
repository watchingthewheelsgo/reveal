"""Claude Agent SDK runtime configured for DeepSeek's Anthropic-compatible API."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    ServerToolResultBlock,
    SystemMessage,
    ToolResultBlock,
    ToolUseBlock,
    query,
)
from loguru import logger

from config.settings import get_settings
from server.capabilities.registry import (
    BUILTIN_AGENT_TOOLS,
    DISALLOWED_LOCAL_TOOLS,
    agent_allowed_tools,
    agent_mcp_tool_names,
    format_agent_tool_catalog,
)
from server.research.sdk_mcp import build_reveal_sdk_mcp_server

REVEAL_MCP_TOOLS = agent_mcp_tool_names()
AGENT_ALLOWED_TOOLS = agent_allowed_tools()
AgentEffort = Literal["low", "medium", "high", "xhigh", "max"]
ProgressCallback = Callable[[str, str], Awaitable[None]]


@dataclass
class AgentRunResult:
    answer: str
    agent_session_id: str | None = None


class AgentRuntimeError(RuntimeError):
    def __init__(self, message: str, user_message: str):
        super().__init__(message)
        self.user_message = user_message


class AgentConfigurationError(AgentRuntimeError):
    pass


async def run_agent(
    prompt: str,
    resume: str | None = None,
    on_progress: ProgressCallback | None = None,
    _retrying_pseudo_tools: bool = False,
) -> AgentRunResult:
    settings = get_settings()
    token = settings.get_agent_auth_token()
    if not token:
        raise AgentConfigurationError(
            "Claude Agent SDK runtime requires ANTHROPIC_AUTH_TOKEN or DEEPSEEK_API_KEY.",
            "研究 Agent 未配置 DeepSeek API Key。请设置 ANTHROPIC_AUTH_TOKEN 或 DEEPSEEK_API_KEY。",
        )

    base_url = settings.get_agent_base_url()
    model = settings.get_agent_model()
    opus_model = settings.get_agent_opus_model()
    sonnet_model = settings.get_agent_sonnet_model()
    haiku_model = settings.get_agent_haiku_model()
    env = {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_AUTH_TOKEN": token,
        "ANTHROPIC_API_KEY": token,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": opus_model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": sonnet_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": haiku_model,
        "CLAUDE_CODE_SUBAGENT_MODEL": haiku_model,
        "CLAUDE_CODE_EFFORT_LEVEL": settings.agent_effort,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }

    options = ClaudeAgentOptions(
        tools=BUILTIN_AGENT_TOOLS,
        allowed_tools=AGENT_ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_LOCAL_TOOLS,
        strict_mcp_config=True,
        mcp_servers={"reveal": build_reveal_sdk_mcp_server()},
        permission_mode="dontAsk",
        model=model,
        max_turns=settings.agent_max_turns,
        cwd=Path.cwd(),
        env=env,
        effort=cast(AgentEffort, settings.agent_effort),
        resume=resume,
        setting_sources=[],
        extra_args={"bare": None},
        system_prompt=(
            "你是 Reveal 美股交易助手的研究代理。\n\n"
            f"{format_agent_tool_catalog()}\n\n"
            "工作原则:\n"
            "1. 先用内部工具 (stock_quote, technical_analysis 等) 获取精确数据\n"
            "2. 再用 WebSearch/WebFetch 补充最新信息和外部观点\n"
            "3. 结合用户持仓 (portfolio) 给出个性化建议\n"
            "4. 区分事实、推断和不确定性\n"
            "5. 输出中文，末尾列出来源 URL\n"
            "6. 不要读取本地文件、运行命令或修改文件\n"
            "7. 必须通过真实工具调用获取数据；不要在正文中输出 JSON 形式的 tool/arguments 伪调用。"
        ),
    )

    answer_parts: list[str] = []
    observation_parts: list[str] = []
    agent_session_id = resume
    result_answer: str | None = None
    result_error: str | None = None
    tool_use_count = 0
    logger.info(
        "Research agent run start: resume={} model={} max_turns={} tools={}",
        bool(resume),
        model,
        settings.agent_max_turns,
        AGENT_ALLOWED_TOOLS,
    )

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                session_id = message.data.get("session_id")
                if session_id:
                    agent_session_id = str(session_id)
                    logger.info("Research agent session initialized: {}", agent_session_id)
                logger.info(
                    "Research agent MCP init: servers={} tools={}",
                    message.data.get("mcp_servers") or [],
                    len(message.data.get("tools") or []),
                )
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        tool_use_count += 1
                        detail = _format_tool_progress(block)
                        logger.info("Research agent tool use: {}", detail)
                        if on_progress:
                            await on_progress("tool_use", detail)
                    text = getattr(block, "text", None)
                    if text:
                        answer_parts.append(str(text))
                    observation = _tool_result_observation(block)
                    if observation:
                        observation_parts.append(observation)
            elif isinstance(message, ResultMessage):
                if message.session_id:
                    agent_session_id = message.session_id
                if message.is_error:
                    result_error = (
                        message.result or message.stop_reason or "Agent SDK execution failed."
                    )
                elif message.result:
                    result_answer = message.result
    except CLINotFoundError as exc:
        logger.exception("Claude Agent SDK CLI was not found")
        raise AgentConfigurationError(
            str(exc),
            "研究 Agent 未找到 Claude Code CLI。请先安装或修复 claude-agent-sdk 的 bundled CLI。",
        ) from exc
    except (CLIConnectionError, ProcessError, ClaudeSDKError) as exc:
        logger.exception("Claude Agent SDK execution failed")
        raise AgentRuntimeError(str(exc), _user_message_for_exception(exc)) from exc
    except Exception as exc:
        if _is_max_turns_error(str(exc)):
            return _max_turns_result(
                answer_parts,
                observation_parts,
                tool_use_count,
                agent_session_id,
            )
        logger.exception("Claude Agent SDK execution failed unexpectedly")
        raise AgentRuntimeError(str(exc), _user_message_for_exception(exc)) from exc

    if result_error:
        if _is_max_turns_error(result_error):
            return _max_turns_result(
                answer_parts,
                observation_parts,
                tool_use_count,
                agent_session_id,
            )
        logger.error("Research agent result error: {}", result_error)
        raise AgentRuntimeError(result_error, _user_message_for_text(result_error))

    answer = result_answer or "\n".join(answer_parts).strip()
    if not answer:
        raise AgentRuntimeError(
            "Agent SDK returned no answer.",
            "研究 Agent 没有返回内容，请稍后重试。",
        )
    if _looks_like_pseudo_tool_call_answer(answer):
        logger.info(
            "Research agent returned pseudo tool calls without protocol tool use; "
            "retrying={} tool_uses={}",
            _retrying_pseudo_tools,
            tool_use_count,
        )
        if not _retrying_pseudo_tools:
            retry_prompt = (
                f"{prompt}\n\n"
                "重要: 你上一次输出了 JSON 形式的 tool/arguments，这不是有效答案。"
                "请通过 Claude Agent SDK 的真实工具调用执行这些工具，拿到结果后再给出中文研究结论。"
                "最终答案不要包含伪工具调用 JSON。"
            )
            return await run_agent(
                retry_prompt,
                resume=resume,
                on_progress=on_progress,
                _retrying_pseudo_tools=True,
            )
        raise AgentRuntimeError(
            "Agent returned pseudo tool-call JSON instead of executing tools.",
            "研究 Agent 没有真正执行工具调用，请稍后重试或换用更强模型。",
        )
    logger.info(
        "Research agent run complete: session_id={} answer_chars={} tool_uses={}",
        agent_session_id or "-",
        len(answer),
        tool_use_count,
    )
    return AgentRunResult(answer=answer, agent_session_id=agent_session_id)


def _format_tool_progress(block: ToolUseBlock) -> str:
    name = block.name or ""
    input_data = block.input if isinstance(block.input, dict) else {}
    if name == "WebSearch":
        q = input_data.get("query", "")
        return f"搜索: {q}"
    if name == "WebFetch":
        url = str(input_data.get("url", ""))
        return f"抓取: {url[:80]}"
    return f"工具: {name}"


def _tool_result_observation(block: object) -> str:
    if isinstance(block, ToolResultBlock):
        if block.is_error:
            return ""
        return _stringify_tool_result_content(block.content)
    if isinstance(block, ServerToolResultBlock):
        return _stringify_tool_result_content(block.content)
    return ""


def _stringify_tool_result_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return _clip_partial_context(content.strip())
    try:
        text = json.dumps(content, ensure_ascii=False, default=str)
    except TypeError:
        text = str(content)
    return _clip_partial_context(text.strip())


def _clip_partial_context(text: str, limit: int = 800) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _user_message_for_exception(exc: BaseException) -> str:
    return _user_message_for_text(str(exc))


def _is_max_turns_error(text: str) -> bool:
    normalized = text.lower()
    return (
        "maximum number of turns" in normalized
        or "max turns" in normalized
        or "reached maximum turns" in normalized
    )


def _max_turns_result(
    answer_parts: list[str],
    observation_parts: list[str],
    tool_use_count: int,
    agent_session_id: str | None = None,
) -> AgentRunResult:
    partial_answer = _partial_answer_from_max_turns(answer_parts, observation_parts, tool_use_count)
    logger.warning(
        "Research agent max turns reached; returning partial answer: "
        "session_id={} answer_chars={} tool_uses={}",
        agent_session_id or "-",
        len(partial_answer),
        tool_use_count,
    )
    return AgentRunResult(answer=partial_answer, agent_session_id=agent_session_id)


def _partial_answer_from_max_turns(
    answer_parts: list[str],
    observation_parts: list[str],
    tool_use_count: int,
) -> str:
    answer = "\n".join(part.strip() for part in answer_parts if part.strip()).strip()
    if not answer and observation_parts:
        snippets = "\n\n".join(
            f"- {_clip_partial_context(part, 500)}"
            for part in observation_parts[-6:]
            if part.strip()
        )
        answer = f"已获取的信息片段：\n{snippets}".strip()
    if not answer:
        answer = "本次运行已执行若干步骤，但没有收到可直接整理的文本结果。"
    return (
        f"阶段性总结：研究 Agent 已达到本次最大轮数限制，"
        f"下面是基于已获取信息整理的当前结论。\n\n{answer}\n\n"
        f"已执行工具步骤：{tool_use_count}。如需继续，可以在这条结果下继续追问。"
    )


def _user_message_for_text(text: str) -> str:
    normalized = text.lower()
    if "authentication" in normalized or "401" in normalized or "auth" in normalized:
        return "研究 Agent 认证失败，请检查 DeepSeek API Key。"
    if "rate limit" in normalized or "429" in normalized:
        return "研究 Agent 触发限流，请稍后重试。"
    if "billing" in normalized or "payment" in normalized or "402" in normalized:
        return "研究 Agent 计费状态异常，请检查 DeepSeek 账户。"
    if "resume" in normalized or "session" in normalized:
        return "研究 Agent 会话恢复失败，请重新发起这次研究。"
    return "研究 Agent 执行失败，请稍后重试。"


def _looks_like_pseudo_tool_call_answer(text: str) -> bool:
    normalized = text.lower()
    tool_markers = normalized.count('"tool"') + normalized.count("'tool'")
    argument_markers = normalized.count('"arguments"') + normalized.count("'arguments'")
    xml_tool_markers = (
        "<function_calls" in normalized or "<invoke " in normalized or "<parameter " in normalized
    )
    bracket_tool_markers = "[调用 " in normalized or "[call " in normalized
    known_tools = [*REVEAL_MCP_TOOLS, "websearch", "webfetch"]
    known_tool_mentions = sum(1 for tool in known_tools if tool in normalized)
    return known_tool_mentions >= 1 and (
        (tool_markers >= 1 and argument_markers >= 1) or xml_tool_markers or bracket_tool_markers
    )
