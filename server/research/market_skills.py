"""Market skill registry for biased-but-explicit analysis perspectives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from server.events.types import Event

MarketSkillBias = Literal[
    "neutral",
    "bullish",
    "bearish",
    "macro",
    "geo_risk",
    "momentum",
    "contrarian",
    "regulatory",
]


@dataclass(frozen=True)
class MarketSkillSpec:
    """A reusable market analysis lens.

    The bias is explicit so the Agent can use it as an analysis perspective
    without contaminating the neutral source-event layer.
    """

    id: str
    name: str
    description: str
    bias: MarketSkillBias
    required_sources: tuple[str, ...] = ()
    prompt_guidance: str = ""
    evidence_policy: str = "Separate facts from interpretation and cite source links."
    risk_level: Literal["low", "medium", "high"] = "medium"


@dataclass(frozen=True)
class SkillRunRequest:
    skill_id: str
    event_id: str
    reason: str
    input_snapshot: str


@dataclass
class SkillRunResult:
    skill_id: str
    event_id: str
    observations: list[str] = field(default_factory=list)
    result: str = ""
    confidence: float | None = None


MARKET_SKILLS: tuple[MarketSkillSpec, ...] = (
    MarketSkillSpec(
        id="macro_policy",
        name="Macro / Policy Shock",
        description="Assess macro, fiscal, monetary, tariff, and election-policy impact.",
        bias="macro",
        prompt_guidance=(
            "从宏观/政策冲击角度分析：传导路径、受益/受损行业、时间窗口、是否已经被市场定价。"
        ),
    ),
    MarketSkillSpec(
        id="geo_risk",
        name="Geopolitical / Military Risk",
        description=(
            "Evaluate geopolitical escalation, military conflict, sanctions, and supply risk."
        ),
        bias="geo_risk",
        prompt_guidance=(
            "从地缘/军事风险角度分析：升级概率、资产影响、供应链影响、油价/国防/半导体等相关链条。"
        ),
        risk_level="high",
    ),
    MarketSkillSpec(
        id="momentum_trader",
        name="Momentum / Flow",
        description="Assess short-term price action, unusual volume, and catalyst-follow-through.",
        bias="momentum",
        prompt_guidance=(
            "从短线动量角度分析：触发因素、成交/价格确认、假突破风险、需要继续观察的价量信号。"
        ),
    ),
    MarketSkillSpec(
        id="regulatory_catalyst",
        name="Regulatory Catalyst",
        description="Assess SEC/FDA/regulatory events as potential catalysts or risks.",
        bias="regulatory",
        prompt_guidance=(
            "从监管催化角度分析：事件性质、法律/审批阶段、对收入/估值/风险披露的影响。"
        ),
    ),
    MarketSkillSpec(
        id="bear_case",
        name="Bear Case",
        description="Actively search for downside risks and disconfirming evidence.",
        bias="bearish",
        prompt_guidance=(
            "以看空/风险视角审视：反证、拥挤交易、估值压力、执行风险、哪些事实会推翻乐观叙事。"
        ),
    ),
    MarketSkillSpec(
        id="bull_case",
        name="Bull Case",
        description="Identify upside catalysts, positive revisions, and acceleration signals.",
        bias="bullish",
        prompt_guidance=(
            "以看多/催化视角审视：上行驱动、预期差、受益链条、哪些事实会强化多头叙事。"
        ),
    ),
)


def list_market_skills() -> list[MarketSkillSpec]:
    return list(MARKET_SKILLS)


def select_market_skills(event: Event, limit: int = 0) -> list[MarketSkillSpec]:
    """Return available skill perspectives for Agent planning.

    Selection is intentionally left to the Agent. This function only limits the
    catalog size for callers that cannot render every skill.
    """
    del event
    if limit <= 0:
        return list(MARKET_SKILLS)
    return list(MARKET_SKILLS[:limit])


def build_skill_run_requests(event: Event, limit: int = 0) -> list[SkillRunRequest]:
    event_snapshot = _event_snapshot(event)
    requests: list[SkillRunRequest] = []
    for skill in select_market_skills(event, limit=limit):
        requests.append(
            SkillRunRequest(
                skill_id=skill.id,
                event_id=event.id,
                reason=_selection_reason(skill, event),
                input_snapshot=event_snapshot,
            )
        )
    return requests


def market_skill_prompt_context(event: Event, limit: int = 0) -> str:
    """Render market skill options for a research prompt."""
    selected = select_market_skills(event, limit=limit)
    if not selected:
        return ""
    lines = [
        "Market skill catalog for Agent planning:",
        "这些 skill 是显式分析视角；由 Agent 在 plan 阶段按任务选择，"
        "事实层保持中立，bias 只用于解释和风险检查。",
    ]
    for skill in selected:
        lines.extend(
            [
                f"- {skill.id} ({skill.bias}): {skill.name}",
                f"  use_when: {skill.description}",
                f"  guidance: {skill.prompt_guidance}",
                f"  evidence_policy: {skill.evidence_policy}",
            ]
        )
    return "\n".join(lines)


def _event_snapshot(event: Event) -> str:
    topics = [str(topic) for topic in getattr(event, "topics", [])]
    return "\n".join(
        [
            f"id={event.id}",
            f"kind={event.kind}",
            f"source={event.source}",
            f"title={event.title}",
            f"tickers={','.join(event.tickers)}",
            f"topics={','.join(topics)}",
        ]
    )


def _selection_reason(skill: MarketSkillSpec, event: Event) -> str:
    del event
    return f"{skill.id} is available for Agent planning"
