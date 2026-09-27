import unittest
from unittest.mock import AsyncMock, patch

from app.models.gateway import (
    BudgetLimitError,
    FreeCloudUnavailableError,
    ModelCallError,
    ModelGateway,
)


class TestModelGateway(unittest.IsolatedAsyncioTestCase):
    async def test_zero_budget_rejects_paid_provider_before_request(self):
        gateway = ModelGateway()
        with patch("app.models.gateway.litellm.acompletion", new=AsyncMock()) as completion:
            with self.assertRaises(ModelCallError):
                await gateway.call(
                    "openai/gpt-4o",
                    [{"role": "user", "content": "hello"}],
                    free_only=True,
                )
        completion.assert_not_awaited()

    async def test_zero_budget_rejects_paid_fallback_before_request(self):
        gateway = ModelGateway()
        gateway._settings.provider_retries = 0
        completion = AsyncMock(side_effect=RuntimeError("local provider unavailable"))
        with patch("app.models.gateway.litellm.acompletion", new=completion):
            with self.assertRaises(ModelCallError):
                await gateway.call(
                    "ollama/qwen2.5",
                    [{"role": "user", "content": "hello"}],
                    fallbacks=["openai/gpt-4o"],
                    free_only=True,
                )
        self.assertEqual(completion.await_count, 1)
        self.assertEqual(completion.await_args.kwargs["model"], "ollama/qwen2.5")

    async def test_request_is_blocked_when_maximum_cost_exceeds_remaining_budget(self):
        gateway = ModelGateway()
        with patch("app.models.gateway.litellm.acompletion", new=AsyncMock()) as completion:
            with self.assertRaises(BudgetLimitError):
                await gateway.call(
                    "anthropic/claude-sonnet-4-5",
                    [{"role": "user", "content": "hello"}],
                    max_cost_usd=0.000001,
                )
        completion.assert_not_awaited()

    async def test_zero_budget_rejects_unlisted_groq_model(self):
        gateway = ModelGateway()
        with patch("app.models.gateway.litellm.acompletion", new=AsyncMock()) as completion:
            with self.assertRaisesRegex(ModelCallError, "paid or unverifiable"):
                await gateway.call(
                    "groq/paid-model",
                    [{"role": "user", "content": "hello"}],
                    free_only=True,
                )
        completion.assert_not_awaited()

    async def test_zero_budget_rejects_openrouter_paid_model_id(self):
        gateway = ModelGateway()
        with patch("app.models.gateway.litellm.acompletion", new=AsyncMock()) as completion:
            with self.assertRaisesRegex(ModelCallError, "non-free OpenRouter"):
                await gateway.call(
                    "openrouter/openai/gpt-4o",
                    [{"role": "user", "content": "hello"}],
                    free_only=True,
                )
        completion.assert_not_awaited()

    async def test_gemini_free_tier_model_is_allowed_but_paid_model_is_not(self):
        gateway = ModelGateway()
        gateway._settings.provider_retries = 0
        completion = AsyncMock(side_effect=RuntimeError("free-tier provider unavailable"))
        with patch("app.models.gateway.litellm.acompletion", new=completion):
            with self.assertRaises(FreeCloudUnavailableError):
                await gateway.call(
                    "gemini/gemini-3.5-flash-lite",
                    [{"role": "user", "content": "hello"}],
                    free_only=True,
                )
        self.assertEqual(completion.await_args.kwargs["model"], "gemini/gemini-3.5-flash-lite")

        with patch("app.models.gateway.litellm.acompletion", new=AsyncMock()) as paid_completion:
            with self.assertRaisesRegex(ModelCallError, "paid or unverifiable"):
                await gateway.call(
                    "gemini/gemini-3.1-pro-preview",
                    [{"role": "user", "content": "hello"}],
                    free_only=True,
                )
        paid_completion.assert_not_awaited()

    async def test_all_free_cloud_routes_exhaust_into_distinct_unavailable_error(self):
        gateway = ModelGateway()
        gateway._settings.provider_retries = 0
        completion = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        with patch("app.models.gateway.litellm.acompletion", new=completion):
            with self.assertRaises(FreeCloudUnavailableError):
                await gateway.call(
                    "groq/llama-3.3-70b-versatile",
                    [{"role": "user", "content": "hello"}],
                    fallbacks=["gemini/gemini-3.5-flash-lite", "openrouter/free"],
                    free_only=True,
                )
        self.assertEqual(
            [call.kwargs["model"] for call in completion.await_args_list],
            [
                "groq/llama-3.3-70b-versatile",
                "gemini/gemini-3.5-flash-lite",
                "openrouter/free",
            ],
        )

    async def test_zero_budget_caps_openrouter_to_free_model_prices(self):
        gateway = ModelGateway()
        gateway._settings.provider_retries = 0
        completion = AsyncMock(side_effect=RuntimeError("offline test"))
        with patch("app.models.gateway.litellm.acompletion", new=completion):
            with self.assertRaises(ModelCallError):
                await gateway.call(
                    "openrouter/free",
                    [{"role": "user", "content": "hello"}],
                    free_only=True,
                )

        self.assertEqual(completion.await_count, 1)
        self.assertEqual(
            completion.await_args.kwargs["extra_body"],
            {"provider": {"max_price": {"prompt": 0, "completion": 0, "request": 0}}},
        )


if __name__ == "__main__":
    unittest.main()