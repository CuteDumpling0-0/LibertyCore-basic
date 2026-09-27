"""
app/schemas.py — Pydantic v2 schemas for all API request/response bodies,
task contracts, success criteria, reviewer output, and event records.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ─── Enumerations ──────────────────────────────────────────────────────────────

class TaskStatus(str, enum.Enum):
    CREATED = "created"
    CONTRACT_REVIEW = "contract_review"
    CONTEXT_GATHERING = "context_gathering"
    PLANNING = "planning"
    EXECUTING = "executing"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    SUCCESS = "success"
    NEEDS_APPROVAL = "needs_approval"
    BLOCKED = "blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class CriterionType(str, enum.Enum):
    COMMAND = "command"
    TEST = "test"
    FILE = "file"
    API = "api"
    SECURITY = "security"
    REVIEW = "review"
    ARTIFACT = "artifact"


class CriterionState(str, enum.Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


class EventKind(str, enum.Enum):
    STATE_TRANSITION = "state_transition"
    TOOL_CALL = "tool_call"
    TOOL_OUTPUT = "tool_output"
    MODEL_CALL = "model_call"
    MODEL_RESPONSE = "model_response"
    PLAN_CREATED = "plan_created"
    CRITERION_UPDATED = "criterion_updated"
    REVIEWER_OUTPUT = "reviewer_output"
    BUDGET_WARNING = "budget_warning"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    ERROR = "error"
    CHECKPOINT = "checkpoint"


class FailureClass(str, enum.Enum):
    TRANSIENT_PROVIDER = "transient_provider"
    TOOL_FAILURE = "tool_failure"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    FLAWED_PLAN = "flawed_plan"
    BLOCKED_PERMISSION = "blocked_permission"
    IMPOSSIBLE_GOAL = "impossible_goal"
    AMBIGUOUS_GOAL = "ambiguous_goal"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNKNOWN = "unknown"


# ─── Success Criterion ─────────────────────────────────────────────────────────

class SuccessCriterion(BaseModel):
    id: str
    type: CriterionType
    description: str = ""
    # For command/test type
    command: str | None = None
    expect: str | None = None
    # Runtime state
    state: CriterionState = CriterionState.PENDING
    evidence: str | None = None
    mandatory: bool = True


# ─── Budget ────────────────────────────────────────────────────────────────────

class Budget(BaseModel):
    max_iterations: int = Field(40, ge=1, le=500)
    max_minutes: int = Field(120, ge=1, le=1440)
    max_cost_usd: float = Field(25.0, ge=0.0)


# ─── Task Contract ─────────────────────────────────────────────────────────────

class TaskContract(BaseModel):
    goal_id: str
    objective: str
    deliverables: list[str] = Field(default_factory=list)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    authorized_actions: list[str] = Field(default_factory=list)
    approval_required_actions: list[str] = Field(default_factory=list)
    budgets: Budget = Field(default_factory=Budget)
    status: TaskStatus = TaskStatus.CREATED


# ─── API Request/Response ──────────────────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    goal: str = Field(..., min_length=10, max_length=4000)
    workspace: str = Field(..., description="Absolute path to the target repository/workspace")
    constraints: list[str] = Field(default_factory=list)
    budget: Budget | None = None
    model_preset: str = "auto"

    @field_validator("goal")
    @classmethod
    def strip_goal(cls, v: str) -> str:
        return v.strip()


class TaskSummary(BaseModel):
    id: str
    status: TaskStatus
    objective: str
    workspace: str
    created_at: datetime
    updated_at: datetime
    iteration: int
    total_cost_usd: float
    criteria_status: list[dict[str, Any]] = Field(default_factory=list)


class TaskDetail(TaskSummary):
    contract: TaskContract | None = None
    current_plan: list[str] = Field(default_factory=list)
    budget: Budget
    elapsed_minutes: float
    failure_class: FailureClass | None = None
    blocked_reason: str | None = None
    checkpoint_id: str | None = None


class EventRecord(BaseModel):
    id: str
    task_id: str
    kind: EventKind
    iteration: int
    payload: dict[str, Any]
    created_at: datetime


class ArtifactInfo(BaseModel):
    id: str
    task_id: str
    name: str
    kind: str
    size_bytes: int
    created_at: datetime


class ApproveActionRequest(BaseModel):
    action_id: str
    approved: bool
    reason: str = ""


# ─── Reviewer Output (enforced JSON schema) ────────────────────────────────────

class CriterionReviewStatus(BaseModel):
    criterion_id: str
    state: CriterionState
    evidence: str


class ReviewerOutput(BaseModel):
    goal_achieved: bool
    criteria_status: list[CriterionReviewStatus]
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_best_action: str
    strategy_change_required: bool = False


# ─── Planner Output ────────────────────────────────────────────────────────────

class PlanStep(BaseModel):
    id: str
    description: str
    tool: str
    tool_input: dict[str, Any]
    expected_evidence: str
    depends_on: list[str] = Field(default_factory=list)


class PlannerOutput(BaseModel):
    plan_id: str
    rationale: str
    steps: list[PlanStep]
    estimated_iterations: int = 1


# ─── Tool execution ────────────────────────────────────────────────────────────

class ToolResult(BaseModel):
    tool: str
    success: bool
    output: str
    exit_code: int | None = None
    duration_ms: int = 0
    truncated: bool = False


# ─── Blocked / Budget-exhausted report ────────────────────────────────────────

class BlockedReport(BaseModel):
    task_id: str
    reason: str
    failure_class: FailureClass
    what_was_tried: list[str]
    evidence: list[str]
    remaining_gaps: list[str]
    recommended_action: str
    checkpoint_id: str | None = None
