"""Gateway-runtime contract and typed invocation models.

These types keep the API ingress (gateway-like request context + normalized
payloads) separated from cognition internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from anima_server.schemas.chat import ChatContextMessage, ChatRequestAttachment, TodayContext


class RuntimeContractEvent(StrEnum):
    """Normalized event stream types shared by gateway and runtime paths."""

    CHUNK = "chunk"
    RUN_STARTED = "run_started"
    THOUGHT = "thought"
    DONE = "done"
    ERROR = "error"
    TOOL_CALL = "tool_call"
    TOOL_RETURN = "tool_return"
    USAGE = "usage"
    MEMORY_STATE = "memory_state"
    WARNING = "warning"
    STEP_STATE = "step_state"
    REASONING = "reasoning"


@dataclass(frozen=True, slots=True)
class RuntimeRequestContext:
    """Request-scoped contract context passed from gateway into runtime services."""

    user_id: int
    trace_id: str | None
    request_id: str | None
    session_id: str | None = None
    device_id: str | None = None
    path: str | None = None
    source: str = "desktop"


@dataclass(frozen=True, slots=True)
class RuntimeChatInput:
    """Normalized chat input contract for runtime execution."""

    request: RuntimeRequestContext
    user_id: int
    user_message: str
    source: str | None = None
    thread_id: int | None = None
    attachments: tuple[ChatRequestAttachment, ...] = ()
    document_ids: tuple[int, ...] = ()
    context_messages: tuple[ChatContextMessage, ...] = ()
    today_context: TodayContext | None = None


@dataclass(frozen=True, slots=True)
class RuntimeChatOutput:
    response: str
    model: str
    provider: str
    tools_used: list[str]
    retrieval: dict[str, Any] | None
    usage: dict[str, int | None] | None


@dataclass(frozen=True, slots=True)
class RuntimeStreamEvent:
    event: str
    data: dict[str, object]
