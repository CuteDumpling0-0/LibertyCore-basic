"""
app/tools/shell.py — Sandboxed command runner.
Only commands in ALLOWLIST are permitted. Enforces timeouts and captures output.
"""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas import ToolResult
from app.tools.safety import validate_workspace

# Commands that are explicitly allowed. Anything not matching is rejected.
COMMAND_ALLOWLIST: set[str] = {
    "pytest",
    "python",
    "python3",
    "pip",
    "ruff",
    "mypy",
    "black",
    "isort",
    "flake8",
    "npm",
    "npx",
    "node",
    "jest",
    "cargo",
    "go",
    "make",
    "gradle",
    "mvn",
    "git",
}

# Patterns that are NEVER allowed, even if the base command is whitelisted
DENIED_PATTERNS: list[str] = [
    "rm -rf /",
    ":(){ :|:& };:",  # fork bomb
    "> /dev/sda",
    "dd if=",
    "mkfs",
    "chmod 777 /",
    "curl | bash",
    "wget | sh",
]

MAX_OUTPUT_CHARS = 16_000


async def run_command(tool_input: dict[str, Any], workspace: str) -> ToolResult:
    """
    Run an allowlisted shell command in the workspace directory.
    Captures stdout+stderr, enforces timeout.
    """
    command = tool_input.get("command", "").strip()
    settings = get_settings()
    timeout = min(tool_input.get("timeout_seconds", settings.sandbox_timeout_seconds), 300)  # hard cap 5 min

    if not command:
        return ToolResult(tool="run_command", success=False, output="'command' is required")

    # Parse arguments without invoking a shell. Reject shell control operators so
    # the same command string has the same meaning in host and Docker modes.
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    try:
        arguments = list(lexer)
    except ValueError as exc:
        return ToolResult(tool="run_command", success=False, output=f"Invalid command syntax: {exc}")
    if any(token and all(char in ";&|<>" for char in token) for token in arguments):
        return ToolResult(tool="run_command", success=False, output="Shell control operators are not allowed")

    # Allowlist check
    raw_cmd = arguments[0] if arguments else ""
    base_cmd = Path(raw_cmd).name.lower()
    if base_cmd.endswith(".exe"):
        base_cmd = base_cmd[:-4]
    if base_cmd not in COMMAND_ALLOWLIST:
        return ToolResult(
            tool="run_command",
            success=False,
            output=f"Command '{raw_cmd}' is not in the allowlist. Allowed: {sorted(COMMAND_ALLOWLIST)}",
        )

    # Denied pattern check
    for pattern in DENIED_PATTERNS:
        if pattern in command:
            return ToolResult(
                tool="run_command",
                success=False,
                output=f"Command matches denied pattern: {pattern}",
            )

    try:
        workspace_path = validate_workspace(workspace)
    except PermissionError as exc:
        return ToolResult(tool="run_command", success=False, output=str(exc))

    try:
        t0 = time.monotonic()
        if settings.use_docker_sandbox:
            # A dedicated image is required: do not silently fall back to host execution
            # when isolation was explicitly requested.
            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "256",
                "--memory", "1g", "--cpus", "1.0",
                "-v", f"{workspace_path}:/workspace", "-w", "/workspace",
                settings.docker_sandbox_image, *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(workspace_path),
            )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(
                tool="run_command",
                success=False,
                output=f"Command timed out after {timeout}s",
                exit_code=-1,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        output = stdout.decode("utf-8", errors="replace")
        truncated = False
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
            truncated = True

        return ToolResult(
            tool="run_command",
            success=(proc.returncode == 0),
            output=output,
            exit_code=proc.returncode,
            duration_ms=elapsed_ms,
            truncated=truncated,
        )
    except Exception as exc:
        return ToolResult(tool="run_command", success=False, output=f"Shell error: {exc}")
