"""
app/verification/command.py — Deterministic command and test verifiers.
Runs the configured command and checks exit code and/or output expectations.
"""

from __future__ import annotations

import re

from app.schemas import SuccessCriterion
from app.tools.shell import run_command
from app.verification.registry import VerifierResult


async def verify_command(criterion: SuccessCriterion, workspace: str) -> VerifierResult:
    """Run criterion.command in workspace and check criterion.expect."""
    command = criterion.command
    if not command:
        return VerifierResult(False, "No command specified in criterion")

    result = await run_command({"command": command, "timeout_seconds": 120}, workspace)
    return _check_expect(criterion.expect or "exit_code=0", result.output, result.exit_code if result.exit_code is not None else -1)


async def verify_test(criterion: SuccessCriterion, workspace: str) -> VerifierResult:
    """
    Run tests. Defaults to 'pytest' if no command is given.
    Checks exit code and extracts pass/fail summary.
    """
    command = criterion.command or "pytest"
    return await verify_command(criterion.model_copy(update={"command": command}), workspace)


def _check_expect(expect: str, output: str, exit_code: int) -> VerifierResult:
    """
    Parse the expect string and validate against actual output.
    Supported forms:
      exit_code=0
      contains:some text
      not_contains:some text
      regex:pattern
    Multiple expectations separated by '|'.
    """
    expectations = [e.strip() for e in expect.split("|")]
    failures = []

    for exp in expectations:
        if exp.startswith("exit_code="):
            expected_code = int(exp.split("=", 1)[1])
            if exit_code != expected_code:
                failures.append(f"exit_code expected {expected_code}, got {exit_code}")
        elif exp.startswith("contains:"):
            text = exp[len("contains:"):]
            if text not in output:
                failures.append(f"output missing: '{text}'")
        elif exp.startswith("not_contains:"):
            text = exp[len("not_contains:"):]
            if text in output:
                failures.append(f"output unexpectedly contains: '{text}'")
        elif exp.startswith("regex:"):
            pattern = exp[len("regex:"):]
            if not re.search(pattern, output):
                failures.append(f"output does not match regex: '{pattern}'")

    if failures:
        evidence = f"FAILED: {'; '.join(failures)}\nOutput snippet: {output[:500]}"
        return VerifierResult(False, evidence)

    summary = output.strip().splitlines()[-3:]  # last few lines as evidence
    return VerifierResult(True, f"PASSED. Output: {' | '.join(summary)}")
