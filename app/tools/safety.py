"""Shared workspace-boundary checks for every tool and verifier."""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings


def validate_workspace(workspace: str, allowed_roots: list[Path] | None = None) -> Path:
    """Resolve a workspace and reject paths outside the configured allowlist."""
    candidate = Path(workspace).resolve()
    if not candidate.is_dir():
        raise PermissionError(f"Workspace does not exist or is not a directory: {workspace}")

    roots = allowed_roots if allowed_roots is not None else get_settings().allowed_roots()
    # The application directory is always a safe local default for a fresh install.
    roots = [*roots, Path.cwd().resolve()]
    if not any(candidate == root.resolve() or candidate.is_relative_to(root.resolve()) for root in roots):
        allowed = ", ".join(str(root) for root in roots)
        raise PermissionError(f"Workspace is outside allowed roots: {candidate}. Allowed roots: {allowed}")
    return candidate
