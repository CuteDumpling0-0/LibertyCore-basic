import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.schemas import CriterionType, SuccessCriterion, ToolResult
from app.verification.command import _check_expect
from app.verification.command import verify_command, verify_test
from app.verification.files import verify_file


class TestVerification(unittest.IsolatedAsyncioTestCase):
    async def test_command_verifiers_use_shared_command_runner(self):
        result = ToolResult(tool="run_command", success=True, output="2 passed", exit_code=0)
        with patch("app.verification.command.run_command", new=AsyncMock(return_value=result)) as runner:
            criterion = SuccessCriterion(
                id="c-command", type=CriterionType.COMMAND, command="pytest tests -q"
            )
            verified = await verify_command(criterion, ".")
            self.assertTrue(verified.passed)
            self.assertEqual(runner.await_args.args[0]["command"], "pytest tests -q")

            test_criterion = SuccessCriterion(id="c-test", type=CriterionType.TEST)
            verified_test = await verify_test(test_criterion, ".")
            self.assertTrue(verified_test.passed)
            self.assertEqual(runner.await_args.args[0]["command"], "pytest")

    def test_command_check_expect_parsing(self):
        res = _check_expect("exit_code=0", "All tests passed", exit_code=0)
        self.assertTrue(res.passed)

        res_fail = _check_expect("exit_code=0", "Test failed", exit_code=1)
        self.assertFalse(res_fail.passed)

        res_contains = _check_expect("contains:12 passed", "Summary: 12 passed in 0.5s", exit_code=0)
        self.assertTrue(res_contains.passed)

        res_not_contains = _check_expect("not_contains:ERROR", "Log output clean", exit_code=0)
        self.assertTrue(res_not_contains.passed)

    async def test_file_verifier(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = tmp_dir
            file_path = Path(tmp_dir) / "README.md"
            file_path.write_text("# Project Docs\nVersion 1.0", encoding="utf-8")

            crit_exists = SuccessCriterion(
                id="c1",
                type=CriterionType.FILE,
                description="exists:README.md",
            )
            res_exists = await verify_file(crit_exists, workspace)
            self.assertTrue(res_exists.passed)

            crit_contains = SuccessCriterion(
                id="c2",
                type=CriterionType.FILE,
                description="contains:README.md:Version 1.0",
            )
            res_contains = await verify_file(crit_contains, workspace)
            self.assertTrue(res_contains.passed)


if __name__ == "__main__":
    unittest.main()
