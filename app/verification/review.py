"""
app/verification/review.py — Reviewer-based verifier (advisory, not sole success signal).
A model criterion is only marked passed if the deterministic verifiers also agree.
"""

from __future__ import annotations

from app.schemas import CriterionState, SuccessCriterion
from app.verification.registry import VerifierResult


async def verify_review(criterion: SuccessCriterion, workspace: str) -> VerifierResult:
    """
    Review criteria cannot be auto-verified without model output.
    This verifier checks if prior review evidence was recorded in criterion.evidence.
    The reviewer agent populates criterion.evidence; this verifier trusts that.

    IMPORTANT: A review criterion should NEVER be the sole success signal.
    It is always advisory unless paired with deterministic criteria.
    """
    if criterion.state == CriterionState.PASSED and criterion.evidence:
        return VerifierResult(True, f"Review passed (evidence: {criterion.evidence[:200]})")
    elif criterion.state == CriterionState.FAILED:
        return VerifierResult(False, f"Review failed: {criterion.evidence or 'no evidence'}")
    else:
        return VerifierResult(False, "Review criterion not yet evaluated by reviewer agent")
