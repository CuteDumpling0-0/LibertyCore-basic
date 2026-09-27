"""
app/tools/web.py — Read-only web search tool using DuckDuckGo Instant Answers API.
No authentication required. URL policy: public documentation only.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.schemas import ToolResult

DDG_URL = "https://api.duckduckgo.com/"


async def web_search(tool_input: dict[str, Any], workspace: str) -> ToolResult:
    """Search the public web for documentation or error solutions."""
    query = tool_input.get("query", "").strip()
    if not query:
        return ToolResult(tool="web_search", success=False, output="'query' is required")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                DDG_URL,
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "goal-agent/0.1 (+local-dev)"},
            )
            resp.raise_for_status()
            data = resp.json()

        lines = []
        if data.get("AbstractText"):
            lines.append(f"Summary: {data['AbstractText']}")
            lines.append(f"Source: {data.get('AbstractURL', '')}")
        for result in data.get("RelatedTopics", [])[:5]:
            if isinstance(result, dict) and result.get("Text"):
                lines.append(f"- {result['Text']}")

        output = "\n".join(lines) if lines else "No results found."
        return ToolResult(tool="web_search", success=True, output=output)
    except Exception as exc:
        return ToolResult(tool="web_search", success=False, output=f"Search failed: {exc}")
