import unittest

from app.schemas import (
    Budget,
    CriterionState,
    CriterionType,
    PlanStep,
    ReviewerOutput,
    SuccessCriterion,
    TaskContract,
    TaskStatus,
)


class TestSchemas(unittest.TestCase):
    def test_budget_defaults_and_validation(self):
        b = Budget()
        self.assertEqual(b.max_iterations, 40)
        self.assertEqual(b.max_minutes, 120)
        self.assertEqual(b.max_cost_usd, 25.0)

        # Bounds validation
        with self.assertRaises(Exception):
            Budget(max_iterations=0)

    def test_success_criterion_model(self):
        crit = SuccessCriterion(
            id="test_1",
            type=CriterionType.COMMAND,
            description="Run test suite",
            command="pytest",
            expect="exit_code=0",
        )
        self.assertEqual(crit.state, CriterionState.PENDING)
        self.assertTrue(crit.mandatory)

    def test_reviewer_output_parsing(self):
        json_data = {
            "goal_achieved": False,
            "criteria_status": [
                {"criterion_id": "c1", "state": "passed", "evidence": "pytest: 1 passed"}
            ],
            "gaps": ["Need negative auth test"],
            "risks": ["Token expiration window"],
            "next_best_action": "Write negative auth test",
            "strategy_change_required": False,
        }
        review = ReviewerOutput.model_validate(json_data)
        self.assertFalse(review.goal_achieved)
        self.assertEqual(len(review.criteria_status), 1)
        self.assertEqual(review.gaps, ["Need negative auth test"])


if __name__ == "__main__":
    unittest.main()
