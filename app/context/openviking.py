"""
app/context/openviking.py — OpenViking client interface.
Integrates with OpenViking (https://github.com/volcengine/OpenViking) for
unified memory, knowledge/RAG, and procedures (resources, memories, skills).
Includes resilient local file-backed persistence when standalone OpenViking daemon is optional.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import uuid

import httpx
import structlog

from app.context.memory_policy import MemoryRecord, redact_secrets, validate_fact_for_persistence

log = structlog.get_logger(__name__)


class OpenVikingClient:
    """
    Client for OpenViking context layer.
    Queries and stores across:
    - Resources: repository docs, architecture notes, specifications, API schemas.
    - Memories: confirmed project conventions, user constraints, previous fixes.
    - Skills: reusable procedures (e.g. 'run backend tests', migration steps).
    """

    def __init__(self, endpoint_url: str = "http://localhost:8080", local_cache_dir: str = ".openviking_cache") -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.local_cache_path = Path(local_cache_dir)
        self.local_cache_path.mkdir(parents=True, exist_ok=True)
        self._db_file = self.local_cache_path / "memory_records.json"
        self._local_records: list[dict[str, Any]] = self._load_local_records()

    def _load_local_records(self) -> list[dict[str, Any]]:
        if self._db_file.exists():
            try:
                return json.loads(self._db_file.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save_local_records(self) -> None:
        try:
            self._db_file.write_text(json.dumps(self._local_records, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("failed_to_save_local_memory_records", error=str(exc))

    async def store_record(self, record: MemoryRecord, is_verified: bool = True) -> bool:
        """Stores a memory record after redacting secrets and validating fact verification."""
        if not validate_fact_for_persistence(record.fact, is_verified=is_verified):
            log.warning("memory_record_rejected_unverified", fact=record.fact[:50])
            return False

        safe_fact = redact_secrets(record.fact)
        record_data = record.model_dump(mode="json")
        record_data["fact"] = safe_fact

        # Try remote OpenViking daemon
        stored_remote = False
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.post(f"{self.endpoint_url}/api/v1/memories", json=record_data)
                if res.status_code in (200, 201):
                    stored_remote = True
        except Exception:
            # Fall back silently to local store
            pass

        # Always maintain durable local record
        self._local_records.append(record_data)
        self._save_local_records()
        log.info("memory_record_stored", id=record.id, category=record.category, remote=stored_remote)
        return True

    async def search(self, query: str, category: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        """
        Retrieves compact summaries matching the query from remote OpenViking or local store.
        """
        # Attempt remote query
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                params = {"query": query, "limit": limit}
                if category:
                    params["category"] = category
                res = await client.get(f"{self.endpoint_url}/api/v1/search", params=params)
                if res.status_code == 200:
                    return res.json().get("results", [])
        except Exception:
            pass

        # Fallback keyword matching on local records
        tokens = query.lower().split()
        scored: list[tuple[float, dict[str, Any]]] = []
        for r in self._local_records:
            if category and r.get("category") != category:
                continue
            fact_lower = r.get("fact", "").lower()
            score = sum(1.0 for t in tokens if t in fact_lower)
            if score > 0:
                scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]


_client: OpenVikingClient | None = None


def get_openviking() -> OpenVikingClient:
    global _client
    if _client is None:
        _client = OpenVikingClient()
    return _client
