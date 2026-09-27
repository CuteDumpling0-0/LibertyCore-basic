"""
app/workflow/recovery.py — Checkpoint serialization, resume logic, and failure diagnosis.
Enables tasks to survive process restarts and generates structured blocked reports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from app.persistence.models import TaskModel
from app.persistence.repositories import TaskRepository
from app.schemas import (
    BlockedReport,
    Budget,
    FailureClass,
    PlanStep,
    ReviewerOutput,
    SuccessCriterion,
    TaskContract,
    TaskStatus,
    ToolResult,
)
from app.workflow.state import WorkflowState

log = structlog.get_logger(__name__)


def create_blocked_report(state: WorkflowState) -> BlockedReport:
    """
    Builds the structured blocked report containing what was tried, evidence,
    remaining gaps, recommended human action, and resumable checkpoint ID.
    """
    evidence_items = [
        f"Diff length: {len(state.recent_diff)} chars",
        f"Last test output snippet: {state.recent_test_output[:250]}",
    ]
    if state.last_review:
        evidence_items.append(f"Reviewer evaluation: {state.last_review.model_dump_json()[:300]}")

    return BlockedReport(
        task_id=state.task_id,
        reason=state.blocked_reason or "Unknown block",
        failure_class=state.failure_class or FailureClass.UNKNOWN,
        what_was_tried=[f"Step: {s.description}" for s in state.current_plan_steps],
        evidence=evidence_items,
        remaining_gaps=state.unresolved_gaps or ["Criteria not satisfied"],
        recommended_action=(
            state.last_review.next_best_action
            if state.last_review and state.last_review.next_best_action
            else "Inspect repository state and review task criteria."
        ),
        checkpoint_id=state.checkpoint_id,
    )


async def restore_workflow_state(task_model: TaskModel) -> WorkflowState:
    """Restores an in-memory WorkflowState from durable database TaskModel."""
    contract_data = task_model.get_contract()
    contract = TaskContract.model_validate(contract_data) if contract_data else None

    criteria = contract.success_criteria if contract else []
    budget = contract.budgets if contract else Budget(
        max_iterations=task_model.max_iterations,
        max_minutes=task_model.max_minutes,
        max_cost_usd=task_model.max_cost_usd,
    )

    state = WorkflowState(
        task_id=task_model.id,
        workspace=task_model.workspace,
        status=TaskStatus(task_model.status),
        contract=contract,
        criteria=criteria,
        iteration=task_model.iteration,
        budget=budget,
        total_cost_usd=task_model.total_cost_usd,
        total_tokens=task_model.total_tokens,
        failure_class=FailureClass(task_model.failure_class) if task_model.failure_class else None,
        blocked_reason=task_model.blocked_reason,
        checkpoint_id=task_model.checkpoint_id,
    )
    snapshot = task_model.get_runtime_state() or {}
    state.model_preset = snapshot.get("model_preset", "auto")
    state.initial_constraints = snapshot.get("initial_constraints", contract.constraints if contract else [])
    if snapshot.get("start_time"):
        state.start_time = datetime.fromisoformat(snapshot["start_time"])
    state.current_plan_steps = [PlanStep.model_validate(step) for step in snapshot.get("current_plan_steps", [])]
    state.recent_observations = snapshot.get("recent_observations", [])
    state.recent_tool_results = [ToolResult.model_validate(item) for item in snapshot.get("recent_tool_results", [])]
    state.recent_diff = snapshot.get("recent_diff", "")
    state.recent_test_output = snapshot.get("recent_test_output", "")
    state.active_files = snapshot.get("active_files", [])
    state.recent_errors = snapshot.get("recent_errors", [])
    state.unresolved_gaps = snapshot.get("unresolved_gaps", [])
    state.gap_history = snapshot.get("gap_history", [])
    state.action_fingerprints = snapshot.get("action_fingerprints", [])
    state.diff_fingerprints = snapshot.get("diff_fingerprints", [])
    state.pending_approval_id = snapshot.get("pending_approval_id")
    state.pending_approval_stage = snapshot.get("pending_approval_stage")
    if snapshot.get("last_review"):
        state.last_review = ReviewerOutput.model_validate(snapshot["last_review"])
    return state
