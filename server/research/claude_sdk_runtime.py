"""Claude Agent SDK runtime configured for DeepSeek's Anthropic-compatible API."""

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
    SystemMessage,
    ToolUseBlock,
    query,
)
from claude_agent_sdk.types import McpServerConfig, McpStdioServerConfig

from config.settings import get_settings

AGENT_TOOLS = ["WebSearch", "WebFetch", "mcp__reveal"]
DISALLOWED_LOCAL_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
MCP_SERVERS: dict[str, McpServerConfig] = {
    "reveal": McpStdioServerConfig(
        command="uv",
        args=["run", "python", "-m", "server.mcp"],
    ),
}
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
) -> AgentRunResult:
    settings = get_settings()
    token = settings.get_agent_auth_token()
    if not token:
        raise AgentConfigurationError(
            "Claude Agent SDK runtime requires ANTHROPIC_AUTH_TOKEN, or OPENAI_API_KEY.",
            "研究 Agent 未配置 DeepSeek API Key。请设置 ANTHROPIC_AUTH_TOKEN 或 OPENAI_API_KEY。",
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
        tools=AGENT_TOOLS,
        allowed_tools=AGENT_TOOLS,
        disallowed_tools=DISALLOWED_LOCAL_TOOLS,
        strict_mcp_config=True,
        mcp_servers=MCP_SERVERS,
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
            "你有以下工具可用:\n"
            "- stock_quote: 查实时股价和涨跌幅\n"
            "- technical_analysis: 查技术指标 (RSI, SMA, 量比, PE, PEG)\n"
            "- stock_news: 查最近新闻\n"
            "- portfolio: 查用户当前持仓和浮盈\n"
            "- research_history: 查过去的研究结论\n"
            "- stock_score: 多因子评分\n"
            "- WebSearch: 搜索互联网\n"
            "- WebFetch: 抓取网页内容\n\n"
            "工作原则:\n"
            "1. 先用内部工具 (stock_quote, technical_analysis 等) 获取精确数据\n"
            "2. 再用 WebSearch/WebFetch 补充最新信息和外部观点\n"
            "3. 结合用户持仓 (portfolio) 给出个性化建议\n"
            "4. 区分事实、推断和不确定性\n"
            "5. 输出中文，末尾列出来源 URL\n"
            "6. 不要读取本地文件、运行命令或修改文件"
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
                    if isinstance(block, ToolUseBlock) and on_progress:
                        detail = _format_tool_progress(block)
                        await on_progress("tool_use", detail)
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
