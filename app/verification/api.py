"""Deterministic HTTP verifier for controlled API test targets."""

from __future__ import annotations

import re

import httpx

from app.schemas import SuccessCriterion
from app.verification.registry import VerifierResult


async def verify_api(criterion: SuccessCriterion, workspace: str) -> VerifierResult:
    """Verify ``METHOD URL`` descriptions against status/output expectations."""
    match = re.match(r"^(GET|POST|PUT|PATCH|DELETE|HEAD)\s+(https?://\S+)$", criterion.description.strip(), re.I)
    if not match:
        return VerifierResult(False, "API criterion must be 'METHOD http(s)://host/path'")

    method, url = match.groups()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.request(method.upper(), url)
    except httpx.HTTPError as exc:
        return VerifierResult(False, f"API request failed: {exc}")

    expectations = [item.strip() for item in (criterion.expect or "status_code=200").split("|")]
    failures: list[str] = []
    for expectation in expectations:
        if expectation.startswith("status_code="):
            expected = int(expectation.split("=", 1)[1])
            if response.status_code != expected:
                failures.append(f"status_code expected {expected}, got {response.status_code}")
        elif expectation.startswith("contains:"):
            text = expectation[len("contains:"):]
            if text not in response.text:
                failures.append(f"response missing '{text}'")
        elif expectation.startswith("not_contains:"):
            text = expectation[len("not_contains:"):]
            if text in response.text:
                failures.append(f"response unexpectedly contains '{text}'")
        else:
            failures.append(f"unsupported API expectation: {expectation}")

    evidence = f"{method.upper()} {url} -> {response.status_code}; body: {response.text[:500]}"
    prefix = "FAILED: " + "; ".join(failures) + "\n" if failures else "PASSED: "
    return VerifierResult(not failures, prefix + evidence)
