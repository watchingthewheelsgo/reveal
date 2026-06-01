"""Claude Agent SDK runtime configured for DeepSeek's Anthropic-compatible API."""

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
    SystemMessage,
    query,
)

from config.settings import get_settings

AGENT_TOOLS = ["WebSearch", "WebFetch"]
DISALLOWED_LOCAL_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
AgentEffort = Literal["low", "medium", "high", "xhigh", "max"]


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


async def run_agent(prompt: str, resume: str | None = None) -> AgentRunResult:
    settings = get_settings()
    token = settings.claude_agent_auth_token or settings.openai_api_key
    if not token:
        raise AgentConfigurationError(
            "Claude Agent SDK runtime requires OPENAI_API_KEY or CLAUDE_AGENT_AUTH_TOKEN.",
            "研究 Agent 未配置 DeepSeek API Key。"
            "请设置 OPENAI_API_KEY 或 CLAUDE_AGENT_AUTH_TOKEN。",
        )

    env = {
        "ANTHROPIC_BASE_URL": settings.claude_agent_base_url,
        "ANTHROPIC_AUTH_TOKEN": token,
        "ANTHROPIC_API_KEY": token,
        "ANTHROPIC_MODEL": settings.claude_agent_model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": settings.claude_agent_model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": settings.claude_agent_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": settings.claude_agent_small_model,
        "CLAUDE_CODE_SUBAGENT_MODEL": settings.claude_agent_small_model,
        "CLAUDE_CODE_EFFORT_LEVEL": settings.claude_agent_effort,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }

    options = ClaudeAgentOptions(
        tools=AGENT_TOOLS,
        allowed_tools=AGENT_TOOLS,
        disallowed_tools=DISALLOWED_LOCAL_TOOLS,
        strict_mcp_config=True,
        mcp_servers={},
        permission_mode="dontAsk",
        model=settings.claude_agent_model,
        max_turns=settings.claude_agent_max_turns,
        cwd=Path.cwd(),
        env=env,
        effort=cast(AgentEffort, settings.claude_agent_effort),
        resume=resume,
        setting_sources=[],
        extra_args={"bare": None},
        system_prompt=(
            "你是 Reveal 的深度研究代理。围绕用户给定的 Twitter/X 更新做研究。"
            "优先使用 WebSearch 和 WebFetch 获取外部证据。不要读取本地文件、运行命令或修改文件。"
            "回答必须使用中文，区分事实、推断和不确定性，并在末尾列出来源 URL。"
        ),
    )

    answer_parts: list[str] = []
    agent_session_id = resume
    result_answer: str | None = None
    result_error: str | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                session_id = message.data.get("session_id")
                if session_id:
                    agent_session_id = str(session_id)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    text = getattr(block, "text", None)
                    if text:
                        answer_parts.append(str(text))
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
        raise AgentConfigurationError(
            str(exc),
            "研究 Agent 未找到 Claude Code CLI。请先安装或修复 claude-agent-sdk 的 bundled CLI。",
        ) from exc
    except (CLIConnectionError, ProcessError, ClaudeSDKError) as exc:
        raise AgentRuntimeError(str(exc), _user_message_for_exception(exc)) from exc

    if result_error:
        raise AgentRuntimeError(result_error, _user_message_for_text(result_error))

    answer = result_answer or "\n".join(answer_parts).strip()
    if not answer:
        raise AgentRuntimeError(
            "Agent SDK returned no answer.",
            "研究 Agent 没有返回内容，请稍后重试。",
        )
    return AgentRunResult(answer=answer, agent_session_id=agent_session_id)


def _user_message_for_exception(exc: BaseException) -> str:
    return _user_message_for_text(str(exc))


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
