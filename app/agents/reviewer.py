"""
app/agents/reviewer.py — Independent reviewer agent.
Evaluates evidence against the task contract and returns structured gaps/risks.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.models.gateway import get_gateway
from app.models.prompts import REVIEWER_SYSTEM, REVIEWER_USER
from app.models.routing import ModelRouter
from app.schemas import ReviewerOutput, SuccessCriterion

log = structlog.get_logger(__name__)


async def review_progress(
    contract: dict[str, Any],
    current_plan: list[str],
    evidence: list[str],
    diffs: str,
    test_output: str,
    unresolved_criteria: list[SuccessCriterion],
    router: ModelRouter,
    max_cost_usd: float | None = None,
) -> tuple[ReviewerOutput, float]:
    """
    Call the reviewer model and return structured ReviewerOutput.
    The reviewer must cite evidence — claims without evidence are flagged as gaps.
    Returns (review, cost_usd).
    """
    gateway = get_gateway()
    model, fallbacks = router.select("reviewer")

    prompt = REVIEWER_USER.format(
        contract=_fmt(contract),
        plan="\n".join(current_plan) if current_plan else "No plan yet.",
        evidence="\n".join(evidence[-15:]) if evidence else "No evidence collected.",
        diffs=diffs[:4000] if diffs else "(no diff)",
        test_output=test_output[:3000] if test_output else "(no test output)",
        unresolved_criteria=_fmt_criteria(unresolved_criteria),
    )

    messages = [
        {"role": "system", "content": REVIEWER_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    review, cost = await gateway.call_structured(
        model,
        messages,
        ReviewerOutput,
        fallbacks=fallbacks,
        role_label="reviewer",
        temperature=0.05,  # very low temp for consistent evaluation
        free_only=router.free_only,
        max_cost_usd=max_cost_usd,
    )

    log.info(
        "reviewer_output",
        goal_achieved=review.goal_achieved,
        gaps=len(review.gaps),
        strategy_change=review.strategy_change_required,
    )
    return review, cost


def _fmt(data: Any) -> str:
    import json
    return json.dumps(data, indent=2)[:3000] if isinstance(data, dict) else str(data)[:3000]


def _fmt_criteria(criteria: list[SuccessCriterion]) -> str:
    if not criteria:
        return "All criteria resolved."
    return "\n".join(
        f"- [{c.id}] ({c.type.value}) {c.description} — state: {c.state.value}"
        for c in criteria
    )
