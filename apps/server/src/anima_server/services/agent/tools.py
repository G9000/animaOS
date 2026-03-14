"""Agent tools registry.

Add tools here as plain functions decorated with @tool.
The `get_tools()` list is bound to the LLM in the agent graph.
"""

from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.tools import tool


@tool
def current_datetime() -> str:
    """Return the current date and time in ISO-8601 format (UTC)."""
    return datetime.now(timezone.utc).isoformat()


def get_tools() -> list:
    """Return all tools available to the agent."""
    return [current_datetime]
