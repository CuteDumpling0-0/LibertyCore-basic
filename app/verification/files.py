"""
app/verification/files.py — File existence, content, and schema verifiers.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.schemas import SuccessCriterion
from app.verification.registry import VerifierResult


async def verify_file(criterion: SuccessCriterion, workspace: str) -> VerifierResult:
    """
    Verify file-based criteria. Description can encode checks:
      exists:<path>
      contains:<path>:<text>
      not_contains:<path>:<text>
      regex:<path>:<pattern>
    """
    desc = criterion.description.strip()
    workspace_path = Path(workspace).resolve()

    try:
        if desc.startswith("exists:"):
            file_path = workspace_path / desc[len("exists:"):]
            if file_path.exists():
                return VerifierResult(True, f"File exists: {file_path.name}")
            return VerifierResult(False, f"File not found: {file_path}")

        elif desc.startswith("contains:"):
            rest = desc[len("contains:"):]
            parts = rest.split(":", 1)
            if len(parts) != 2:
                return VerifierResult(False, "Bad format: contains:<path>:<text>")
            file_path = workspace_path / parts[0].strip()
            search_text = parts[1]
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if search_text in content:
                return VerifierResult(True, f"'{search_text}' found in {file_path.name}")
            return VerifierResult(False, f"'{search_text}' NOT found in {file_path.name}")

        elif desc.startswith("not_contains:"):
            rest = desc[len("not_contains:"):]
            parts = rest.split(":", 1)
            if len(parts) != 2:
                return VerifierResult(False, "Bad format: not_contains:<path>:<text>")
            file_path = workspace_path / parts[0].strip()
            search_text = parts[1]
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if search_text not in content:
                return VerifierResult(True, f"'{search_text}' correctly absent from {file_path.name}")
            return VerifierResult(False, f"'{search_text}' unexpectedly present in {file_path.name}")

        elif desc.startswith("regex:"):
            rest = desc[len("regex:"):]
            parts = rest.split(":", 1)
            if len(parts) != 2:
                return VerifierResult(False, "Bad format: regex:<path>:<pattern>")
            file_path = workspace_path / parts[0].strip()
            pattern = parts[1]
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if re.search(pattern, content):
                return VerifierResult(True, f"Pattern matched in {file_path.name}")
            return VerifierResult(False, f"Pattern not found in {file_path.name}")

        else:
            return VerifierResult(False, f"Unknown file criterion format: {desc}")
    except Exception as exc:
        return VerifierResult(False, f"File verifier error: {exc}")
