"""
app/verification/registry.py — Verifier registry keyed by criterion type.
Each verifier receives a SuccessCriterion and the workspace path and
returns (passed: bool, evidence: str).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from app.schemas import CriterionState, CriterionType, SuccessCriterion


class VerifierResult:
    def __init__(self, passed: bool, evidence: str) -> None:
        self.passed = passed
        self.evidence = evidence
        self.state = CriterionState.PASSED if passed else CriterionState.FAILED


VerifierFn = Callable[[SuccessCriterion, str], Awaitable[VerifierResult]]


class VerifierRegistry:
    def __init__(self) -> None:
        self._verifiers: dict[CriterionType, VerifierFn] = {}

    def register(self, ctype: CriterionType, fn: VerifierFn) -> None:
        self._verifiers[ctype] = fn

    async def verify(self, criterion: SuccessCriterion, workspace: str) -> VerifierResult:
        fn = self._verifiers.get(criterion.type)
        if fn is None:
            return VerifierResult(False, f"No verifier registered for type: {criterion.type}")
        try:
            return await fn(criterion, workspace)
        except Exception as exc:
            return VerifierResult(False, f"Verifier exception: {exc}")

    async def verify_all(
        self, criteria: list[SuccessCriterion], workspace: str
    ) -> list[tuple[SuccessCriterion, VerifierResult]]:
        """Run all mandatory criteria and return results."""
        results = []
        for criterion in criteria:
            if not criterion.mandatory:
                continue
            result = await self.verify(criterion, workspace)
            results.append((criterion, result))
        return results


def build_default_verifier_registry() -> VerifierRegistry:
    from app.verification import api as api_v, command as cmd_v, review as rev_v, files as file_v

    reg = VerifierRegistry()
    reg.register(CriterionType.COMMAND, cmd_v.verify_command)
    reg.register(CriterionType.TEST, cmd_v.verify_test)
    reg.register(CriterionType.FILE, file_v.verify_file)
    reg.register(CriterionType.ARTIFACT, file_v.verify_file)
    reg.register(CriterionType.SECURITY, cmd_v.verify_command)
    reg.register(CriterionType.API, api_v.verify_api)
    reg.register(CriterionType.REVIEW, rev_v.verify_review)
    return reg


_registry: VerifierRegistry | None = None


def get_verifier_registry() -> VerifierRegistry:
    global _registry
    if _registry is None:
        _registry = build_default_verifier_registry()
    return _registry
