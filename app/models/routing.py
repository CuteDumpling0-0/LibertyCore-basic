"""
app/models/routing.py — Role-based model selection, presets, auto-detection, and escalation.
The controller calls select_model(role) rather than hardcoding any vendor string.
"""

from __future__ import annotations

from typing import Any
import structlog

from app.config import Settings, get_settings

log = structlog.get_logger(__name__)

FREE_CLOUD_GROQ_MODELS = {
    "planner": "groq/llama-3.3-70b-versatile",
    "contract": "groq/llama-3.3-70b-versatile",
    "executor": "groq/llama-3.3-70b-versatile",
    "reviewer": "groq/llama-3.3-70b-versatile",
    "summarizer": "groq/llama-3.1-8b-instant",
}

FREE_CLOUD_GEMINI_MODELS = {
    "planner": "gemini/gemini-3.5-flash-lite",
    "contract": "gemini/gemini-3.5-flash-lite",
    "executor": "gemini/gemini-3.5-flash-lite",
    "reviewer": "gemini/gemini-3.5-flash-lite",
    "summarizer": "gemini/gemini-3.5-flash-lite",
}

FREE_CLOUD_OPENROUTER_MODELS = {
    "planner": "openrouter/free",
    "contract": "openrouter/free",
    "executor": "openrouter/free",
    "reviewer": "openrouter/free",
    "summarizer": "openrouter/free",
}

# Curated presets so users don't have to guess or manually research models
PRESETS: dict[str, dict[str, Any]] = {
    "auto": {
        "id": "auto",
        "name": "⚡ Auto-Detect Best Match (Recommended)",
        "tagline": "Automatically selects the best model profile based on your environment keys",
        "cost_estimate": "Depends on keys",
        "requires_keys": False,
        "explanation": "If API keys exist, uses optimal models. If no keys exist, automatically runs 100% free via Ollama.",
    },
    "free_local": {
        "id": "free_local",
        "name": "🆓 100% Free Local (Ollama)",
        "tagline": "Runs entirely offline with open-weight models and zero API cost",
        "cost_estimate": "$0.00 (Free)",
        "requires_keys": False,
        "roles": {
            "planner": {"model": "ollama/qwen2.5-coder", "why": "High-performing open coding model for planning contracts"},
            "executor": {"model": "ollama/qwen2.5-coder", "why": "Specialized in precise code patches and tool execution"},
            "reviewer": {"model": "ollama/llama3.1", "why": "Independent open model providing unbiased verification review"},
            "summarizer": {"model": "ollama/qwen2.5", "why": "Fast lightweight compression of event history"},
        },
        "escalation": "ollama/qwen2.5-coder:14b",
    },
    "free_cloud": {
        "id": "free_cloud",
        "name": "Free Cloud (Groq / Gemini / OpenRouter)",
        "tagline": "Uses only free-tier Groq and Gemini models or OpenRouter free routes",
        "cost_estimate": "$0.00 (Free Tiers)",
        "requires_keys": True,
        "roles": {},
        "escalation": "groq/llama-3.3-70b-versatile",
    },
    "balanced": {
        "id": "balanced",
        "name": "⚖️ Balanced & Cost-Effective",
        "tagline": "Near-frontier intelligence for a few cents per goal",
        "cost_estimate": "~$0.02 - $0.15",
        "requires_keys": True,
        "roles": {
            "planner": {"model": "openai/gpt-4o-mini", "why": "Strong logical planning at micro-pricing"},
            "executor": {"model": "openai/gpt-4o-mini", "why": "Accurate JSON tool calling and refactoring"},
            "reviewer": {"model": "gemini/gemini-1.5-flash", "why": "Multi-vendor oversight to eliminate vendor bias"},
            "summarizer": {"model": "openai/gpt-4o-mini", "why": "Fast timeline compression"},
        },
        "escalation": "openai/gpt-4o",
    },
    "frontier": {
        "id": "frontier",
        "name": "🚀 Frontier Maximum Intelligence",
        "tagline": "Maximum capability for deep architectural fixes and difficult tasks",
        "cost_estimate": "~$0.50 - $3.00",
        "requires_keys": True,
        "roles": {
            "planner": {"model": "anthropic/claude-sonnet-4-5", "why": "World-class architectural planning and constraints"},
            "executor": {"model": "openai/gpt-4o", "why": "Top-tier code synthesis and tool execution"},
            "reviewer": {"model": "google/gemini-pro", "why": "Frontier cross-model oversight"},
            "summarizer": {"model": "openai/gpt-4o-mini", "why": "Cost-effective history summarizer"},
        },
        "escalation": "anthropic/claude-opus-4-5",
    },
    # These provider-specific profiles are selected only by automatic mode.
    # They ensure that a user who has supplied just one API key can still run
    # the whole workflow without learning or configuring a model matrix.
    "openai_only": {
        "id": "openai_only",
        "name": "Automatic: ready to run",
        "tagline": "Uses your configured provider with a cost-conscious profile",
        "cost_estimate": "Usage-based",
        "requires_keys": True,
        "roles": {
            "planner": {"model": "openai/gpt-4o-mini", "why": "Automatic selection"},
            "executor": {"model": "openai/gpt-4o-mini", "why": "Automatic selection"},
            "reviewer": {"model": "openai/gpt-4o-mini", "why": "Automatic selection"},
            "summarizer": {"model": "openai/gpt-4o-mini", "why": "Automatic selection"},
        },
        "escalation": "openai/gpt-4o",
    },
    "anthropic_only": {
        "id": "anthropic_only",
        "name": "Automatic: ready to run",
        "tagline": "Uses your configured provider with a quality-focused profile",
        "cost_estimate": "Usage-based",
        "requires_keys": True,
        "roles": {
            "planner": {"model": "anthropic/claude-sonnet-4-5", "why": "Automatic selection"},
            "executor": {"model": "anthropic/claude-sonnet-4-5", "why": "Automatic selection"},
            "reviewer": {"model": "anthropic/claude-sonnet-4-5", "why": "Automatic selection"},
            "summarizer": {"model": "anthropic/claude-sonnet-4-5", "why": "Automatic selection"},
        },
        "escalation": "anthropic/claude-opus-4-5",
    },
    "google_only": {
        "id": "google_only",
        "name": "Automatic: ready to run",
        "tagline": "Uses your configured provider with a cost-conscious profile",
        "cost_estimate": "Usage-based",
        "requires_keys": True,
        "roles": {
            "planner": {"model": "google/gemini-pro", "why": "Automatic selection"},
            "executor": {"model": "google/gemini-pro", "why": "Automatic selection"},
            "reviewer": {"model": "google/gemini-pro", "why": "Automatic selection"},
            "summarizer": {"model": "google/gemini-pro", "why": "Automatic selection"},
        },
        "escalation": "google/gemini-pro",
    },
    "groq_only": {
        "id": "groq_only",
        "name": "Automatic: ready to run",
        "tagline": "Uses your configured provider with a fast, cost-conscious profile",
        "cost_estimate": "Usage-based",
        "requires_keys": True,
        "roles": {
            "planner": {"model": "groq/llama-3.3-70b-versatile", "why": "Automatic selection"},
            "executor": {"model": "groq/llama-3.3-70b-versatile", "why": "Automatic selection"},
            "reviewer": {"model": "groq/llama-3.3-70b-versatile", "why": "Automatic selection"},
            "summarizer": {"model": "groq/llama-3.1-8b-instant", "why": "Automatic selection"},
        },
        "escalation": "groq/llama-3.3-70b-versatile",
    },
    "openrouter_only": {
        "id": "openrouter_only",
        "name": "Automatic: ready to run",
        "tagline": "Uses your configured provider with a cost-conscious profile",
        "cost_estimate": "Usage-based",
        "requires_keys": True,
        "roles": {
            "planner": {"model": "openrouter/openai/gpt-4o-mini", "why": "Automatic selection"},
            "executor": {"model": "openrouter/openai/gpt-4o-mini", "why": "Automatic selection"},
            "reviewer": {"model": "openrouter/openai/gpt-4o-mini", "why": "Automatic selection"},
            "summarizer": {"model": "openrouter/openai/gpt-4o-mini", "why": "Automatic selection"},
        },
        "escalation": "openrouter/openai/gpt-4o",
    },
}


def auto_detect_preset(settings: Settings) -> str:
    """Choose a runnable profile from the credentials actually configured."""
    if settings.anthropic_api_key and settings.openai_api_key and settings.google_api_key:
        return "frontier"
    if settings.openai_api_key:
        return "openai_only"
    if settings.anthropic_api_key:
        return "anthropic_only"
    if settings.google_api_key:
        return "google_only"
    if settings.groq_api_key:
        return "groq_only"
    if settings.openrouter_api_key:
        return "openrouter_only"
    return "free_local"


class ModelRouter:
    """Maps logical roles to models and tracks escalation state."""

    def __init__(self, preset: str = "auto", free_only: bool = False) -> None:
        self._s = get_settings()
        self._preset_id = preset
        self.free_only = free_only
        self._escalated: set[str] = set()

        resolved = auto_detect_preset(self._s) if preset == "auto" else preset
        self.active_preset = PRESETS.get(resolved, PRESETS["free_local"])
        self.escalation_model = self.active_preset.get("escalation", "anthropic/claude-opus-4-5")

    def select(self, role: str) -> tuple[str, list[str]]:
        """
        Returns (primary_model, [fallback_models]).
        If this role has been escalated, return the escalation model.
        """
        if role in self._escalated and not self.free_only:
            log.info("model_escalated", role=role, model=self.escalation_model)
            return self.escalation_model, []

        if self._preset_id == "free_cloud":
            routes = []
            if self._s.groq_api_key:
                routes.append(FREE_CLOUD_GROQ_MODELS[role])
            if self._s.google_api_key:
                routes.append(FREE_CLOUD_GEMINI_MODELS[role])
            if self._s.openrouter_api_key:
                routes.append(FREE_CLOUD_OPENROUTER_MODELS[role])
            return (routes[0], routes[1:]) if routes else (FREE_CLOUD_OPENROUTER_MODELS[role], [])

        if self.free_only and self.active_preset["id"] == "openrouter_only":
            # OpenRouter chooses among currently available free endpoints; the
            # gateway also sends a server-enforced zero-price cap.
            return "openrouter/auto", []

        roles_cfg = self.active_preset.get("roles")
        if roles_cfg and role in roles_cfg:
            primary = roles_cfg[role]["model"]
            return primary, []

        # Fallback to .env configuration if role not in preset
        mapping = {
            "planner": (self._s.planner_model, self._s.planner_fallback_list()),
            "executor": (self._s.executor_model, self._s.executor_fallback_list()),
            "reviewer": (self._s.reviewer_model, self._s.reviewer_fallback_list()),
            "summarizer": (self._s.summarizer_model, []),
            "contract": (self._s.planner_model, self._s.planner_fallback_list()),
        }
        return mapping.get(role, (self._s.planner_model, []))

    def escalate(self, role: str) -> None:
        """Force role to use the strongest available model next time."""
        log.warning("escalating_model", role=role)
        self._escalated.add(role)

    def reset_escalation(self, role: str) -> None:
        self._escalated.discard(role)


def make_router(preset: str = "auto", free_only: bool = False) -> ModelRouter:
    return ModelRouter(preset=preset, free_only=free_only)
