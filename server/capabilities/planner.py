"""Natural-language planning primitives for Reveal capabilities.

The planner is intentionally thin: deterministic routes and LLM routes should
both compile into the same action shape before execution. That keeps command,
MCP, and Agent entrypoints aligned with the capability catalog.
"""

from dataclasses import dataclass, field

from server.capabilities.registry import CapabilitySpec, list_capabilities


@dataclass(frozen=True)
class PlannedAction:
    capability_id: str | None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    confidence: float = 1.0
    reason: str = ""
    confirmation_prompt: str | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self.confirmation_prompt is not None


def capability_for_command(command: str) -> CapabilitySpec | None:
    normalized = command.strip().lstrip("/").lower()
    for cap in list_capabilities():
        if normalized in cap.slash_commands:
            return cap
    return None


def plan_from_command_route(route: dict, user_text: str) -> PlannedAction:
    command = str(route["command"]).strip().lstrip("/").lower()
    args = [str(arg) for arg in route.get("args", [])]
    cap = capability_for_command(command)
    return PlannedAction(
        capability_id=cap.id if cap else None,
        command=command,
        args=args,
        confidence=float(route.get("confidence", 0.95)),
        reason=str(route.get("reason") or _default_reason(command, user_text)),
    )


def confirmation_plan(prompt: str, user_text: str) -> PlannedAction:
    return PlannedAction(
        capability_id=None,
        command=None,
        confidence=0.35,
        reason=f"Need clarification before executing a capability for: {user_text[:80]}",
        confirmation_prompt=prompt,
    )


def _default_reason(command: str, user_text: str) -> str:
    cap = capability_for_command(command)
    if cap is None:
        return f"Matched natural language to /{command}."
    return f"Matched natural language to {cap.id} via /{command}: {user_text[:80]}"
