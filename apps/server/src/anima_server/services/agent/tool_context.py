"""Per-turn context for agent tools that need DB/user access.

Tools like note_to_self need to know the current user_id, thread_id, and
have access to a DB session. This module provides a contextvar-based
mechanism to inject that context before each agent turn.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass(slots=True)
class ToolContext:
    db: Session          # soul session
    runtime_db: Session  # runtime session
    user_id: int
    thread_id: int
    memory_modified: bool = False


_current_context: ContextVar[ToolContext | None] = ContextVar("agent_tool_context", default=None)


def set_tool_context(ctx: ToolContext) -> None:
    _current_context.set(ctx)


def clear_tool_context() -> None:
    _current_context.set(None)


def get_tool_context() -> ToolContext:
    ctx = _current_context.get()
    if ctx is None:
        raise RuntimeError(
            "No tool context set — tools requiring DB access cannot run outside an agent turn"
        )
    return ctx
