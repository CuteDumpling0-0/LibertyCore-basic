"""
app/persistence/models.py — SQLAlchemy ORM models for Tasks, Events, Iterations,
ToolOutputs, and PendingApprovals.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.persistence.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    workspace: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Full contract stored as JSON
    contract_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Complete resumable controller snapshot. Kept separate from the human-readable plan.
    runtime_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Budget tracking
    max_iterations: Mapped[int] = mapped_column(Integer, default=40)
    max_minutes: Mapped[int] = mapped_column(Integer, default=120)
    max_cost_usd: Mapped[float] = mapped_column(Float, default=25.0)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Failure / blocking state
    failure_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[EventModel]] = relationship(
        "EventModel", back_populates="task", cascade="all, delete-orphan"
    )
    approvals: Mapped[list[ApprovalModel]] = relationship(
        "ApprovalModel", back_populates="task", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[ArtifactModel]] = relationship(
        "ArtifactModel", back_populates="task", cascade="all, delete-orphan"
    )

    def get_contract(self) -> dict[str, Any] | None:
        if self.contract_json:
            return json.loads(self.contract_json)
        return None

    def set_contract(self, contract: dict[str, Any]) -> None:
        self.contract_json = json.dumps(contract)

    def get_plan(self) -> list[str]:
        if self.current_plan_json:
            return json.loads(self.current_plan_json)
        return []

    def set_plan(self, plan: list[str]) -> None:
        self.current_plan_json = json.dumps(plan)

    def get_runtime_state(self) -> dict[str, Any] | None:
        return json.loads(self.runtime_state_json) if self.runtime_state_json else None

    def set_runtime_state(self, state: dict[str, Any]) -> None:
        self.runtime_state_json = json.dumps(state, default=str)


class EventModel(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    task: Mapped[TaskModel] = relationship("TaskModel", back_populates="events")

    def get_payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    def set_payload(self, payload: dict[str, Any]) -> None:
        self.payload_json = json.dumps(payload, default=str)


class ApprovalModel(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), nullable=False, index=True)
    action_name: Mapped[str] = mapped_column(String(128), nullable=False)
    action_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None = pending
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[TaskModel] = relationship("TaskModel", back_populates="approvals")


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # diff, log, report, test_output
    content: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    task: Mapped[TaskModel] = relationship("TaskModel", back_populates="artifacts")
