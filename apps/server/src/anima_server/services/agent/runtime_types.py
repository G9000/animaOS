from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TERMINAL_TOOL = "terminal_tool"
    MAX_STEPS = "max_steps"
    AWAITING_APPROVAL = "awaiting_approval"
    CANCELLED = "cancelled"
    EMPTY_RESPONSE = "empty_response"
    NO_TERMINAL_TOOL = "no_terminal_tool"


class StepProgression(IntEnum):
    START = 0
    LLM_REQUESTED = 1
    RESPONSE_RECEIVED = 2
    TOOLS_STARTED = 3
    TOOLS_COMPLETED = 4
    PERSISTED = 5
    FINISHED = 6


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
    memory_modified: bool = False
    inner_thinking: str | None = None


@dataclass(frozen=True, slots=True)
class UsageStats:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_input_tokens: int | None = None


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
    reasoning_content: str | None = None
    reasoning_signature: str | None = None
    ttft_ms: float | None = None


@dataclass(frozen=True, slots=True)
class StepStreamEvent:
    content_delta: str = ""
    result: StepExecutionResult | None = None


@dataclass(frozen=True, slots=True)
class StepTiming:
    step_duration_ms: float | None = None
    llm_duration_ms: float | None = None
    ttft_ms: float | None = None


@dataclass(frozen=True, slots=True)
class StepTrace:
    step_index: int
    llm_invoked: bool = True
    request_messages: tuple[MessageSnapshot, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    force_tool_call: bool = False
    assistant_text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolExecutionResult, ...] = ()
    usage: UsageStats | None = None
    timing: StepTiming | None = None
    reasoning_content: str | None = None
    reasoning_signature: str | None = None


@dataclass
class StepContext:
    """Mutable step-level state bundling progression and timing."""

    step_index: int = 0
    progression: StepProgression = StepProgression.START
    start_time: float = 0.0
    ttft_time: float | None = None
    llm_end_time: float | None = None


@dataclass(frozen=True, slots=True)
class DryRunResult:
    """Returned by invoke(dry_run=True) with the full prompt assembly."""

    system_prompt: str
    messages: tuple[Any, ...]
    tool_schemas: tuple[dict[str, Any], ...]
    allowed_tools: tuple[str, ...]
    memory_blocks: tuple[Any, ...]
    estimated_prompt_tokens: int
    prompt_budget: Any | None  # PromptBudgetTrace


class StepFailedError(Exception):
    """Raised by the runtime when a step fails at a known progression stage."""

    def __init__(self, cause: Exception, context: StepContext) -> None:
        self.cause = cause
        self.progression = context.progression
        self.context = context
        super().__init__(str(cause))
