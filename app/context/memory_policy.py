"""
app/context/memory_policy.py — Memory validation, redaction, and provenance policy.
Ensures only verified durable facts are persisted and sensitive tokens/secrets are redacted.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

SECRET_PATTERNS = [
    re.compile(r"(?:api[_-]?key|secret|token|password|auth|bearer)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{8,})['\"]?", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}", re.IGNORECASE),
]


class MemoryRecord(BaseModel):
    id: str
    category: str = Field(..., description="resources | memories | skills")
    fact: str
    provenance_task_id: str
    source_file_or_cmd: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def redact_secrets(text: str) -> str:
    """Replaces tokens, credentials, and api keys with [REDACTED]."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(r"[REDACTED]", redacted)
    return redacted


def validate_fact_for_persistence(fact: str, is_verified: bool) -> bool:
    """
    Enforces the non-negotiable memory write rule:
    Write only verified durable facts, not speculative model thoughts.
    """
    if not is_verified:
        return False
    if not fact or len(fact.strip()) < 5:
        return False
    # Reject speculative language
    speculative_terms = ["maybe", "might be", "i think", "perhaps", "probably", "could be"]
    lower = fact.lower()
    if any(term in lower for term in speculative_terms):
        return False
    return True
