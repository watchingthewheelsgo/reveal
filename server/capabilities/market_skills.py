"""Capability payloads for MarketSkill registry."""

from typing import Any

from server.research.market_skills import list_market_skills


def get_market_skill_catalog_payload() -> dict[str, Any]:
    skills = []
    for skill in list_market_skills():
        skills.append(
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "bias": skill.bias,
                "required_sources": list(skill.required_sources),
                "prompt_guidance": skill.prompt_guidance,
                "evidence_policy": skill.evidence_policy,
                "risk_level": skill.risk_level,
            }
        )
    return {"count": len(skills), "skills": skills}
