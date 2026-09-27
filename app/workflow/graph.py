"""
app/workflow/graph.py — Durable Workflow State Machine.
Executes the full persistent goal loop:
CREATED -> CONTRACT_REVIEW -> CONTEXT_GATHERING -> PLANNING -> EXECUTING
 -> OBSERVING -> VERIFYING -> REVIEWING -> SUCCESS / BLOCKED / BUDGET_EXHAUSTED
Persists state and logs audit events to DB after every node.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.agents.contract import parse_goal_to_contract
from app.agents.executor import execute_step
from app.agents.planner import create_plan
from app.agents.reviewer import review_progress
from app.context.memory_policy import MemoryRecord
from app.context.openviking import get_openviking
from app.context.retrieval import retrieve_context_for_iteration
from app.models.gateway import FreeCloudUnavailableError
from app.models.routing import make_router
from app.persistence.db import AsyncSessionLocal
from app.persistence.repositories import (
    ArtifactRepository,
    ApprovalRepository,
    EventRepository,
    TaskRepository,
)
from app.schemas import (
    CriterionState,
    EventKind,
    FailureClass,
    TaskStatus,
)
from app.tools.git import git_diff
from app.tools.registry import SERVER_APPROVAL_REQUIRED_TOOLS
from app.verification.registry import get_verifier_registry
from app.workflow.policies import (
    check_budgets,
    check_stall,
    compute_fingerprint,
    evaluate_completion,
)
from app.workflow.state import WorkflowState

log = structlog.get_logger(__name__)

class WorkflowController:
    """
    Manages the lifecycle and state transitions for a single task.
    """

    def __init__(self, state: WorkflowState) -> None:
        self.state = state
        self.router = make_router(state.model_preset, free_only=state.budget.max_cost_usd == 0)

    async def _emit_event(self, kind: EventKind, payload: dict[str, Any]) -> None:
        async with AsyncSessionLocal() as session:
            events_repo = EventRepository(session)
            await events_repo.append(
                task_id=self.state.task_id,
                kind=kind,
                iteration=self.state.iteration,
                payload=payload,
            )

    async def _save_checkpoint(self, status: TaskStatus) -> None:
        checkpoint_id = f"chk_{self.state.iteration}_{uuid.uuid4().hex[:6]}"
        self.state.checkpoint_id = checkpoint_id
        self.state.status = status

        async with AsyncSessionLocal() as session:
            task_repo = TaskRepository(session)
            task = await task_repo.get(self.state.task_id)
            if task:
                await task_repo.update_status(
                    task,
                    status=status,
                    failure_class=self.state.failure_class.value if self.state.failure_class else None,
                    blocked_reason=self.state.blocked_reason,
                )
                await task_repo.save_checkpoint(task, checkpoint_id)
                if self.state.contract:
                    await task_repo.save_contract(task, self.state.contract.model_dump(mode="json"))
                plan_descriptions = [s.description for s in self.state.current_plan_steps]
                await task_repo.save_plan(task, plan_descriptions)
                await task_repo.save_runtime_state(task, self.state.snapshot())

        await self._emit_event(
            EventKind.CHECKPOINT,
            {"checkpoint_id": checkpoint_id, "status": status.value, "iteration": self.state.iteration},
        )

    async def _stop_if_runtime_budget_exhausted(self) -> bool:
        exceeded, reason = check_budgets(self.state, check_iteration=False)
        if not exceeded:
            return False
        self.state.failure_class = FailureClass.BUDGET_EXHAUSTED
        self.state.blocked_reason = reason
        await self._save_checkpoint(TaskStatus.BUDGET_EXHAUSTED)
        await self._emit_event(EventKind.BUDGET_WARNING, {"reason": reason})
        return True

    async def _stop_for_control_request(self) -> bool:
        if self.state.is_cancelled:
            await self._save_checkpoint(TaskStatus.CANCELLED)
            return True
        if self.state.is_paused:
            await self._save_checkpoint(TaskStatus.PAUSED)
            return True
        return False

    async def run(self) -> TaskStatus:
        """Runs the loop until a terminal state or pause/approval barrier is reached."""
        log.info("starting_workflow_execution", task_id=self.state.task_id)
        if await self._stop_for_control_request():
            return self.state.status

        # ── 1. CONTRACT PARSING & REVIEW ──────────────────────────────────────
        if self.state.status == TaskStatus.CREATED or not self.state.contract:
            await self._step_contract_review()
            if await self._stop_for_control_request():
                return self.state.status
            if self.state.status != TaskStatus.CONTEXT_GATHERING:
                return self.state.status

        # ── 2. ACTIVE REACTION LOOP ───────────────────────────────────────────
        if self.state.resume_pending_approval and (
            self.state.current_plan_steps or self.state.pending_approval_stage == "verifying"
        ):
            if self.state.pending_approval_stage == "verifying":
                self.state.status = TaskStatus.VERIFYING
                await self._step_verifying()
                if await self._stop_for_control_request():
                    return self.state.status
                if self.state.status == TaskStatus.NEEDS_APPROVAL:
                    return TaskStatus.NEEDS_APPROVAL
                if self.state.status == TaskStatus.BUDGET_EXHAUSTED:
                    return TaskStatus.BUDGET_EXHAUSTED
            else:
                self.state.status = TaskStatus.EXECUTING
                await self._step_executing()
                if await self._stop_for_control_request():
                    return self.state.status
                if self.state.status == TaskStatus.NEEDS_APPROVAL:
                    return TaskStatus.NEEDS_APPROVAL
                if self.state.status == TaskStatus.BUDGET_EXHAUSTED:
                    return TaskStatus.BUDGET_EXHAUSTED
                await self._step_observing()
                if await self._stop_for_control_request():
                    return self.state.status
                if self.state.status == TaskStatus.BUDGET_EXHAUSTED:
                    return TaskStatus.BUDGET_EXHAUSTED
                await self._step_verifying()
                if await self._stop_for_control_request():
                    return self.state.status
                if self.state.status == TaskStatus.NEEDS_APPROVAL:
                    return TaskStatus.NEEDS_APPROVAL
                if self.state.status == TaskStatus.BUDGET_EXHAUSTED:
                    return TaskStatus.BUDGET_EXHAUSTED
            await self._step_reviewing()
            if await self._stop_for_control_request():
                return self.state.status
            self.state.resume_pending_approval = False
            self.state.pending_approval_stage = None
            await self._save_checkpoint(TaskStatus.PLANNING)

        while self.state.is_active():
            # Check budgets before doing work
            budget_exceeded, reason = check_budgets(self.state)
            if budget_exceeded:
                self.state.failure_class = FailureClass.BUDGET_EXHAUSTED
                self.state.blocked_reason = reason
                await self._save_checkpoint(TaskStatus.BUDGET_EXHAUSTED)
                await self._emit_event(EventKind.BUDGET_WARNING, {"reason": reason})
                return TaskStatus.BUDGET_EXHAUSTED

            # Check stall
            is_stalled, stall_reason = check_stall(self.state)
            if is_stalled:
                log.warning("workflow_stalled", reason=stall_reason)
                # Escalate model first before giving up
                if self.state.failed_review_count < 2:
                    self.router.escalate("planner")
                    self.router.escalate("reviewer")
                    self.state.failed_review_count += 1
                else:
                    self.state.failure_class = FailureClass.FLAWED_PLAN
                    self.state.blocked_reason = stall_reason
                    await self._save_checkpoint(TaskStatus.BLOCKED)
                    return TaskStatus.BLOCKED

            # Advance iteration
            self.state.iteration += 1
            async with AsyncSessionLocal() as session:
                task_repo = TaskRepository(session)
                task = await task_repo.get(self.state.task_id)
                if task:
                    await task_repo.increment_iteration(task)

            # Node: CONTEXT GATHERING
            await self._step_context_gathering()
            await self._save_checkpoint(TaskStatus.PLANNING)
            if await self._stop_for_control_request():
                return self.state.status

            # Node: PLANNING
            await self._step_planning()
            if self.state.status == TaskStatus.BUDGET_EXHAUSTED:
                return TaskStatus.BUDGET_EXHAUSTED
            await self._save_checkpoint(TaskStatus.EXECUTING)
            if await self._stop_for_control_request():
                return self.state.status

            # Node: EXECUTING
            await self._step_executing()
            if await self._stop_for_control_request():
                return self.state.status
            if self.state.status == TaskStatus.NEEDS_APPROVAL:
                return TaskStatus.NEEDS_APPROVAL
            if self.state.status == TaskStatus.BUDGET_EXHAUSTED:
                return TaskStatus.BUDGET_EXHAUSTED
            await self._save_checkpoint(TaskStatus.OBSERVING)

            # Node: OBSERVING
            await self._step_observing()
            if await self._stop_for_control_request():
                return self.state.status
            if self.state.status == TaskStatus.BUDGET_EXHAUSTED:
                return TaskStatus.BUDGET_EXHAUSTED
            await self._save_checkpoint(TaskStatus.VERIFYING)

            # Node: VERIFYING
            await self._step_verifying()
            if await self._stop_for_control_request():
                return self.state.status
            if self.state.status == TaskStatus.NEEDS_APPROVAL:
                return TaskStatus.NEEDS_APPROVAL
            if self.state.status == TaskStatus.BUDGET_EXHAUSTED:
                return TaskStatus.BUDGET_EXHAUSTED
            await self._save_checkpoint(TaskStatus.REVIEWING)

            # Node: REVIEWING
            await self._step_reviewing()
            if await self._stop_for_control_request():
                return self.state.status
            if self.state.status == TaskStatus.BUDGET_EXHAUSTED:
                return TaskStatus.BUDGET_EXHAUSTED

            # Check for completion
            is_complete, completion_notes = evaluate_completion(self.state)
            if is_complete:
                log.info("workflow_succeeded", notes=completion_notes)
                await self._step_record_success_memory()
                await self._save_checkpoint(TaskStatus.SUCCESS)
                await self._emit_event(EventKind.STATE_TRANSITION, {"from": "reviewing", "to": "success", "notes": completion_notes})
                return TaskStatus.SUCCESS

            # Persist checkpoint at end of iteration
            await self._save_checkpoint(TaskStatus.PLANNING)

        return self.state.status

    # ── Step Implementations ──────────────────────────────────────────────────

    async def _step_contract_review(self) -> None:
        if await self._stop_if_runtime_budget_exhausted():
            return
        log.info("node_contract_review", task_id=self.state.task_id)
        await self._emit_event(EventKind.STATE_TRANSITION, {"from": "created", "to": "contract_review"})

        async with AsyncSessionLocal() as session:
            task_repo = TaskRepository(session)
            task = await task_repo.get(self.state.task_id)
            objective = task.objective if task else ""

        try:
            contract = await parse_goal_to_contract(
                goal=objective,
                workspace=self.state.workspace,
                constraints=self.state.initial_constraints,
                budget=self.state.budget,
                router=self.router,
            )
            self.state.contract = contract
            self.state.criteria = contract.success_criteria
            self.state.status = TaskStatus.CONTEXT_GATHERING
            await self._save_checkpoint(TaskStatus.CONTEXT_GATHERING)
        except FreeCloudUnavailableError:
            raise
        except Exception as exc:
            log.error("contract_generation_failed", error=str(exc))
            self.state.failure_class = FailureClass.AMBIGUOUS_GOAL
            self.state.blocked_reason = f"Could not create clear task contract: {exc}"
            await self._save_checkpoint(TaskStatus.BLOCKED)

    async def _step_context_gathering(self) -> None:
        log.info("node_context_gathering", iteration=self.state.iteration)
        failing_criteria = [c.description for c in self.state.criteria if c.state != CriterionState.PASSED]
        context_items = await retrieve_context_for_iteration(
            objective=self.state.contract.objective if self.state.contract else "",
            failing_criteria=failing_criteria,
            recent_errors=self.state.recent_errors,
            active_files=self.state.active_files,
        )
        self.state.recent_observations.extend(context_items)

    async def _step_planning(self) -> None:
        if await self._stop_if_runtime_budget_exhausted():
            return
        log.info("node_planning", iteration=self.state.iteration)
        unresolved = [c for c in self.state.criteria if c.state != CriterionState.PASSED]
        plan, cost = await create_plan(
            contract=self.state.contract.model_dump(mode="json") if self.state.contract else {},
            iteration=self.state.iteration,
            unresolved_criteria=unresolved,
            observations=self.state.recent_observations,
            gaps=self.state.unresolved_gaps,
            failures=self.state.recent_errors,
            router=self.router,
            max_cost_usd=max(0.0, self.state.budget.max_cost_usd - self.state.total_cost_usd),
        )
        self.state.total_cost_usd += cost
        self.state.current_plan_steps = plan.steps
        await self._emit_event(
            EventKind.PLAN_CREATED,
            {"rationale": plan.rationale, "steps": [s.model_dump() for s in plan.steps]},
        )

    async def _step_executing(self) -> None:
        log.info("node_executing", steps_count=len(self.state.current_plan_steps))
        self.state.recent_tool_results = []

        contract = self.state.contract
        auth_actions = contract.authorized_actions if contract else ["read_files", "edit_files", "run_tests", "git_diff"]
        approval_actions = SERVER_APPROVAL_REQUIRED_TOOLS

        for step in self.state.current_plan_steps:
            if await self._stop_for_control_request():
                return
            if await self._stop_if_runtime_budget_exhausted():
                return
            approval_granted = False
            # Check approval gate
            if step.tool in approval_actions:
                detail = step.model_dump_json()
                log.info("tool_requires_approval", tool=step.tool)
                async with AsyncSessionLocal() as session:
                    ap_repo = ApprovalRepository(session)
                    approved = await ap_repo.granted_for_step(self.state.task_id, step.tool, detail)
                    if approved:
                        approval_granted = True
                        self.state.pending_approval_id = None
                        self.state.pending_approval_stage = None
                    else:
                        ap = await ap_repo.request(self.state.task_id, step.tool, detail)
                        self.state.pending_approval_id = ap.id
                        self.state.pending_approval_stage = "executing"
                        self.state.status = TaskStatus.NEEDS_APPROVAL
                        await self._save_checkpoint(TaskStatus.NEEDS_APPROVAL)
                        await self._emit_event(EventKind.APPROVAL_REQUESTED, {"tool": step.tool, "approval_id": ap.id})
                        return

            await self._emit_event(EventKind.TOOL_CALL, {"tool": step.tool, "input": step.tool_input})
            result, cost = await execute_step(
                step=step,
                workspace=self.state.workspace,
                authorized_actions=auth_actions,
                router=self.router,
                approval_granted=approval_granted,
                max_cost_usd=max(0.0, self.state.budget.max_cost_usd - self.state.total_cost_usd),
            )
            self.state.total_cost_usd += cost
            self.state.recent_tool_results.append(result)
            await self._emit_event(EventKind.TOOL_OUTPUT, {"tool": step.tool, "success": result.success, "output": result.output[:1000]})

            if not result.success:
                self.state.recent_errors.append(f"{step.tool}: {result.output[:300]}")

    async def _step_observing(self) -> None:
        if await self._stop_if_runtime_budget_exhausted():
            return
        log.info("node_observing", iteration=self.state.iteration)
        diff_res = await git_diff({}, self.state.workspace)
        if diff_res.success:
            self.state.recent_diff = diff_res.output
            self.state.diff_fingerprints.append(compute_fingerprint(diff_res.output))

            # Save diff artifact
            if diff_res.output.strip() and diff_res.output != "(no changes)":
                async with AsyncSessionLocal() as session:
                    art_repo = ArtifactRepository(session)
                    await art_repo.save(
                        task_id=self.state.task_id,
                        name=f"diff_iter_{self.state.iteration}.patch",
                        kind="diff",
                        content=diff_res.output,
                    )

        # Update recent observations
        obs = [f"Tool '{r.tool}' -> {'SUCCESS' if r.success else 'FAILURE'}: {r.output[:200]}" for r in self.state.recent_tool_results]
        self.state.recent_observations.extend(obs)

    async def _step_verifying(self) -> None:
        log.info("node_verifying", iteration=self.state.iteration)
        verifier_registry = get_verifier_registry()

        for criterion in self.state.criteria:
            if criterion.type.value == "review":
                continue
            if await self._stop_for_control_request():
                return
            if await self._stop_if_runtime_budget_exhausted():
                return
            command = criterion.command or ("pytest" if criterion.type.value == "test" else None)
            if criterion.type.value in {"command", "test", "security"} and command:
                detail = criterion.model_dump_json(exclude={"state", "evidence"})
                async with AsyncSessionLocal() as session:
                    ap_repo = ApprovalRepository(session)
                    approved = await ap_repo.granted_for_step(
                        self.state.task_id, "verify_command", detail
                    )
                    if approved:
                        self.state.pending_approval_id = None
                        self.state.pending_approval_stage = None
                    else:
                        approval = await ap_repo.request(
                            self.state.task_id, "verify_command", detail
                        )
                        self.state.pending_approval_id = approval.id
                        self.state.pending_approval_stage = "verifying"
                        self.state.status = TaskStatus.NEEDS_APPROVAL
                        await self._save_checkpoint(TaskStatus.NEEDS_APPROVAL)
                        await self._emit_event(
                            EventKind.APPROVAL_REQUESTED,
                            {"tool": "verify_command", "approval_id": approval.id},
                        )
                        return
            v_res = await verifier_registry.verify(criterion, self.state.workspace)
            criterion.state = v_res.state
            criterion.evidence = v_res.evidence
            if criterion.type.value in ("command", "test", "security"):
                self.state.recent_test_output = v_res.evidence
            await self._emit_event(
                EventKind.CRITERION_UPDATED,
                {"criterion_id": criterion.id, "state": v_res.state.value, "evidence": v_res.evidence[:250]},
            )

    async def _step_reviewing(self) -> None:
        if await self._stop_if_runtime_budget_exhausted():
            return
        log.info("node_reviewing", iteration=self.state.iteration)
        contract_data = self.state.contract.model_dump(mode="json") if self.state.contract else {}
        plan_desc = [s.description for s in self.state.current_plan_steps]
        unresolved = [c for c in self.state.criteria if c.state != CriterionState.PASSED]

        review, cost = await review_progress(
            contract=contract_data,
            current_plan=plan_desc,
            evidence=self.state.recent_observations,
            diffs=self.state.recent_diff,
            test_output=self.state.recent_test_output,
            unresolved_criteria=unresolved,
            router=self.router,
            max_cost_usd=max(0.0, self.state.budget.max_cost_usd - self.state.total_cost_usd),
        )
        self.state.total_cost_usd += cost
        self.state.last_review = review
        self.state.unresolved_gaps = review.gaps
        self.state.gap_history.append(review.gaps)

        # Update review-based criteria with reviewer evidence
        for status_item in review.criteria_status:
            for c in self.state.criteria:
                if c.id == status_item.criterion_id and c.type.value == "review":
                    c.state = status_item.state
                    c.evidence = status_item.evidence

        await self._emit_event(
            EventKind.REVIEWER_OUTPUT,
            {
                "goal_achieved": review.goal_achieved,
                "gaps": review.gaps,
                "risks": review.risks,
                "next_best_action": review.next_best_action,
            },
        )

    async def _step_record_success_memory(self) -> None:
        """Stores verified successful fix into OpenViking memory."""
        try:
            ov = get_openviking()
            rec = MemoryRecord(
                id=str(uuid.uuid4()),
                category="memories",
                fact=f"Task {self.state.task_id}: {self.state.contract.objective if self.state.contract else ''} successfully completed.",
                provenance_task_id=self.state.task_id,
                source_file_or_cmd="workflow_controller",
                confidence=1.0,
            )
            await ov.store_record(rec, is_verified=True)
        except Exception as exc:
            log.warning("failed_to_store_success_memory", error=str(exc))
