import unittest

from app.schemas import Budget, CriterionState, CriterionType, SuccessCriterion
from app.workflow.policies import check_budgets, check_stall, evaluate_completion
from app.workflow.state import WorkflowState


class TestWorkflowPolicies(unittest.TestCase):
    def test_budget_exhaustion_policy(self):
        state = WorkflowState(
            task_id="t1",
            workspace=".",
            budget=Budget(max_iterations=5, max_minutes=60, max_cost_usd=10.0),
        )
        state.iteration = 5
        exceeded, reason = check_budgets(state)
        self.assertTrue(exceeded)
        self.assertIn("Max iteration limit", reason)

    def test_stall_detection_policy(self):
        state = WorkflowState(task_id="t2", workspace=".")
        # Identical gaps 3 times
        state.gap_history = [
            ["gap 1", "gap 2"],
            ["gap 1", "gap 2"],
            ["gap 1", "gap 2"],
        ]
        stalled, reason = check_stall(state)
        self.assertTrue(stalled)
        self.assertIn("identical gaps persisted", reason)

    def test_completion_evaluation_policy(self):
        crit = SuccessCriterion(
            id="c1",
            type=CriterionType.COMMAND,
            description="tests",
            state=CriterionState.PENDING,
        )
        state = WorkflowState(task_id="t3", workspace=".", criteria=[crit])

        # Should not complete while criterion is pending
        complete, _ = evaluate_completion(state)
        self.assertFalse(complete)

        # Mark passed
        crit.state = CriterionState.PASSED
        complete, _ = evaluate_completion(state)
        self.assertTrue(complete)


if __name__ == "__main__":
    unittest.main()
