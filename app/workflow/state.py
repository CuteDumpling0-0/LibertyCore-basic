"""
app/workflow/state.py — In-memory state container for the active workflow run.
Mirrors the durable state persisted in the database after every step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.schemas import (
    Budget,
    FailureClass,
    PlanStep,
    ReviewerOutput,
    SuccessCriterion,
    TaskContract,
    TaskStatus,
    ToolResult,
)


@dataclass
class WorkflowState:
    task_id: str
    workspace: str
    status: TaskStatus = TaskStatus.CREATED
    contract: TaskContract | None = None
    criteria: list[SuccessCriterion] = field(default_factory=list)
    model_preset: str = "auto"
    initial_constraints: list[str] = field(default_factory=list)

    # Iteration & Budget tracking
    iteration: int = 0
    budget: Budget = field(default_factory=Budget)
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Active execution state
    current_plan_steps: list[PlanStep] = field(default_factory=list)
    recent_observations: list[str] = field(default_factory=list)
    recent_tool_results: list[ToolResult] = field(default_factory=list)
    recent_diff: str = ""
    recent_test_output: str = ""
    active_files: list[str] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)

    # Reviewer tracking
    last_review: ReviewerOutput | None = None
    unresolved_gaps: list[str] = field(default_factory=list)
    gap_history: list[list[str]] = field(default_factory=list)
    failed_review_count: int = 0

    # Stall & Loop detection fingerprints
    action_fingerprints: list[str] = field(default_factory=list)
    diff_fingerprints: list[str] = field(default_factory=list)

    # Outcome & Stop flags
    failure_class: FailureClass | None = None
    blocked_reason: str | None = None
    checkpoint_id: str | None = None
    pending_approval_id: str | None = None
    pending_approval_stage: str | None = None
    is_paused: bool = False
    is_cancelled: bool = False
    resume_pending_approval: bool = False

    def elapsed_minutes(self) -> float:
        now = datetime.now(timezone.utc)
        return (now - self.start_time).total_seconds() / 60.0

    def is_active(self) -> bool:
        return self.status in {
            TaskStatus.CREATED,
            TaskStatus.CONTRACT_REVIEW,
            TaskStatus.CONTEXT_GATHERING,
            TaskStatus.PLANNING,
            TaskStatus.EXECUTING,
            TaskStatus.OBSERVING,
            TaskStatus.VERIFYING,
            TaskStatus.REVIEWING,
        } and not self.is_paused and not self.is_cancelled

    def snapshot(self) -> dict[str, Any]:
        """Return the complete controller state needed to resume after restart."""
        return {
            "model_preset": self.model_preset,
            "initial_constraints": self.initial_constraints,
            "start_time": self.start_time.isoformat(),
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
            "current_plan_steps": [step.model_dump(mode="json") for step in self.current_plan_steps],
            "recent_observations": self.recent_observations[-50:],
            "recent_tool_results": [result.model_dump(mode="json") for result in self.recent_tool_results[-20:]],
            "recent_diff": self.recent_diff,
            "recent_test_output": self.recent_test_output,
            "active_files": self.active_files[-50:],
            "recent_errors": self.recent_errors[-20:],
            "last_review": self.last_review.model_dump(mode="json") if self.last_review else None,
            "unresolved_gaps": self.unresolved_gaps,
            "gap_history": self.gap_history[-10:],
            "action_fingerprints": self.action_fingerprints[-10:],
            "diff_fingerprints": self.diff_fingerprints[-10:],
            "pending_approval_id": self.pending_approval_id,
            "pending_approval_stage": self.pending_approval_stage,
        }
