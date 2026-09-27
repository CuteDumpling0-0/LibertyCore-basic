"""
app/agents/executor.py — Tool-decision executor agent.
Receives a single plan step, checks authorized permissions, formats tool arguments,
and executes the tool via the ToolRegistry.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from app.models.gateway import get_gateway
from app.models.prompts import EXECUTOR_SYSTEM, EXECUTOR_USER
from app.models.routing import ModelRouter
from app.schemas import PlanStep, ToolResult
from app.tools.registry import get_registry

log = structlog.get_logger(__name__)


async def execute_step(
    step: PlanStep,
    workspace: str,
    authorized_actions: list[str],
    router: ModelRouter,
    approval_granted: bool = False,
    max_cost_usd: float | None = None,
) -> tuple[ToolResult, float]:
    """
    Executes a plan step.
    If the step already defines concrete tool and tool_input, we directly dispatch to the tool registry.
    If parameter extraction/refinement is needed, query the executor model.
    Returns (tool_result, cost_usd).
    """
    registry = get_registry()
    cost_usd = 0.0

    # If the step already has tool and tool_input defined
    tool_name = step.tool
    tool_input = step.tool_input or {}

    if not tool_name or not tool_input:
        gateway = get_gateway()
        model, fallbacks = router.select("executor")
        prompt = EXECUTOR_USER.format(
            authorized_actions=", ".join(authorized_actions),
            step=step.model_dump_json(),
            workspace=workspace,
        )
        messages = [
            {"role": "system", "content": EXECUTOR_SYSTEM},
            {"role": "user", "content": prompt},
        ]

        text, cost = await gateway.call(
            model,
            messages,
            fallbacks=fallbacks,
            role_label="executor",
            temperature=0.0,
            free_only=router.free_only,
            max_cost_usd=max_cost_usd,
        )
        cost_usd += cost

        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip())
            parsed = json.loads(cleaned)
            tool_name = parsed.get("tool", tool_name)
            tool_input = parsed.get("input", tool_input)
        except Exception as exc:
            log.warning("executor_json_parse_fallback", error=str(exc), raw=text[:100])

    log.info("dispatching_tool", tool=tool_name, input_keys=list(tool_input.keys()))
    result = await registry.dispatch(
        name=tool_name,
        tool_input=tool_input,
        workspace=workspace,
        authorized_actions=authorized_actions,
        approval_granted=approval_granted,
    )
    return result, cost_usd
