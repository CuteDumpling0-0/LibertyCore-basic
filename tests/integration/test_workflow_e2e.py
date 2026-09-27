import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from app import main
from app.config import Settings
from app.models.gateway import FreeCloudUnavailableError
from app.persistence.db import AsyncSessionLocal, create_all_tables
from app.persistence.models import ApprovalModel
from app.persistence.repositories import (
    ApprovalRepository,
    ArtifactRepository,
    EventRepository,
    TaskRepository,
)
from app.schemas import (
    Budget,
    ApproveActionRequest,
    CriterionState,
    CriterionType,
    CreateTaskRequest,
    EventKind,
    FailureClass,
    PlanStep,
    SuccessCriterion,
    TaskContract,
    TaskStatus,
)
from app.workflow.recovery import create_blocked_report, restore_workflow_state
from app.workflow.graph import WorkflowController
from app.workflow.state import WorkflowState
from app.verification.registry import VerifierResult


class TestWorkflowE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await create_all_tables()

    async def test_task_lifecycle_persistence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = tmp_dir
            objective = "Implement JWT authentication"

            async with AsyncSessionLocal() as session:
                task_repo = TaskRepository(session)
                task = await task_repo.create(
                    objective=objective,
                    workspace=workspace,
                    max_iterations=10,
                    max_minutes=30,
                    max_cost_usd=5.0,
                )
                self.assertIsNotNone(task.id)
                self.assertEqual(task.status, "created")

                # Append events
                event_repo = EventRepository(session)
                await event_repo.append(
                    task_id=task.id,
                    kind=EventKind.STATE_TRANSITION,
                    iteration=0,
                    payload={"from": "created", "to": "contract_review"},
                )
                events = await event_repo.list_for_task(task.id)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].kind, "state_transition")

                # Test approvals
                approval_repo = ApprovalRepository(session)
                ap = await approval_repo.request(task.id, "run_destructive_command", "rm file")
                self.assertIsNotNone(ap.id)
                self.assertIsNone(ap.granted)

                resolved = await approval_repo.resolve(ap.id, granted=True, reason="User confirmed")
                self.assertIsNotNone(resolved)
                self.assertTrue(resolved.granted)

                # Test artifacts
                art_repo = ArtifactRepository(session)
                art = await art_repo.save(task.id, "test.patch", "diff", "+ new code")
                self.assertIsNotNone(art.id)
                artifacts = await art_repo.list_for_task(task.id)
                self.assertEqual(len(artifacts), 1)
                self.assertEqual(artifacts[0].name, "test.patch")

                # Test state checkpoint and restoration
                contract = TaskContract(
                    goal_id=task.id,
                    objective=objective,
                    success_criteria=[
                        SuccessCriterion(
                            id="c1",
                            type=CriterionType.COMMAND,
                            description="test suite",
                            command="python -V",
                        )
                    ],
                    budgets=Budget(max_iterations=10, max_minutes=30, max_cost_usd=5.0),
                )
                await task_repo.save_contract(task, contract.model_dump(mode="json"))
                await task_repo.save_checkpoint(task, "chk_1_abc123")
                await task_repo.update_status(task, TaskStatus.PLANNING)

                # Restore
                restored_state = await restore_workflow_state(task)
                self.assertEqual(restored_state.task_id, task.id)
                self.assertEqual(restored_state.status, TaskStatus.PLANNING)
                self.assertEqual(restored_state.checkpoint_id, "chk_1_abc123")
                self.assertEqual(len(restored_state.criteria), 1)

                # Generate blocked report test
                restored_state.blocked_reason = "Out of budget"
                restored_state.failure_class = FailureClass.BUDGET_EXHAUSTED
                report = create_blocked_report(restored_state)
                self.assertEqual(report.task_id, task.id)
                self.assertEqual(report.failure_class, FailureClass.BUDGET_EXHAUSTED)

    async def test_approval_cannot_be_resolved_through_another_task(self):
        async with AsyncSessionLocal() as session:
            task_repo = TaskRepository(session)
            owner = await task_repo.create("Owner task objective", ".", 5, 10, 1.0)
            other = await task_repo.create("Other task objective", ".", 5, 10, 1.0)
            approval = await ApprovalRepository(session).request(owner.id, "run_command", "pytest")

            with self.assertRaises(HTTPException) as raised:
                await main.approve_action(
                    other.id,
                    ApproveActionRequest(action_id=approval.id, approved=True),
                    BackgroundTasks(),
                    session,
                )

            self.assertEqual(raised.exception.status_code, 404)
            unchanged = await session.get(ApprovalModel, approval.id)
            self.assertIsNone(unchanged.granted)

    async def test_task_constraints_are_passed_into_workflow_state(self):
        with tempfile.TemporaryDirectory() as workspace:
            constraints = ["Keep the public API unchanged", "Do not add runtime dependencies"]
            async with AsyncSessionLocal() as session:
                created = await main.create_task(
                    CreateTaskRequest(
                        goal="Implement a useful feature safely",
                        workspace=workspace,
                        constraints=constraints,
                        model_preset="free_local",
                        budget=Budget(max_iterations=5, max_minutes=10, max_cost_usd=5.0),
                    ),
                    BackgroundTasks(),
                    session,
                )

            controller = main._active_controllers.pop(created.id)
            self.assertEqual(controller.state.initial_constraints, constraints)
            self.assertEqual(controller.state.budget.max_cost_usd, 0.0)
            self.assertTrue(controller.router.free_only)

    async def test_free_cloud_requires_supported_provider_and_forces_zero_budget(self):
        with tempfile.TemporaryDirectory() as workspace:
            settings = Settings(
                anthropic_api_key="", openai_api_key="", google_api_key="gemini-test",
                groq_api_key="groq-test", openrouter_api_key="openrouter-test",
            )
            with patch("app.main.get_settings", return_value=settings):
                with patch("app.models.routing.get_settings", return_value=settings):
                    async with AsyncSessionLocal() as session:
                        created = await main.create_task(
                            CreateTaskRequest(
                                goal="Implement a free cloud example",
                                workspace=workspace,
                                model_preset="free_cloud",
                                budget=Budget(max_iterations=5, max_minutes=10, max_cost_usd=5.0),
                            ),
                            BackgroundTasks(),
                            session,
                        )

            controller = main._active_controllers.pop(created.id)
            self.assertEqual(controller.state.budget.max_cost_usd, 0.0)
            self.assertTrue(controller.router.free_only)
            primary, fallbacks = controller.router.select("planner")
            self.assertTrue(primary.startswith("groq/"))
            self.assertEqual(
                fallbacks,
                ["gemini/gemini-3.5-flash-lite", "openrouter/free"],
            )

    async def test_free_cloud_without_provider_keys_is_rejected(self):
        settings = Settings(
            anthropic_api_key="", openai_api_key="", google_api_key="",
            groq_api_key="", openrouter_api_key="",
        )
        with patch("app.main.get_settings", return_value=settings):
            with self.assertRaises(HTTPException) as raised:
                await main.create_task(
                    CreateTaskRequest(
                        goal="Implement a free cloud example",
                        workspace=str(Path.cwd()),
                        model_preset="free_cloud",
                    ),
                    BackgroundTasks(),
                    None,
                )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "free_cloud_unavailable")

    async def _queue_free_cloud_fallback(self, workspace):
        async with AsyncSessionLocal() as session:
            task = await TaskRepository(session).create(
                "Free Cloud recovery task", workspace, 5, 10, 0.0
            )
        state = WorkflowState(
            task_id=task.id,
            workspace=workspace,
            status=TaskStatus.PLANNING,
            model_preset="free_cloud",
            budget=Budget(max_iterations=5, max_minutes=10, max_cost_usd=0.0),
        )
        controller = WorkflowController(state)
        controller.run = AsyncMock(
            side_effect=FreeCloudUnavailableError("All free providers are unavailable")
        )
        await main._run_workflow_background(controller)

        async with AsyncSessionLocal() as session:
            stored_task = await TaskRepository(session).get(task.id)
            approvals = await ApprovalRepository(session).pending_for_task(task.id)
        self.assertEqual(stored_task.status, TaskStatus.NEEDS_APPROVAL.value)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].action_name, "switch_to_free_local")
        return task.id, approvals[0].id

    async def test_exhausted_free_cloud_requires_explicit_local_opt_in(self):
        with tempfile.TemporaryDirectory() as workspace:
            task_id, approval_id = await self._queue_free_cloud_fallback(workspace)
            async with AsyncSessionLocal() as session:
                response = await main.approve_action(
                    task_id,
                    ApproveActionRequest(action_id=approval_id, approved=True),
                    BackgroundTasks(),
                    session,
                )

            self.assertEqual(response["status"], "resumed")
            resumed = main._active_controllers.pop(task_id)
            self.assertEqual(resumed.state.model_preset, "free_local")
            self.assertEqual(resumed.router.active_preset["id"], "free_local")

    async def test_free_cloud_contract_failure_also_requests_local_opt_in(self):
        with tempfile.TemporaryDirectory() as workspace:
            async with AsyncSessionLocal() as session:
                task = await TaskRepository(session).create(
                    "Free Cloud contract task", workspace, 5, 10, 0.0
                )
            state = WorkflowState(
                task_id=task.id,
                workspace=workspace,
                model_preset="free_cloud",
                budget=Budget(max_iterations=5, max_minutes=10, max_cost_usd=0.0),
            )
            controller = WorkflowController(state)
            parser = AsyncMock(
                side_effect=FreeCloudUnavailableError("No cloud contract provider responded")
            )
            with patch("app.workflow.graph.parse_goal_to_contract", new=parser):
                await main._run_workflow_background(controller)

            self.assertEqual(state.status, TaskStatus.NEEDS_APPROVAL)
            parser.assert_awaited_once()
            async with AsyncSessionLocal() as session:
                approvals = await ApprovalRepository(session).pending_for_task(task.id)
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0].action_name, "switch_to_free_local")

    async def test_declining_free_cloud_local_fallback_blocks_task(self):
        with tempfile.TemporaryDirectory() as workspace:
            task_id, approval_id = await self._queue_free_cloud_fallback(workspace)
            async with AsyncSessionLocal() as session:
                response = await main.approve_action(
                    task_id,
                    ApproveActionRequest(action_id=approval_id, approved=False),
                    BackgroundTasks(),
                    session,
                )
                task = await TaskRepository(session).get(task_id)

            self.assertEqual(response["status"], "blocked_by_user")
            self.assertEqual(task.status, TaskStatus.BLOCKED.value)
            self.assertNotIn(task_id, main._active_controllers)

    async def test_pause_and_cancel_are_honored_at_node_boundaries(self):
        for flag, expected_status in (
            ("is_paused", TaskStatus.PAUSED),
            ("is_cancelled", TaskStatus.CANCELLED),
        ):
            with self.subTest(flag=flag):
                async with AsyncSessionLocal() as session:
                    task = await TaskRepository(session).create(
                        f"Control task {flag}", ".", 5, 10, 1.0
                    )

                contract = TaskContract(goal_id=task.id, objective=task.objective)
                state = WorkflowState(
                    task_id=task.id,
                    workspace=".",
                    status=TaskStatus.PLANNING,
                    contract=contract,
                    budget=Budget(max_iterations=5, max_minutes=10, max_cost_usd=1.0),
                )

                async def request_control():
                    setattr(state, flag, True)

                controller = WorkflowController(state)
                controller._step_context_gathering = AsyncMock(side_effect=request_control)
                result = await controller.run()

                async with AsyncSessionLocal() as session:
                    persisted_task = await TaskRepository(session).get(task.id)
                    self.assertEqual(persisted_task.status, expected_status.value)
                self.assertEqual(result, expected_status)

    async def test_startup_recovery_schedules_durable_active_task(self):
        async with AsyncSessionLocal() as session:
            task_repo = TaskRepository(session)
            task = await task_repo.create("Resume task objective", ".", 5, 10, 1.0)
            await task_repo.update_status(task, TaskStatus.PLANNING)
            await task_repo.save_runtime_state(task, WorkflowState(task_id=task.id, workspace=".").snapshot())

        initial_ids = set(main._active_controllers)
        with patch("app.main._schedule_workflow") as schedule:
            with patch.object(TaskRepository, "list_active", new=AsyncMock(return_value=[task])):
                await main._recover_active_workflows()

        self.assertIn(task.id, main._active_controllers)
        self.assertTrue(any(call.args[0].state.task_id == task.id for call in schedule.call_args_list))
        for task_id in set(main._active_controllers) - initial_ids:
            main._active_controllers.pop(task_id, None)

    async def test_verifier_requires_approval_and_resumes_without_replaying_plan(self):
        with tempfile.TemporaryDirectory() as workspace:
            criterion = SuccessCriterion(
                id="verify-tests",
                type=CriterionType.TEST,
                description="Run project tests",
                command="pytest",
            )
            async with AsyncSessionLocal() as session:
                task = await TaskRepository(session).create(
                    "Verify task objective", workspace, 5, 10, 1.0
                )
                contract = TaskContract(
                    goal_id=task.id,
                    objective=task.objective,
                    success_criteria=[criterion],
                )
                await TaskRepository(session).save_contract(
                    task, contract.model_dump(mode="json")
                )

            state = WorkflowState(
                task_id=task.id,
                workspace=workspace,
                status=TaskStatus.VERIFYING,
                contract=contract,
                criteria=[criterion],
                current_plan_steps=[PlanStep(
                    id="edit",
                    description="Edit a file",
                    tool="edit_file",
                    tool_input={"path": "result.txt", "mode": "write", "content": "done"},
                    expected_evidence="file updated",
                )],
            )
            registry = AsyncMock()
            registry.verify.return_value = VerifierResult(True, "approved test output")
            controller = WorkflowController(state)
            with patch("app.workflow.graph.get_verifier_registry", return_value=registry):
                await controller._step_verifying()
            self.assertEqual(state.status, TaskStatus.NEEDS_APPROVAL)
            registry.verify.assert_not_awaited()

            async with AsyncSessionLocal() as session:
                approval = (await ApprovalRepository(session).pending_for_task(task.id))[0]
                await ApprovalRepository(session).resolve_for_task(
                    task.id, approval.id, granted=True
                )
                task = await TaskRepository(session).get(task.id)
                restored = await restore_workflow_state(task)

            self.assertEqual(restored.pending_approval_stage, "verifying")
            restored.resume_pending_approval = True
            resumed = WorkflowController(restored)
            resumed._step_executing = AsyncMock()
            resumed._step_observing = AsyncMock()

            async def pause_after_review():
                restored.is_paused = True

            resumed._step_reviewing = AsyncMock(side_effect=pause_after_review)
            with patch("app.workflow.graph.get_verifier_registry", return_value=registry):
                await resumed.run()

            resumed._step_executing.assert_not_awaited()
            resumed._step_observing.assert_not_awaited()
            registry.verify.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
