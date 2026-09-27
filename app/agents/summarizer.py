"""
app/agents/summarizer.py — Context compression agent.
Compresses state, verbose tool outputs, and observations into compact bullet points
to keep the prompt token count bounded.
"""

from __future__ import annotations

import structlog

from app.models.gateway import get_gateway
from app.models.prompts import SUMMARIZER_SYSTEM, SUMMARIZER_USER
from app.models.routing import ModelRouter

log = structlog.get_logger(__name__)


async def summarize_observations(
    observations: list[str],
    router: ModelRouter,
) -> tuple[str, float]:
    """
    Summarizes observations into concise factual points.
    Returns (summary_text, cost_usd).
    """
    if not observations:
        return "No observations to summarize.", 0.0

    raw_text = "\n".join(observations)
    # If already short, do not spend money or tokens
    if len(raw_text) < 500:
        return raw_text, 0.0

    gateway = get_gateway()
    model, fallbacks = router.select("summarizer")

    messages = [
        {"role": "system", "content": SUMMARIZER_SYSTEM},
        {"role": "user", "content": SUMMARIZER_USER.format(observations=raw_text[:8000])},
    ]

    try:
        summary, cost = await gateway.call(
            model,
            messages,
            fallbacks=fallbacks,
        role_label="summarizer",
        free_only=router.free_only,
            temperature=0.1,
            max_tokens=1000,
        )
        return summary, cost
    except Exception as exc:
        log.warning("summarizer_fallback_to_truncation", error=str(exc))
        # Fallback to local truncation
        return "\n".join(f"- {obs[:120]}" for obs in observations[-10:]), 0.0
