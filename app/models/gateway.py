"""
app/models/gateway.py — Thin async wrapper around LiteLLM.
Normalises requests, applies retries/fallbacks, tracks cost, and
validates structured JSON output via Pydantic.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Type, TypeVar

import litellm
import structlog
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.models.routing import FREE_CLOUD_GEMINI_MODELS, FREE_CLOUD_GROQ_MODELS

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

litellm.drop_params = True  # ignore unsupported params silently


class ModelCallError(Exception):
    pass


class BudgetLimitError(ModelCallError):
    pass


class FreeCloudUnavailableError(ModelCallError):
    pass


class ModelGateway:
    """Single gateway for all model calls across all roles."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._configure_litellm()

    def _configure_litellm(self) -> None:
        s = self._settings
        if s.anthropic_api_key:
            litellm.anthropic_key = s.anthropic_api_key
        if s.openai_api_key:
            litellm.openai_key = s.openai_api_key
        if s.google_api_key:
            litellm.vertex_key = s.google_api_key
            os.environ["GEMINI_API_KEY"] = s.google_api_key
        if s.groq_api_key:
            os.environ["GROQ_API_KEY"] = s.groq_api_key
        if s.openrouter_api_key:
            os.environ["OPENROUTER_API_KEY"] = s.openrouter_api_key

    async def call(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        fallbacks: list[str] | None = None,
        role_label: str = "generic",
        free_only: bool = False,
        max_cost_usd: float | None = None,
    ) -> tuple[str, float]:
        """
        Returns (response_text, cost_usd).
        Retries transient failures with exponential backoff, then tries fallbacks.
        """
        models_to_try = [model] + (fallbacks or [])
        last_exc: Exception | None = None
        retries = self._settings.provider_retries

        for attempt_model in models_to_try:
            extra_body: dict[str, Any] | None = None
            if free_only:
                if attempt_model.startswith("ollama/"):
                    pass
                elif attempt_model.startswith("openrouter/"):
                    if attempt_model not in {"openrouter/auto", "openrouter/free"} and not attempt_model.endswith(":free"):
                        raise ModelCallError(
                            f"Strict $0 budget blocks non-free OpenRouter model: {attempt_model}"
                        )
                    extra_body = {"provider": {"max_price": {"prompt": 0, "completion": 0, "request": 0}}}
                elif attempt_model in FREE_CLOUD_GROQ_MODELS.values():
                    pass
                elif attempt_model in FREE_CLOUD_GEMINI_MODELS.values():
                    pass
                else:
                    raise ModelCallError(
                        f"Strict $0 budget blocks paid or unverifiable provider model: {attempt_model}"
                    )
            if max_cost_usd is not None and not free_only:
                try:
                    prompt_tokens = litellm.token_counter(model=attempt_model, messages=messages)
                    input_cost, output_cost = litellm.cost_per_token(
                        model=attempt_model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=max_tokens,
                    )
                    estimated_cost = (input_cost + output_cost) * 1.2
                except Exception as exc:
                    raise BudgetLimitError(
                        f"Cannot safely estimate maximum cost for model {attempt_model}: {exc}"
                    ) from exc
                if estimated_cost > max_cost_usd:
                    raise BudgetLimitError(
                        f"Model call estimate (${estimated_cost:.6f}) exceeds remaining budget "
                        f"(${max_cost_usd:.6f}); provider request was not sent."
                    )
            for attempt in range(retries + 1):
                try:
                    t0 = time.monotonic()
                    response = await litellm.acompletion(
                        model=attempt_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        extra_body=extra_body,
                    )
                    elapsed = (time.monotonic() - t0) * 1000
                    cost = litellm.completion_cost(completion_response=response) or 0.0
                    content = response.choices[0].message.content or ""
                    log.info(
                        "model_call_ok",
                        role=role_label,
                        model=attempt_model,
                        tokens=response.usage.total_tokens,
                        cost_usd=cost,
                        elapsed_ms=int(elapsed),
                    )
                    return content, cost
                except (litellm.exceptions.RateLimitError, litellm.exceptions.Timeout) as exc:
                    last_exc = exc
                    wait = 2 ** attempt
                    log.warning("model_transient_error", model=attempt_model, attempt=attempt, wait_s=wait, error=str(exc))
                    if attempt < retries:
                        import asyncio
                        await asyncio.sleep(wait)
                except litellm.exceptions.AuthenticationError as exc:
                    log.error("model_auth_error", model=attempt_model, error=str(exc))
                    last_exc = exc
                    break  # don't retry auth errors
                except Exception as exc:
                    last_exc = exc
                    log.error("model_call_error", model=attempt_model, error=str(exc))
                    break

        error_type = FreeCloudUnavailableError if any(
            attempted_model == "openrouter/free"
            or attempted_model in FREE_CLOUD_GROQ_MODELS.values()
            or attempted_model in FREE_CLOUD_GEMINI_MODELS.values()
            for attempted_model in models_to_try
        ) and free_only else ModelCallError
        raise error_type(f"All model attempts failed. Last error: {last_exc}") from last_exc

    async def call_structured(
        self,
        model: str,
        messages: list[dict[str, str]],
        schema: Type[T],
        *,
        fallbacks: list[str] | None = None,
        role_label: str = "generic",
        temperature: float = 0.1,
        free_only: bool = False,
        max_cost_usd: float | None = None,
    ) -> tuple[T, float]:
        """
        Call a model and parse the response as a Pydantic model.
        Retries up to max_invalid_json_retries on parse failures.
        """
        max_json_retries = self._settings.max_invalid_json_retries
        total_cost = 0.0

        for json_attempt in range(max_json_retries + 1):
            text, cost = await self.call(
                model, messages, fallbacks=fallbacks, role_label=role_label, temperature=temperature,
                free_only=free_only, max_cost_usd=(max_cost_usd - total_cost) if max_cost_usd is not None else None,
            )
            total_cost += cost

            try:
                parsed = _extract_and_parse(text, schema)
                return parsed, total_cost
            except (json.JSONDecodeError, ValidationError) as exc:
                log.warning(
                    "structured_output_parse_error",
                    attempt=json_attempt,
                    error=str(exc),
                    raw_snippet=text[:200],
                )
                if json_attempt < max_json_retries:
                    # Add a repair prompt
                    messages = messages + [
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": (
                                f"Your response could not be parsed as valid JSON matching the schema. "
                                f"Error: {exc}. Please respond ONLY with valid JSON, no markdown fences."
                            ),
                        },
                    ]

        raise ModelCallError(f"Could not obtain valid structured output after {max_json_retries + 1} attempts")


def _extract_and_parse(text: str, schema: Type[T]) -> T:
    """Strip markdown fences and parse JSON into a Pydantic model."""
    # Remove ```json ... ``` or ``` ... ``` wrappers
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    data = json.loads(cleaned)
    return schema.model_validate(data)


# Singleton instance
_gateway: ModelGateway | None = None


def get_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway
