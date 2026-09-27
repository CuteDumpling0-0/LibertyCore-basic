"""
app/tools/git.py — Read-only Git inspection tools (diff, status).
Write operations (commit, push) require explicit approval and are not enabled by default.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from app.schemas import ToolResult

MAX_DIFF_CHARS = 32_000


async def _run_git(args: list[str], cwd: str, timeout: int = 30) -> tuple[str, int]:
    cmd = ["git"] + args
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return f"git {' '.join(args)} timed out", -1
    return stdout.decode("utf-8", errors="replace"), proc.returncode or 0


async def git_diff(tool_input: dict[str, Any], workspace: str) -> ToolResult:
    """Show uncommitted changes as a unified diff."""
    staged = tool_input.get("staged", False)
    path_filter = tool_input.get("path", None)

    args = ["diff"]
    if staged:
        args.append("--cached")
    if path_filter:
        args += ["--", path_filter]

    output, code = await _run_git(args, workspace)
    if not output.strip():
        output = "(no changes)"

    truncated = False
    if len(output) > MAX_DIFF_CHARS:
        output = output[:MAX_DIFF_CHARS] + "\n...[diff truncated]"
        truncated = True

    return ToolResult(
        tool="git_diff",
        success=(code == 0),
        output=output,
        exit_code=code,
        truncated=truncated,
    )


async def git_status(tool_input: dict[str, Any], workspace: str) -> ToolResult:
    """Show working tree status."""
    output, code = await _run_git(["status", "--short", "--branch"], workspace)
    return ToolResult(tool="git_status", success=(code == 0), output=output, exit_code=code)
