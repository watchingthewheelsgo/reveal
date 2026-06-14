"""Claude Agent SDK runtime configured for DeepSeek's Anthropic-compatible API."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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
    agent_mcp_tool_names,
)
from server.research.agent_plan import AgentRunPlan, new_agent_run_plan
from server.research.prompts import (
    AgentToolProfile,
    agent_system_prompt,
    allowed_tools_for_profile,
)
from server.research.sdk_mcp import build_reveal_sdk_mcp_server

REVEAL_MCP_TOOLS = agent_mcp_tool_names()
AgentEffort = Literal["low", "medium", "high", "xhigh", "max"]
ProgressCallback = Callable[[str, str], Awaitable[None]]


@dataclass
class AgentRunResult:
    answer: str
    agent_session_id: str | None = None
    plan: AgentRunPlan = field(default_factory=lambda: new_agent_run_plan(""))


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
    tool_profile: AgentToolProfile = "research",
    _retrying_pseudo_tools: bool = False,
) -> AgentRunResult:
    settings = get_settings()
    allowed_tools = allowed_tools_for_profile(tool_profile)
    plan = new_agent_run_plan(prompt, resume=resume, allowed_tools=allowed_tools)
    plan.start()
    token = settings.get_agent_auth_token()
    if not token:
        plan.fail("Claude Agent SDK runtime is not configured")
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
        tools=[tool for tool in BUILTIN_AGENT_TOOLS if tool in allowed_tools],
        allowed_tools=allowed_tools,
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
        # Claude Code bare mode omits built-in WebSearch/WebFetch from the tool context.
        extra_args={},
        system_prompt=agent_system_prompt(allowed_tools, tool_profile),
    )

    answer_parts: list[str] = []
    observation_parts: list[str] = []
    agent_session_id = resume
    result_answer: str | None = None
    result_error: str | None = None
    tool_use_count = 0
    logger.info(
        "Research agent run start: resume={} model={} max_turns={} profile={} tools={}",
        bool(resume),
        model,
        settings.agent_max_turns,
        tool_profile,
        allowed_tools,
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
                        input_data = block.input if isinstance(block.input, dict) else {}
                        plan.record_tool_use(block.name or "", input_data)
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
                        plan.record_observation(observation)
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
        plan.fail(str(exc))
        raise AgentConfigurationError(
            str(exc),
            "研究 Agent 未找到 Claude Code CLI。请先安装或修复 claude-agent-sdk 的 bundled CLI。",
        ) from exc
    except (CLIConnectionError, ProcessError, ClaudeSDKError) as exc:
        logger.exception("Claude Agent SDK execution failed")
        plan.fail(str(exc))
        raise AgentRuntimeError(str(exc), _user_message_for_exception(exc)) from exc
    except Exception as exc:
        if _is_max_turns_error(str(exc)):
            return _max_turns_result(
                answer_parts,
                observation_parts,
                tool_use_count,
                agent_session_id,
                plan,
            )
        logger.exception("Claude Agent SDK execution failed unexpectedly")
        plan.fail(str(exc))
        raise AgentRuntimeError(str(exc), _user_message_for_exception(exc)) from exc

    if result_error:
        if _is_max_turns_error(result_error):
            return _max_turns_result(
                answer_parts,
                observation_parts,
                tool_use_count,
                agent_session_id,
                plan,
            )
        logger.error("Research agent result error: {}", result_error)
        plan.fail(result_error)
        raise AgentRuntimeError(result_error, _user_message_for_text(result_error))

    answer = result_answer or "\n".join(answer_parts).strip()
    if not answer:
        plan.fail("Agent SDK returned no answer.")
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
                tool_profile=tool_profile,
                _retrying_pseudo_tools=True,
            )
        plan.fail("Agent returned pseudo tool-call JSON instead of executing tools.")
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
    return AgentRunResult(
        answer=answer,
        agent_session_id=agent_session_id,
        plan=plan.complete(answer),
    )


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
    plan: AgentRunPlan | None = None,
) -> AgentRunResult:
    partial_answer = _partial_answer_from_max_turns(answer_parts, observation_parts, tool_use_count)
    logger.warning(
        "Research agent max turns reached; returning partial answer: "
        "session_id={} answer_chars={} tool_uses={}",
        agent_session_id or "-",
        len(partial_answer),
        tool_use_count,
    )
    if plan is None:
        plan = new_agent_run_plan("")
    return AgentRunResult(
        answer=partial_answer,
        agent_session_id=agent_session_id,
        plan=plan.partial(partial_answer),
    )


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
    title, action = _classify_agent_error(text)
    reason = _compact_error_reason(text)
    if reason:
        return f"{title}。建议: {action}。原因: {reason}"
    return f"{title}。建议: {action}。"


def _classify_agent_error(text: str) -> tuple[str, str]:
    normalized = text.lower()
    if "authentication" in normalized or "401" in normalized or "auth" in normalized:
        return "研究 Agent 认证失败", "检查 ANTHROPIC_AUTH_TOKEN 或 DEEPSEEK_API_KEY"
    if "rate limit" in normalized or "429" in normalized:
        return "研究 Agent 触发限流", "稍后重试，或降低并发和请求频率"
    if "billing" in normalized or "payment" in normalized or "402" in normalized:
        return "研究 Agent 计费状态异常", "检查模型账户余额、账单或额度"
    if "resume" in normalized or "session" in normalized:
        return "研究 Agent 会话恢复失败", "在本话题重新发送问题，系统会重建上下文"
    if "not found" in normalized or "claude code cli" in normalized:
        return "研究 Agent 运行环境不可用", "检查 Docker 镜像是否包含 Claude Code runtime"
    if "tool" in normalized or "mcp" in normalized:
        return "研究 Agent 工具调用失败", "重试或把问题拆成更具体的操作"
    if "timeout" in normalized or "timed out" in normalized:
        return "研究 Agent 上游请求超时", "稍后重试，必要时缩小问题范围"
    if "connection" in normalized or "network" in normalized or "dns" in normalized:
        return "研究 Agent 网络连接失败", "检查服务器网络和上游 API 可用性"
    return "研究 Agent 执行失败", "稍后重试；如果持续失败，请查看服务日志"


def _compact_error_reason(text: str, limit: int = 220) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


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
