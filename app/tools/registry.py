"""
app/tools/registry.py — Tool registry: maps tool names to handler functions,
enforces permission classes, and provides a typed dispatch interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.schemas import ToolResult
from app.tools.safety import validate_workspace


ToolHandler = Callable[[dict[str, Any], str], Awaitable[ToolResult]]
SERVER_APPROVAL_REQUIRED_TOOLS = {"edit_file", "run_command", "verify_command"}


@dataclass
class ToolDefinition:
    name: str
    description: str
    permission_class: str  # "read", "write", "network"
    handler: ToolHandler
    timeout_seconds: int = 30
    requires_approval: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, defn: ToolDefinition) -> None:
        self._tools[defn.name] = defn

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    async def dispatch(
        self,
        name: str,
        tool_input: dict[str, Any],
        workspace: str,
        authorized_actions: list[str],
        approval_granted: bool = False,
    ) -> ToolResult:
        try:
            validate_workspace(workspace)
        except PermissionError as exc:
            return ToolResult(tool=name, success=False, output=str(exc))
        defn = self._tools.get(name)
        if defn is None:
            return ToolResult(tool=name, success=False, output=f"Unknown tool: {name}")

        # Permission gate
        if defn.permission_class == "write" and "edit_files" not in authorized_actions:
            return ToolResult(tool=name, success=False, output="Tool requires edit_files permission")
        if defn.permission_class == "network" and "network_read" not in authorized_actions:
            return ToolResult(tool=name, success=False, output="Tool requires network permission")
        if defn.requires_approval and not approval_granted:
            return ToolResult(tool=name, success=False, output=f"Tool '{name}' requires explicit approval")

        try:
            return await defn.handler(tool_input, workspace)
        except Exception as exc:
            return ToolResult(tool=name, success=False, output=f"Tool error: {exc}")


# ─── Build and return the default registry ───────────────────────────────────

def build_default_registry() -> ToolRegistry:
    from app.tools import filesystem, git, shell, web  # lazy import to avoid cycles

    registry = ToolRegistry()

    # Read-only filesystem tools
    registry.register(ToolDefinition(
        name="read_file",
        description="Read contents of a file within the workspace",
        permission_class="read",
        handler=filesystem.read_file,
    ))
    registry.register(ToolDefinition(
        name="search_repo",
        description="Search the repository for a pattern (grep-style)",
        permission_class="read",
        handler=filesystem.search_repo,
    ))
    registry.register(ToolDefinition(
        name="list_dir",
        description="List contents of a directory within the workspace",
        permission_class="read",
        handler=filesystem.list_directory,
    ))

    # Write tools
    registry.register(ToolDefinition(
        name="edit_file",
        description="Apply a unified diff patch or write file contents",
        permission_class="write",
        handler=filesystem.edit_file,
        timeout_seconds=30,
        requires_approval=True,
    ))

    # Git tools (read-only)
    registry.register(ToolDefinition(
        name="git_diff",
        description="Show uncommitted changes as a unified diff",
        permission_class="read",
        handler=git.git_diff,
    ))
    registry.register(ToolDefinition(
        name="git_status",
        description="Show working tree status",
        permission_class="read",
        handler=git.git_status,
    ))

    # Shell / test runner
    registry.register(ToolDefinition(
        name="run_command",
        description="Run an allowlisted command (tests, lint, build) in the workspace",
        permission_class="write",
        handler=shell.run_command,
        timeout_seconds=120,
        requires_approval=True,
    ))

    # Web (read-only)
    registry.register(ToolDefinition(
        name="web_search",
        description="Search the public web for documentation or solutions",
        permission_class="network",
        handler=web.web_search,
        timeout_seconds=15,
    ))

    return registry


_default_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry
