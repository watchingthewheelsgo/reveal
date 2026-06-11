"""Agent run plan and execution trace primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

AgentRunStatus = Literal["planned", "running", "complete", "partial", "error"]


@dataclass
class AgentPlanStep:
    index: int
    action: str
    tool_name: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    observation: str | None = None
    status: Literal["planned", "running", "complete", "error"] = "planned"


@dataclass
class AgentRunPlan:
    """A structured, auditable trace for one Agent execution."""

    intent: str
    resume_session_id: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    steps: list[AgentPlanStep] = field(default_factory=list)
    status: AgentRunStatus = "planned"
    final_answer: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def start(self) -> None:
        self.status = "running"

    def record_tool_use(self, tool_name: str, input_data: dict[str, Any]) -> AgentPlanStep:
        step = AgentPlanStep(
            index=len(self.steps) + 1,
            action=f"Call {tool_name}",
            tool_name=tool_name,
            input=input_data,
            status="running",
        )
        self.steps.append(step)
        return step

    def record_observation(self, observation: str) -> None:
        if not observation:
            return
        for step in reversed(self.steps):
            if step.observation is None:
                step.observation = observation
                step.status = "complete"
                return
        self.steps.append(
            AgentPlanStep(
                index=len(self.steps) + 1,
                action="Observe tool result",
                observation=observation,
                status="complete",
            )
        )

    def complete(self, answer: str) -> AgentRunPlan:
        self.status = "complete"
        self.final_answer = answer
        self.completed_at = datetime.now(UTC)
        for step in self.steps:
            if step.status == "running":
                step.status = "complete"
        return self

    def partial(self, answer: str) -> AgentRunPlan:
        self.status = "partial"
        self.final_answer = answer
        self.completed_at = datetime.now(UTC)
        return self

    def fail(self, error: str) -> AgentRunPlan:
        self.status = "error"
        self.error = error
        self.completed_at = datetime.now(UTC)
        for step in self.steps:
            if step.status == "running":
                step.status = "error"
        return self


def new_agent_run_plan(
    prompt: str,
    *,
    resume: str | None = None,
    allowed_tools: list[str] | tuple[str, ...] = (),
) -> AgentRunPlan:
    return AgentRunPlan(
        intent=_compact_intent(prompt),
        resume_session_id=resume,
        allowed_tools=list(allowed_tools),
    )


def _compact_intent(prompt: str, limit: int = 240) -> str:
    clean = " ".join(prompt.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."
