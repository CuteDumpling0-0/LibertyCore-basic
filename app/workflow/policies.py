"""
app/workflow/policies.py — Policies for budgets, stall detection, failure classification,
and goal completion checks.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.schemas import CriterionState, FailureClass, TaskStatus
from app.workflow.state import WorkflowState


def check_budgets(
    state: WorkflowState, *, check_iteration: bool = True
) -> tuple[bool, str]:
    """
    Checks iteration, elapsed time, and cost budgets.
    Returns (budget_exceeded: bool, reason_description: str).
    """
    if check_iteration and state.iteration >= state.budget.max_iterations:
        return True, f"Max iteration limit ({state.budget.max_iterations}) reached."

    if state.elapsed_minutes() >= state.budget.max_minutes:
        return True, f"Time limit ({state.budget.max_minutes} minutes) exceeded ({state.elapsed_minutes():.1f} mins elapsed)."

    if state.budget.max_cost_usd > 0:
        if state.total_cost_usd >= state.budget.max_cost_usd:
            return True, f"Cost budget limit (${state.budget.max_cost_usd:.2f}) reached (spent ${state.total_cost_usd:.2f})."
    elif state.budget.max_cost_usd == 0:
        if state.total_cost_usd > 0.0:
            return True, f"Strict free mode ($0.00 budget): paid model call incurred ${state.total_cost_usd:.4f}."

    return False, ""


def check_stall(state: WorkflowState) -> tuple[bool, str]:
    """
    Detects infinite loops and stalling:
    1. Unchanged diff for 4 consecutive iterations when writing tools were called.
    2. Identical gaps reported 3 times in a row without progress.
    3. Repeated tool failure pattern.
    """
    if len(state.gap_history) >= 3:
        last_three = state.gap_history[-3:]
        # If last three gap sets are identical and non-empty
        if last_three[0] and last_three[0] == last_three[1] == last_three[2]:
            return True, f"Stall detected: identical gaps persisted for 3 iterations ({last_three[0]})."

    if len(state.diff_fingerprints) >= 4:
        recent_diffs = state.diff_fingerprints[-4:]
        if len(set(recent_diffs)) == 1 and recent_diffs[0] != "":
            return True, "Stall detected: git diff unchanged across 4 consecutive iterations."

    return False, ""


def compute_fingerprint(text: str) -> str:
    """Helper to hash strings for stall tracking."""
    return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()


def evaluate_completion(state: WorkflowState) -> tuple[bool, str]:
    """
    Non-negotiable rule 4 & Section 6:
    SUCCESS only if every mandatory deterministic criterion passes,
    and every mandatory review criterion passes,
    and no unresolved high-severity reviewer gap remains.
    A model claiming 'done' is only an observation, not verification.
    """
    mandatory_criteria = [c for c in state.criteria if c.mandatory]
    if not mandatory_criteria:
        return False, "No mandatory criteria defined."

    failed_or_pending = [c for c in mandatory_criteria if c.state != CriterionState.PASSED]
    if failed_or_pending:
        reasons = [f"Criterion '{c.id}' is {c.state.value} ({c.description})" for c in failed_or_pending]
        return False, "; ".join(reasons)

    # Check reviewer gaps if review exists
    if state.last_review:
        if state.last_review.gaps:
            return False, f"Reviewer identified unresolved gaps: {', '.join(state.last_review.gaps)}"
        if not state.last_review.goal_achieved:
            return False, "Reviewer indicated overall goal not yet fully achieved."

    return True, "All mandatory deterministic and review criteria passed with verified evidence."
