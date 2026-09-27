"""
app/config.py — Application configuration loaded from environment variables.
Uses pydantic-settings so all values are validated at startup.
"""

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Service ──────────────────────────────────────────────────────────────
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    cors_allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000"

    @field_validator("host")
    @classmethod
    def require_local_host(cls, value: str) -> str:
        host = value.strip().lower()
        if host == "localhost":
            return value
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("Unauthenticated service must bind to localhost or a loopback IP") from exc
        if not address.is_loopback:
            raise ValueError("Unauthenticated service must bind to localhost or a loopback IP")
        return value

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./goal_agent.db"

    # ── Model credentials (kept in env, never source-controlled) ─────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # ── Model role assignments ────────────────────────────────────────────────
    planner_model: str = "anthropic/claude-sonnet-4-5"
    executor_model: str = "openai/gpt-4o"
    reviewer_model: str = "google/gemini-pro"
    summarizer_model: str = "ollama/qwen2.5"

    # ── Fallbacks (pipe-separated lists) ─────────────────────────────────────
    planner_fallbacks: str = "openai/gpt-4o|google/gemini-pro"
    executor_fallbacks: str = "anthropic/claude-sonnet-4-5"
    reviewer_fallbacks: str = "anthropic/claude-sonnet-4-5|openai/gpt-4o"

    # ── Retry / escalation policies ──────────────────────────────────────────
    provider_retries: int = 2
    max_invalid_json_retries: int = 1
    escalation_after_failed_reviews: int = 2

    # ── Default budgets ───────────────────────────────────────────────────────
    default_max_iterations: int = Field(40, ge=1, le=500)
    default_max_minutes: int = Field(120, ge=1, le=1440)
    default_max_cost_usd: float = Field(25.0, ge=0.0, le=500.0)

    # ── Sandbox / security ───────────────────────────────────────────────────
    allowed_workspace_roots: str = "/workspaces,/home,C:\\Users"
    sandbox_timeout_seconds: int = 60
    use_docker_sandbox: bool = False
    docker_sandbox_image: str = "goal-agent-sandbox:latest"

    @field_validator("allowed_workspace_roots", mode="before")
    @classmethod
    def normalise_roots(cls, v: str) -> str:
        return v  # kept as raw string; parsed in tools/filesystem.py

    def planner_fallback_list(self) -> list[str]:
        return [m.strip() for m in self.planner_fallbacks.split("|") if m.strip()]

    def executor_fallback_list(self) -> list[str]:
        return [m.strip() for m in self.executor_fallbacks.split("|") if m.strip()]

    def reviewer_fallback_list(self) -> list[str]:
        return [m.strip() for m in self.reviewer_fallbacks.split("|") if m.strip()]

    def allowed_roots(self) -> list[Path]:
        return [Path(p.strip()) for p in self.allowed_workspace_roots.split(",") if p.strip()]

    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
