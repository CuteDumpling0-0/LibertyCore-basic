"""
app/persistence/repositories.py — Data-access layer. All DB interaction goes
through these repository classes so the rest of the app stays DB-agnostic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import ArtifactModel, ApprovalModel, EventModel, TaskModel
from app.schemas import EventKind, TaskStatus


def _uid() -> str:
    return str(uuid.uuid4())


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        objective: str,
        workspace: str,
        max_iterations: int,
        max_minutes: int,
        max_cost_usd: float,
    ) -> TaskModel:
        task = TaskModel(
            id=_uid(),
            objective=objective,
            workspace=workspace,
            max_iterations=max_iterations,
            max_minutes=max_minutes,
            max_cost_usd=max_cost_usd,
        )
        self._s.add(task)
        await self._s.commit()
        await self._s.refresh(task)
        return task

    async def get(self, task_id: str) -> TaskModel | None:
        return await self._s.get(TaskModel, task_id)

    async def list_all(self, limit: int = 50, offset: int = 0) -> list[TaskModel]:
        result = await self._s.execute(
            select(TaskModel).order_by(TaskModel.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def list_active(self) -> list[TaskModel]:
        active_statuses = [
            "created", "contract_review", "context_gathering", "planning",
            "executing", "observing", "verifying", "reviewing",
        ]
        result = await self._s.execute(
            select(TaskModel).where(TaskModel.status.in_(active_statuses))
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        task: TaskModel,
        status: TaskStatus,
        *,
        failure_class: str | None = None,
        blocked_reason: str | None = None,
    ) -> None:
        task.status = status.value
        if failure_class:
            task.failure_class = failure_class
        if blocked_reason:
            task.blocked_reason = blocked_reason
        if status in (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.BUDGET_EXHAUSTED, TaskStatus.BLOCKED):
            task.finished_at = datetime.now(timezone.utc)
        task.updated_at = datetime.now(timezone.utc)
        await self._s.commit()

    async def increment_iteration(self, task: TaskModel, cost_delta: float = 0.0, tokens_delta: int = 0) -> None:
        task.iteration += 1
        task.total_cost_usd += cost_delta
        task.total_tokens += tokens_delta
        task.updated_at = datetime.now(timezone.utc)
        await self._s.commit()

    async def save_contract(self, task: TaskModel, contract: dict[str, Any]) -> None:
        task.set_contract(contract)
        task.updated_at = datetime.now(timezone.utc)
        await self._s.commit()

    async def save_plan(self, task: TaskModel, plan: list[str]) -> None:
        task.set_plan(plan)
        task.updated_at = datetime.now(timezone.utc)
        await self._s.commit()

    async def save_runtime_state(self, task: TaskModel, state: dict[str, Any]) -> None:
        task.set_runtime_state(state)
        task.total_cost_usd = float(state.get("total_cost_usd", task.total_cost_usd))
        task.total_tokens = int(state.get("total_tokens", task.total_tokens))
        task.updated_at = datetime.now(timezone.utc)
        await self._s.commit()

    async def save_checkpoint(self, task: TaskModel, checkpoint_id: str) -> None:
        task.checkpoint_id = checkpoint_id
        task.updated_at = datetime.now(timezone.utc)
        await self._s.commit()


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def append(
        self,
        task_id: str,
        kind: EventKind,
        iteration: int,
        payload: dict[str, Any],
    ) -> EventModel:
        ev = EventModel(id=_uid(), task_id=task_id, kind=kind.value, iteration=iteration)
        ev.set_payload(payload)
        self._s.add(ev)
        await self._s.commit()
        return ev

    async def list_for_task(self, task_id: str, limit: int = 200) -> list[EventModel]:
        result = await self._s.execute(
            select(EventModel)
            .where(EventModel.task_id == task_id)
            .order_by(EventModel.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def request(self, task_id: str, action_name: str, detail: str = "") -> ApprovalModel:
        ap = ApprovalModel(id=_uid(), task_id=task_id, action_name=action_name, action_detail=detail)
        self._s.add(ap)
        await self._s.commit()
        await self._s.refresh(ap)
        return ap

    async def resolve(self, approval_id: str, granted: bool, reason: str = "") -> ApprovalModel | None:
        ap = await self._s.get(ApprovalModel, approval_id)
        if ap is None:
            return None
        ap.granted = granted
        ap.reason = reason
        ap.resolved_at = datetime.now(timezone.utc)
        await self._s.commit()
        return ap

    async def resolve_for_task(
        self, task_id: str, approval_id: str, granted: bool, reason: str = ""
    ) -> ApprovalModel | None:
        result = await self._s.execute(
            select(ApprovalModel).where(
                ApprovalModel.id == approval_id,
                ApprovalModel.task_id == task_id,
                ApprovalModel.granted.is_(None),
            )
        )
        ap = result.scalars().first()
        if ap is None:
            return None
        ap.granted = granted
        ap.reason = reason
        ap.resolved_at = datetime.now(timezone.utc)
        await self._s.commit()
        return ap

    async def pending_for_task(self, task_id: str) -> list[ApprovalModel]:
        result = await self._s.execute(
            select(ApprovalModel)
            .where(ApprovalModel.task_id == task_id, ApprovalModel.granted.is_(None))
        )
        return list(result.scalars().all())

    async def granted_for_step(self, task_id: str, action_name: str, detail: str) -> ApprovalModel | None:
        result = await self._s.execute(
            select(ApprovalModel).where(
                ApprovalModel.task_id == task_id,
                ApprovalModel.action_name == action_name,
                ApprovalModel.action_detail == detail,
                ApprovalModel.granted.is_(True),
            )
        )
        return result.scalars().first()


class ArtifactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, task_id: str, name: str, kind: str, content: str) -> ArtifactModel:
        art = ArtifactModel(
            id=_uid(),
            task_id=task_id,
            name=name,
            kind=kind,
            content=content,
            size_bytes=len(content.encode()),
        )
        self._s.add(art)
        await self._s.commit()
        return art

    async def list_for_task(self, task_id: str) -> list[ArtifactModel]:
        result = await self._s.execute(
            select(ArtifactModel).where(ArtifactModel.task_id == task_id)
        )
        return list(result.scalars().all())
