from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TERMINAL_TOOL = "terminal_tool"
    MAX_STEPS = "max_steps"
    AWAITING_APPROVAL = "awaiting_approval"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    parse_error: str | None = None
    raw_arguments: str | None = None


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    call_id: str
    name: str
    output: str
    is_error: bool = False
    is_terminal: bool = False


@dataclass(frozen=True, slots=True)
class UsageStats:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LLMRequest:
    messages: Sequence[Any]
    user_id: int
    step_index: int
    max_steps: int
    system_prompt: str
    conversation_turn_count: int | None = None
    available_tools: Sequence[Any] = ()
    force_tool_call: bool = False


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    role: str
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class StepExecutionResult:
    assistant_text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: UsageStats | None = None
    raw_response: Any | None = None


@dataclass(frozen=True, slots=True)
class StepStreamEvent:
    content_delta: str = ""
    result: StepExecutionResult | None = None


@dataclass(frozen=True, slots=True)
class StepTrace:
    step_index: int
    request_messages: tuple[MessageSnapshot, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    force_tool_call: bool = False
    assistant_text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolExecutionResult, ...] = ()
    usage: UsageStats | None = None
