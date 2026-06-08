"""Research card builders."""

from __future__ import annotations

from server.bot.cards._base import card_shell, markdown_elements, note


def research_status_card(text: str, *, state: str = "running") -> dict:
    title, template = _status_title_template(state)
    return card_shell(
        title,
        markdown_elements(text),
        template=template,
    )


def research_result_card(
    result_text: str,
    *,
    step_count: int | None = None,
    elapsed_seconds: float | None = None,
    thread_hint: str = "继续回复本话题即可追问",
) -> dict:
    metadata = _result_metadata(step_count, elapsed_seconds, thread_hint)
    elements: list[dict] = [note(metadata), {"tag": "hr"}]
    body = markdown_elements(result_text)
    for index, element in enumerate(body):
        if index:
            elements.append({"tag": "hr"})
        elements.append(element)
    return card_shell("Reveal · 研究结果", elements, template="green")


def _status_title_template(state: str) -> tuple[str, str]:
    normalized = state.strip().lower()
    if normalized in {"completed", "done", "success"}:
        return "Reveal · 研究完成", "green"
    if normalized in {"failed", "error"}:
        return "Reveal · 处理失败", "red"
    return "Reveal · 研究中", "blue"


def _result_metadata(
    step_count: int | None,
    elapsed_seconds: float | None,
    thread_hint: str,
) -> str:
    parts: list[str] = []
    if step_count is not None:
        parts.append(f"{step_count} 个工具步骤")
    if elapsed_seconds is not None:
        parts.append(f"{elapsed_seconds:.1f}s")
    parts.append(thread_hint)
    return " · ".join(parts)
