"""Claude Agent SDK runtime configured for DeepSeek's Anthropic-compatible API."""

from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    query,
)

from config.settings import get_settings


@dataclass
class AgentRunResult:
    answer: str
    agent_session_id: str | None = None


async def run_agent(prompt: str, resume: str | None = None) -> AgentRunResult:
    settings = get_settings()
    token = settings.claude_agent_auth_token or settings.openai_api_key
    if not token:
        raise RuntimeError(
            "Claude Agent SDK runtime requires OPENAI_API_KEY or CLAUDE_AGENT_AUTH_TOKEN."
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
        allowed_tools=["WebSearch", "WebFetch"],
        disallowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
        permission_mode="dontAsk",
        model=settings.claude_agent_model,
        max_turns=settings.claude_agent_max_turns,
        cwd=Path.cwd(),
        env=env,
        resume=resume,
        setting_sources=[],
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

    if result_error:
        raise RuntimeError(result_error)

    answer = result_answer or "\n".join(answer_parts).strip()
    if not answer:
        raise RuntimeError("Agent SDK returned no answer.")
    return AgentRunResult(answer=answer, agent_session_id=agent_session_id)
