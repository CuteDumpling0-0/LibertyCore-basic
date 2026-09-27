import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.contract import parse_goal_to_contract
from app.schemas import Budget


class TestContractParsing(unittest.IsolatedAsyncioTestCase):
    async def test_user_constraints_are_preserved_alongside_generated_constraints(self):
        gateway = AsyncMock()
        gateway.call.return_value = (
            json.dumps({
                "success_criteria": [{"id": "tests", "type": "test"}],
                "constraints": ["Model-added constraint"],
            }),
            0.0,
        )
        router = SimpleNamespace(select=lambda role: ("test/model", []), free_only=False)

        with patch("app.agents.contract.get_gateway", return_value=gateway):
            contract = await parse_goal_to_contract(
                goal="Implement a bounded example feature",
                workspace=".",
                constraints=["Do not deploy", "Do not deploy"],
                budget=Budget(),
                router=router,
            )

        self.assertEqual(
            contract.constraints,
            ["Do not deploy", "Model-added constraint"],
        )

    async def test_invalid_model_criteria_do_not_create_an_empty_contract(self):
        gateway = AsyncMock()
        gateway.call.return_value = (
            json.dumps({"success_criteria": [{"id": "bad", "type": "not-a-type"}]}),
            0.0,
        )
        router = SimpleNamespace(select=lambda role: ("test/model", []), free_only=False)

        with patch("app.agents.contract.get_gateway", return_value=gateway):
            with self.assertRaisesRegex(ValueError, "no valid success criteria"):
                await parse_goal_to_contract(
                    goal="Implement a bounded example feature",
                    workspace=".",
                    constraints=[],
                    budget=Budget(),
                    router=router,
                )


if __name__ == "__main__":
    unittest.main()