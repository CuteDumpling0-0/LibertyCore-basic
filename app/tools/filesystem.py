"""
app/tools/filesystem.py — File read, write (patch application), and search tools.
All operations are confined to the allowed workspace root (path-traversal rejected).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas import ToolResult

MAX_FILE_SIZE = 512 * 1024  # 512 KB read limit
MAX_OUTPUT_CHARS = 8000


def _resolve_safe(path_str: str, workspace: str) -> Path:
    """Resolve path and confirm it stays inside the workspace."""
    workspace_path = Path(workspace).resolve()
    target = (workspace_path / path_str).resolve()
    if not str(target).startswith(str(workspace_path)):
        raise PermissionError(f"Path traversal rejected: {path_str}")
    return target


async def read_file(tool_input: dict[str, Any], workspace: str) -> ToolResult:
    """Read a file within the workspace."""
    path_str = tool_input.get("path", "")
    start_line = tool_input.get("start_line", 1)
    end_line = tool_input.get("end_line", None)

    try:
        target = _resolve_safe(path_str, workspace)
        if not target.exists():
            return ToolResult(tool="read_file", success=False, output=f"File not found: {path_str}")
        if target.stat().st_size > MAX_FILE_SIZE:
            return ToolResult(tool="read_file", success=False, output=f"File too large (>{MAX_FILE_SIZE} bytes): {path_str}")

        lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        start = max(0, start_line - 1)
        end = end_line if end_line else len(lines)
        selected = lines[start:end]
        content = "".join(selected)

        truncated = False
        if len(content) > MAX_OUTPUT_CHARS:
            content = content[:MAX_OUTPUT_CHARS]
            truncated = True

        return ToolResult(
            tool="read_file",
            success=True,
            output=content,
            truncated=truncated,
        )
    except PermissionError as exc:
        return ToolResult(tool="read_file", success=False, output=str(exc))
    except Exception as exc:
        return ToolResult(tool="read_file", success=False, output=f"Error reading file: {exc}")


async def list_directory(tool_input: dict[str, Any], workspace: str) -> ToolResult:
    """List directory contents."""
    path_str = tool_input.get("path", ".")
    try:
        target = _resolve_safe(path_str, workspace)
        if not target.is_dir():
            return ToolResult(tool="list_dir", success=False, output=f"Not a directory: {path_str}")

        entries = []
        for child in sorted(target.iterdir()):
            kind = "dir" if child.is_dir() else "file"
            size = child.stat().st_size if child.is_file() else 0
            entries.append(f"{kind:4}  {child.name}  ({size} bytes)" if kind == "file" else f"{kind}  {child.name}/")

        return ToolResult(tool="list_dir", success=True, output="\n".join(entries[:200]))
    except PermissionError as exc:
        return ToolResult(tool="list_dir", success=False, output=str(exc))
    except Exception as exc:
        return ToolResult(tool="list_dir", success=False, output=f"Error: {exc}")


async def search_repo(tool_input: dict[str, Any], workspace: str) -> ToolResult:
    """Grep-style search within the workspace."""
    pattern = tool_input.get("pattern", "")
    include_glob = tool_input.get("include", "*.py")
    max_results = min(tool_input.get("max_results", 50), 200)

    if not pattern:
        return ToolResult(tool="search_repo", success=False, output="'pattern' is required")

    try:
        workspace_path = Path(workspace).resolve()
        results = []
        files = list(workspace_path.rglob(include_glob))

        for filepath in files:
            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
                for lineno, line in enumerate(text.splitlines(), 1):
                    if re.search(pattern, line):
                        rel = filepath.relative_to(workspace_path)
                        results.append(f"{rel}:{lineno}: {line.rstrip()}")
                        if len(results) >= max_results:
                            break
            except Exception:
                pass
            if len(results) >= max_results:
                break

        if not results:
            return ToolResult(tool="search_repo", success=True, output="No matches found.")
        return ToolResult(tool="search_repo", success=True, output="\n".join(results))
    except Exception as exc:
        return ToolResult(tool="search_repo", success=False, output=f"Search error: {exc}")


async def edit_file(tool_input: dict[str, Any], workspace: str) -> ToolResult:
    """
    Apply changes to a file. Supports two modes:
    - mode='write': overwrite file with 'content'
    - mode='patch': apply a unified diff 'patch' using Python's difflib
    """
    mode = tool_input.get("mode", "write")
    path_str = tool_input.get("path", "")

    if not path_str:
        return ToolResult(tool="edit_file", success=False, output="'path' is required")

    try:
        target = _resolve_safe(path_str, workspace)

        if mode == "write":
            content = tool_input.get("content", "")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return ToolResult(
                tool="edit_file",
                success=True,
                output=f"Written {len(content)} chars to {path_str}",
            )

        elif mode == "patch":
            patch = tool_input.get("patch", "")
            if not patch:
                return ToolResult(tool="edit_file", success=False, output="'patch' is required for patch mode")
            result = _apply_patch(target, patch)
            return result

        else:
            return ToolResult(tool="edit_file", success=False, output=f"Unknown mode: {mode}")

    except PermissionError as exc:
        return ToolResult(tool="edit_file", success=False, output=str(exc))
    except Exception as exc:
        return ToolResult(tool="edit_file", success=False, output=f"Edit error: {exc}")


def _apply_patch(target: Path, patch: str) -> ToolResult:
    """Apply a simple unified diff patch to a file."""
    try:
        original = target.read_text(encoding="utf-8") if target.exists() else ""
        original_lines = original.splitlines(keepends=True)

        # Parse unified diff hunks (simplified)
        patched = _apply_unified_diff(original_lines, patch)
        target.write_text("".join(patched), encoding="utf-8")
        return ToolResult(tool="edit_file", success=True, output=f"Patch applied to {target.name}")
    except Exception as exc:
        return ToolResult(tool="edit_file", success=False, output=f"Patch failed: {exc}")


def _apply_unified_diff(original_lines: list[str], patch: str) -> list[str]:
    """Very basic unified diff application (for simple hunks)."""
    patch_lines = patch.splitlines(keepends=True)
    result = list(original_lines)
    offset = 0

    i = 0
    while i < len(patch_lines):
        line = patch_lines[i]
        if line.startswith("@@"):
            # Parse @@ -start,count +start,count @@
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                old_start = int(m.group(1)) - 1  # 0-indexed
                hunk_lines = []
                i += 1
                while i < len(patch_lines) and not patch_lines[i].startswith("@@"):
                    hunk_lines.append(patch_lines[i])
                    i += 1

                old_chunk = [l[1:] for l in hunk_lines if l.startswith("-") or l.startswith(" ")]
                new_chunk = [l[1:] for l in hunk_lines if l.startswith("+") or l.startswith(" ")]

                actual_start = old_start + offset
                result[actual_start: actual_start + len(old_chunk)] = new_chunk
                offset += len(new_chunk) - len(old_chunk)
            else:
                i += 1
        else:
            i += 1

    return result
