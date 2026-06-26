from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anima_server.services.agent.service import (
        approve_or_deny_turn,
        cancel_agent_run,
        dry_run_agent,
        ensure_agent_ready,
        ensure_image_attachments_supported,
        invalidate_agent_runtime_cache,
        list_agent_history,
        normalize_document_only_user_message,
        reset_agent_thread,
        run_agent,
        stream_agent,
        stream_approve_or_deny,
    )

__all__ = [
    "approve_or_deny_turn",
    "cancel_agent_run",
    "dry_run_agent",
    "ensure_agent_ready",
    "ensure_image_attachments_supported",
    "invalidate_agent_runtime_cache",
    "list_agent_history",
    "normalize_document_only_user_message",
    "reset_agent_thread",
    "run_agent",
    "stream_agent",
    "stream_approve_or_deny",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    service_module = import_module("anima_server.services.agent.service")
    value = getattr(service_module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
