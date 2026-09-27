from app.config import Settings
from app.models.routing import ModelRouter, auto_detect_preset
from pydantic import ValidationError
from unittest.mock import patch


def test_automatic_mode_uses_only_the_configured_openai_provider():
    settings = Settings(
        anthropic_api_key="",
        openai_api_key="test-key",
        google_api_key="",
        groq_api_key="",
        openrouter_api_key="",
    )

    assert auto_detect_preset(settings) == "openai_only"


def test_automatic_mode_uses_local_profile_without_credentials():
    settings = Settings(
        anthropic_api_key="",
        openai_api_key="",
        google_api_key="",
        groq_api_key="",
        openrouter_api_key="",
    )

    assert auto_detect_preset(settings) == "free_local"


def test_automatic_mode_detects_groq_and_openrouter():
    assert auto_detect_preset(Settings(
        anthropic_api_key="", openai_api_key="", google_api_key="",
        groq_api_key="test-key", openrouter_api_key="",
    )) == "groq_only"
    assert auto_detect_preset(Settings(
        anthropic_api_key="", openai_api_key="", google_api_key="",
        groq_api_key="", openrouter_api_key="test-key",
    )) == "openrouter_only"


def test_explicit_unknown_profile_falls_back_to_local_profile():
    router = ModelRouter("not-a-profile")

    assert router.active_preset["id"] == "free_local"


def test_free_only_openrouter_uses_automatic_routing_target():
    router = ModelRouter("openrouter_only", free_only=True)

    assert router.select("planner") == ("openrouter/auto", [])


def test_free_cloud_uses_groq_with_only_free_openrouter_fallbacks():
    settings = Settings(
        anthropic_api_key="", openai_api_key="", google_api_key="gemini-test",
        groq_api_key="groq-test", openrouter_api_key="openrouter-test",
    )
    with patch("app.models.routing.get_settings", return_value=settings):
        router = ModelRouter("free_cloud", free_only=True)
        route = router.select("planner")
        router.escalate("planner")
        escalated_route = router.select("planner")

    assert route == (
        "groq/llama-3.3-70b-versatile",
        ["gemini/gemini-3.5-flash-lite", "openrouter/free"],
    )
    assert escalated_route == route


def test_free_cloud_uses_gemini_free_tier_when_it_is_the_only_provider():
    settings = Settings(
        anthropic_api_key="", openai_api_key="", google_api_key="gemini-test",
        groq_api_key="", openrouter_api_key="",
    )
    with patch("app.models.routing.get_settings", return_value=settings):
        router = ModelRouter("free_cloud", free_only=True)

    assert router.select("planner") == ("gemini/gemini-3.5-flash-lite", [])


def test_free_cloud_uses_only_free_openrouter_models_without_groq_key():
    settings = Settings(
        anthropic_api_key="", openai_api_key="", google_api_key="",
        groq_api_key="", openrouter_api_key="openrouter-test",
    )
    with patch("app.models.routing.get_settings", return_value=settings):
        router = ModelRouter("free_cloud", free_only=True)

    model, fallbacks = router.select("reviewer")
    assert model == "openrouter/free"
    assert fallbacks == []
    contract_model, contract_fallbacks = router.select("contract")
    assert contract_model == "openrouter/free"
    assert contract_fallbacks == []


def test_dashboard_exposes_exactly_the_three_model_presets():
    from html.parser import HTMLParser
    from pathlib import Path

    class ModelPresetParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_preset_select = False
            self.values = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "select" and attributes.get("id") == "model-preset":
                self.in_preset_select = True
            elif tag == "option" and self.in_preset_select:
                self.values.append(attributes.get("value"))

        def handle_endtag(self, tag):
            if tag == "select" and self.in_preset_select:
                self.in_preset_select = False

    parser = ModelPresetParser()
    parser.feed((Path(__file__).parents[2] / "app/static/index.html").read_text(encoding="utf-8"))
    assert parser.values == ["auto", "free_cloud", "free_local"]


def test_unauthenticated_service_rejects_non_loopback_binding():
    try:
        Settings(host="0.0.0.0")
    except ValidationError:
        return
    raise AssertionError("Non-loopback binding must be rejected without API authentication")
