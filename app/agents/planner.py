"""
app/agents/planner.py — Planning agent.
Produces a bounded, testable action plan given current task state.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.models.gateway import get_gateway
from app.models.prompts import PLANNER_SYSTEM, PLANNER_USER
from app.models.routing import ModelRouter
from app.schemas import PlannerOutput, SuccessCriterion

log = structlog.get_logger(__name__)


async def create_plan(
    contract: dict[str, Any],
    iteration: int,
    unresolved_criteria: list[SuccessCriterion],
    observations: list[str],
    gaps: list[str],
    failures: list[str],
    router: ModelRouter,
    max_cost_usd: float | None = None,
) -> tuple[PlannerOutput, float]:
    """
    Call the planner model and return a PlannerOutput with 1-3 steps.
    Returns (plan, cost_usd).
    """
    gateway = get_gateway()
    model, fallbacks = router.select("planner")

    prompt = PLANNER_USER.format(
        contract=_format_contract(contract),
        iteration=iteration,
        unresolved_criteria=_format_criteria(unresolved_criteria),
        observations="\n".join(observations[-10:]) if observations else "None yet.",
        gaps="\n".join(gaps) if gaps else "None.",
        failures="\n".join(failures[-5:]) if failures else "None.",
    )

    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    plan, cost = await gateway.call_structured(
        model,
        messages,
        PlannerOutput,
        fallbacks=fallbacks,
        role_label="planner",
        free_only=router.free_only,
        max_cost_usd=max_cost_usd,
    )

    # Enforce 1-3 steps max per batch
    if len(plan.steps) > 3:
        log.warning("planner_too_many_steps", count=len(plan.steps), trimmed=3)
        plan.steps = plan.steps[:3]

    log.info("plan_created", plan_id=plan.plan_id, steps=len(plan.steps))
    return plan, cost


def _format_contract(contract: dict[str, Any]) -> str:
    import json
    return json.dumps(contract, indent=2)[:3000]


def _format_criteria(criteria: list[SuccessCriterion]) -> str:
    if not criteria:
        return "All criteria resolved."
    lines = []
    for c in criteria:
        lines.append(f"- [{c.id}] ({c.type.value}) {c.description} — state: {c.state.value}")
    return "\n".join(lines)
