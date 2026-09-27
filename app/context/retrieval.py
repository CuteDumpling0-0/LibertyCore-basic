"""
app/context/retrieval.py — Context retrieval and reranking for the agent loop.
Pulls relevant resources, memories, and skills according to the current task state,
failing criteria, recent errors, and active plan.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.context.openviking import get_openviking

log = structlog.get_logger(__name__)


async def retrieve_context_for_iteration(
    objective: str,
    failing_criteria: list[str],
    recent_errors: list[str],
    active_files: list[str],
    limit: int = 5,
) -> list[str]:
    """
    Formulates a targeted search query and retrieves relevant context items.
    Reranks items prioritizing errors and failing criteria first.
    """
    ov = get_openviking()
    query_parts = [objective]
    if failing_criteria:
        query_parts.extend(failing_criteria)
    if recent_errors:
        query_parts.extend(recent_errors[-2:])
    if active_files:
        query_parts.extend(active_files)

    query = " ".join(query_parts)
    raw_results = await ov.search(query=query, limit=limit * 2)

    # Reranking: rank higher if matching failing criteria or error terms
    def rank_score(item: dict[str, Any]) -> float:
        score = 0.0
        fact = item.get("fact", "").lower()
        for fc in failing_criteria:
            if fc.lower() in fact:
                score += 3.0
        for err in recent_errors:
            if any(term in fact for term in err.lower().split()[:3]):
                score += 2.0
        for f in active_files:
            if f.lower() in fact:
                score += 1.5
        score += item.get("confidence", 1.0)
        return score

    sorted_results = sorted(raw_results, key=rank_score, reverse=True)
    summaries = [f"[{r.get('category', 'context')}] {r.get('fact', '')}" for r in sorted_results[:limit]]
    log.info("context_retrieved", count=len(summaries))
    return summaries
