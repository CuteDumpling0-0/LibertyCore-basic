"""
app/agents/contract.py — Goal-parser agent.
Converts a natural-language goal into a validated task contract.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.models.gateway import BudgetLimitError, FreeCloudUnavailableError, ModelCallError, get_gateway
from app.models.prompts import CONTRACT_SYSTEM, CONTRACT_USER
from app.models.routing import ModelRouter
from app.schemas import Budget, CriterionType, SuccessCriterion, TaskContract, TaskStatus

log = structlog.get_logger(__name__)


async def parse_goal_to_contract(
    goal: str,
    workspace: str,
    constraints: list[str],
    budget: Budget,
    router: ModelRouter,
) -> TaskContract:
    """
    Call the contract-parser model and return a validated TaskContract.
    Raises ValueError if the output is unacceptable (e.g., no checkable criteria).
    """
    gateway = get_gateway()
    model, fallbacks = router.select("contract")

    prompt = CONTRACT_USER.format(
        goal=goal,
        workspace=workspace,
        constraints=", ".join(constraints) if constraints else "none",
    )

    messages = [
        {"role": "system", "content": CONTRACT_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    try:
        import json
        text, cost = await gateway.call(
            model,
            messages,
            fallbacks=fallbacks,
            role_label="contract",
            temperature=0.1,
            free_only=router.free_only,
            max_cost_usd=budget.max_cost_usd,
        )

        # Parse the JSON contract
        import re
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        raw: dict[str, Any] = json.loads(cleaned)

    except (BudgetLimitError, FreeCloudUnavailableError):
        raise
    except ModelCallError as exc:
        log.error("contract_model_failed", error=str(exc))
        raise ValueError(f"Contract model failed: {exc}") from exc
    except Exception as exc:
        log.error("contract_parse_failed", error=str(exc))
        raise ValueError(f"Failed to parse contract JSON: {exc}") from exc

    # Validate: must have at least one criterion
    raw_criteria = raw.get("success_criteria", [])
    if not raw_criteria:
        raise ValueError(
            "Contract has no success criteria. Goal may be ambiguous. "
            f"Clarification needed: {raw.get('clarification_needed', [])}"
        )

    # Build SuccessCriterion objects
    criteria = []
    for c in raw_criteria:
        try:
            criteria.append(
                SuccessCriterion(
                    id=c.get("id", str(uuid.uuid4())[:8]),
                    type=CriterionType(c.get("type", "review")),
                    description=c.get("description", ""),
                    command=c.get("command"),
                    expect=c.get("expect"),
                )
            )
        except Exception:
            log.warning("criterion_parse_skip", raw=c)

    if not criteria:
        raise ValueError("Contract has no valid success criteria after validation.")

    generated_constraints = raw.get("constraints", [])
    if not isinstance(generated_constraints, list):
        generated_constraints = []
    all_constraints = list(dict.fromkeys([
        *constraints,
        *(item for item in generated_constraints if isinstance(item, str) and item.strip()),
    ]))

    goal_id = str(uuid.uuid4())
    contract = TaskContract(
        goal_id=goal_id,
        objective=raw.get("objective", goal),
        deliverables=raw.get("deliverables", []),
        success_criteria=criteria,
        constraints=all_constraints,
        authorized_actions=raw.get("authorized_actions", ["read_files", "edit_files", "run_tests", "git_diff"]),
        approval_required_actions=raw.get("approval_required_actions", ["network_write", "deployment"]),
        budgets=budget,
        status=TaskStatus.CREATED,
    )

    log.info("contract_created", goal_id=goal_id, criteria_count=len(criteria))
    return contract
