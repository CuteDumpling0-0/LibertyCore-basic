"""
app/main.py — FastAPI service exposing the Persistent Goal-Agent Platform API.
Routes:
  POST   /tasks                 Create task from goal
  GET    /tasks                 List recent tasks
  GET    /tasks/{id}            Current status, criteria, budget
  GET    /tasks/{id}/events     Ordered execution timeline
  POST   /tasks/{id}/pause      Pause at a safe checkpoint
  POST   /tasks/{id}/resume     Resume from durable state
  POST   /tasks/{id}/cancel     Cancel future work
  POST   /tasks/{id}/approve    Approve a named gated action
  GET    /tasks/{id}/artifacts  Diffs, logs, reports
  GET    /tasks/{id}/report     Get blocked/success outcome report
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.config import get_settings
from app.persistence.db import AsyncSessionLocal, create_all_tables, get_db
from app.persistence.repositories import (
    ApprovalRepository,
    ArtifactRepository,
    EventRepository,
    TaskRepository,
)
from app.schemas import (
    ApproveActionRequest,
    ArtifactInfo,
    BlockedReport,
    Budget,
    CreateTaskRequest,
    EventRecord,
    EventKind,
    FailureClass,
    TaskDetail,
    TaskStatus,
    TaskSummary,
)
from app.models.routing import PRESETS, auto_detect_preset
from app.models.gateway import BudgetLimitError, FreeCloudUnavailableError, ModelCallError
from app.workflow.graph import WorkflowController
from app.workflow.recovery import create_blocked_report, restore_workflow_state
from app.workflow.state import WorkflowState
from app.tools.safety import validate_workspace

log = structlog.get_logger(__name__)

# Active in-memory runners by task_id to allow pause/resume/cancel
_active_controllers: dict[str, WorkflowController] = {}
_workflow_tasks: set[asyncio.Task[None]] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("initializing_goal_agent_service")
    await create_all_tables()
    await _recover_active_workflows()
    yield
    log.info("shutting_down_goal_agent_service")


app = FastAPI(
    title="Persistent Goal-Agent Platform",
    version="0.1.0",
    description="Local-first, multi-model durable agent platform for verified outcomes.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _run_workflow_background(controller: WorkflowController) -> None:
    try:
        await controller.run()
    except Exception as exc:
        log.exception("unhandled_workflow_error", task_id=controller.state.task_id, error=str(exc))
        if (
            isinstance(exc, FreeCloudUnavailableError)
            and controller.state.model_preset == "free_cloud"
        ):
            await _request_free_local_fallback(controller, str(exc))
            return
        if isinstance(exc, BudgetLimitError):
            controller.state.failure_class = FailureClass.BUDGET_EXHAUSTED
            controller.state.blocked_reason = str(exc)
            await controller._save_checkpoint(TaskStatus.BUDGET_EXHAUSTED)
            return
        controller.state.failure_class = FailureClass.TRANSIENT_PROVIDER if isinstance(exc, ModelCallError) else FailureClass.UNKNOWN
        controller.state.blocked_reason = f"Workflow stopped unexpectedly: {exc}"
        try:
            await controller._save_checkpoint(TaskStatus.FAILED)
        except Exception:
            log.exception("failed_to_persist_workflow_failure", task_id=controller.state.task_id)
    finally:
        _active_controllers.pop(controller.state.task_id, None)


async def _request_free_local_fallback(controller: WorkflowController, reason: str) -> None:
    state = controller.state
    message = (
        "All configured Free Cloud providers are unavailable. Switch this task to "
        f"Free Local (Ollama)? Details: {reason}"
    )
    async with AsyncSessionLocal() as session:
        approval = await ApprovalRepository(session).request(
            state.task_id,
            "switch_to_free_local",
            message,
        )
    state.pending_approval_id = approval.id
    state.pending_approval_stage = "free_cloud_fallback"
    state.blocked_reason = message
    await controller._save_checkpoint(TaskStatus.NEEDS_APPROVAL)
    await controller._emit_event(
        EventKind.APPROVAL_REQUESTED,
        {"tool": "switch_to_free_local", "approval_id": approval.id, "reason": reason},
    )


async def _recover_active_workflows() -> None:
    async with AsyncSessionLocal() as session:
        tasks = await TaskRepository(session).list_active()
        states = []
        for task in tasks:
            try:
                states.append(await restore_workflow_state(task))
            except Exception as exc:
                log.exception("workflow_recovery_failed", task_id=task.id, error=str(exc))
                await TaskRepository(session).update_status(
                    task,
                    TaskStatus.FAILED,
                    failure_class=FailureClass.UNKNOWN.value,
                    blocked_reason=f"Could not restore workflow state: {exc}",
                )

    for state in states:
        controller = WorkflowController(state)
        _active_controllers[state.task_id] = controller
        _schedule_workflow(controller)


def _schedule_workflow(controller: WorkflowController) -> None:
    task = asyncio.create_task(_run_workflow_background(controller))
    _workflow_tasks.add(task)
    task.add_done_callback(_workflow_tasks.discard)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/presets")
async def get_presets() -> dict[str, Any]:
    """Return a safe, plain-language status for the automatic setup UI."""
    settings = get_settings()
    detected = auto_detect_preset(settings)
    active = PRESETS[detected]
    free_cloud_providers = []
    if settings.groq_api_key:
        free_cloud_providers.append("Groq")
    if settings.google_api_key:
        free_cloud_providers.append("Gemini API")
    if settings.openrouter_api_key:
        free_cloud_providers.append("OpenRouter")
    return {
        "mode": "automatic",
        "name": active["name"],
        "description": active["tagline"],
        "cost_estimate": active["cost_estimate"],
        "needs_setup": detected == "free_local",
        "free_cloud_available": bool(free_cloud_providers),
        "free_cloud_providers": free_cloud_providers,
        "setup_hint": (
            "Add one API key to .env, then restart the service."
            if detected == "free_local"
            else "Your configured API key will be used automatically."
        ),
    }


@app.post("/tasks", response_model=TaskSummary, status_code=status.HTTP_201_CREATED)
async def create_task(
    req: CreateTaskRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> Any:
    try:
        workspace = str(validate_workspace(req.workspace))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    settings = get_settings()
    if req.model_preset == "free_cloud":
        if not (settings.groq_api_key or settings.openrouter_api_key):
            if not settings.google_api_key:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "free_cloud_unavailable",
                        "message": (
                            "No Free Cloud provider is configured. Switch to Free Local "
                            "(Ollama)?"
                        ),
                    },
                )
        budget = (req.budget or Budget(
            max_iterations=settings.default_max_iterations,
            max_minutes=settings.default_max_minutes,
            max_cost_usd=settings.default_max_cost_usd,
        )).model_copy(update={"max_cost_usd": 0.0})
    else:
        budget = req.budget or Budget(
            max_iterations=settings.default_max_iterations,
            max_minutes=settings.default_max_minutes,
            max_cost_usd=settings.default_max_cost_usd,
        )
    if req.model_preset == "free_local":
        budget = budget.model_copy(update={"max_cost_usd": 0.0})

    task_repo = TaskRepository(db)
    task_model = await task_repo.create(
        objective=req.goal,
        workspace=workspace,
        max_iterations=budget.max_iterations,
        max_minutes=budget.max_minutes,
        max_cost_usd=budget.max_cost_usd,
    )

    state = WorkflowState(
        task_id=task_model.id,
        workspace=workspace,
        budget=budget,
        model_preset=req.model_preset,
        initial_constraints=req.constraints,
    )
    controller = WorkflowController(state)
    _active_controllers[task_model.id] = controller

    # Run state machine in background
    background_tasks.add_task(_run_workflow_background, controller)

    return TaskSummary(
        id=task_model.id,
        status=TaskStatus(task_model.status),
        objective=task_model.objective,
        workspace=task_model.workspace,
        created_at=task_model.created_at,
        updated_at=task_model.updated_at,
        iteration=task_model.iteration,
        total_cost_usd=task_model.total_cost_usd,
        criteria_status=[],
    )


@app.get("/tasks", response_model=list[TaskSummary])
async def list_tasks(limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)) -> Any:
    task_repo = TaskRepository(db)
    tasks = await task_repo.list_all(limit=limit, offset=offset)
    return [
        TaskSummary(
            id=t.id,
            status=TaskStatus(t.status),
            objective=t.objective,
            workspace=t.workspace,
            created_at=t.created_at,
            updated_at=t.updated_at,
            iteration=t.iteration,
            total_cost_usd=t.total_cost_usd,
            criteria_status=[],
        )
        for t in tasks
    ]


@app.get("/tasks/{task_id}", response_model=TaskDetail)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)) -> Any:
    task_repo = TaskRepository(db)
    task = await task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    contract_data = task.get_contract()
    criteria_list = contract_data.get("success_criteria", []) if contract_data else []

    return TaskDetail(
        id=task.id,
        status=TaskStatus(task.status),
        objective=task.objective,
        workspace=task.workspace,
        created_at=task.created_at,
        updated_at=task.updated_at,
        iteration=task.iteration,
        total_cost_usd=task.total_cost_usd,
        criteria_status=criteria_list,
        current_plan=task.get_plan(),
        budget=Budget(
            max_iterations=task.max_iterations,
            max_minutes=task.max_minutes,
            max_cost_usd=task.max_cost_usd,
        ),
        elapsed_minutes=0.0,
        failure_class=task.failure_class,
        blocked_reason=task.blocked_reason,
        checkpoint_id=task.checkpoint_id,
    )


@app.get("/tasks/{task_id}/events", response_model=list[EventRecord])
async def get_task_events(task_id: str, limit: int = 200, db: AsyncSession = Depends(get_db)) -> Any:
    event_repo = EventRepository(db)
    events = await event_repo.list_for_task(task_id, limit=limit)
    return [
        EventRecord(
            id=e.id,
            task_id=e.task_id,
            kind=e.kind,
            iteration=e.iteration,
            payload=e.get_payload(),
            created_at=e.created_at,
        )
        for e in events
    ]


@app.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    controller = _active_controllers.get(task_id)
    if controller:
        controller.state.is_paused = True
    task_repo = TaskRepository(db)
    task = await task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await task_repo.update_status(task, TaskStatus.PAUSED)
    return {"status": "paused", "task_id": task_id}


@app.post("/tasks/{task_id}/resume")
async def resume_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if task_id in _active_controllers:
        return {"status": "already_running", "task_id": task_id}

    task_repo = TaskRepository(db)
    task = await task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    state = await restore_workflow_state(task)
    if state.status == TaskStatus.NEEDS_APPROVAL and (
        state.current_plan_steps or state.pending_approval_stage == "verifying"
    ):
        state.resume_pending_approval = True
    else:
        state.status = TaskStatus.PLANNING
    controller = WorkflowController(state)
    _active_controllers[task_id] = controller
    background_tasks.add_task(_run_workflow_background, controller)

    return {"status": "resumed", "task_id": task_id}


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    controller = _active_controllers.get(task_id)
    if controller:
        controller.state.is_cancelled = True
    task_repo = TaskRepository(db)
    task = await task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await task_repo.update_status(task, TaskStatus.CANCELLED)
    return {"status": "cancelled", "task_id": task_id}


@app.post("/tasks/{task_id}/approve")
async def approve_action(
    task_id: str,
    req: ApproveActionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ap_repo = ApprovalRepository(db)
    approval = await ap_repo.resolve_for_task(
        task_id=task_id,
        approval_id=req.action_id,
        granted=req.approved,
        reason=req.reason,
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Pending approval request not found for this task")

    if req.approved:
        if approval.action_name == "switch_to_free_local":
            task_repo = TaskRepository(db)
            task = await task_repo.get(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            runtime_state = task.get_runtime_state() or {}
            runtime_state["model_preset"] = "free_local"
            runtime_state["pending_approval_id"] = None
            runtime_state["pending_approval_stage"] = None
            await task_repo.save_runtime_state(task, runtime_state)
        # Resume task execution
        return await resume_task(task_id, background_tasks, db)
    else:
        task_repo = TaskRepository(db)
        task = await task_repo.get(task_id)
        if task:
            await task_repo.update_status(task, TaskStatus.BLOCKED, blocked_reason="Action approval denied by user.")
        return {"status": "blocked_by_user", "task_id": task_id}


@app.get("/tasks/{task_id}/artifacts", response_model=list[ArtifactInfo])
async def get_task_artifacts(task_id: str, db: AsyncSession = Depends(get_db)) -> Any:
    art_repo = ArtifactRepository(db)
    artifacts = await art_repo.list_for_task(task_id)
    return [
        ArtifactInfo(
            id=a.id,
            task_id=a.task_id,
            name=a.name,
            kind=a.kind,
            size_bytes=a.size_bytes,
            created_at=a.created_at,
        )
        for a in artifacts
    ]


@app.get("/tasks/{task_id}/report", response_model=BlockedReport)
async def get_task_report(task_id: str, db: AsyncSession = Depends(get_db)) -> Any:
    task_repo = TaskRepository(db)
    task = await task_repo.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    state = await restore_workflow_state(task)
    return create_blocked_report(state)


# ─── Static UI Mount ──────────────────────────────────────────────────────────
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
