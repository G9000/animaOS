"""Unit tests for Phase 2 run cancellation — step-boundary and mid-stream."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

import pytest
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.companion import AnimaCompanion
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.rules import TerminalToolRule
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StopReason,
    ToolCall,
    UsageStats,
)
from anima_server.services.agent.state import AgentResult
from anima_server.services.agent.streaming import AgentStreamEvent
from anima_server.services.agent.tools import send_message

# ---- Helpers ----


class _DummyTool:
    """A non-terminal tool so the loop continues after execution."""

    name = "dummy_think"
    description = "Think step"

    async def ainvoke(self, _input: dict[str, Any]) -> str:
        return "ok"


class QueueAdapter(BaseLLMAdapter):
    """Test adapter that returns pre-queued responses."""

    provider = "test"
    model = "test-model"

    def __init__(self, responses: list[StepExecutionResult] | None = None) -> None:
        self._responses = deque(responses or [])

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        if not self._responses:
            raise AssertionError("No queued LLM responses remain.")
        return self._responses.popleft()

    async def stream(self, request: LLMRequest):
        result = await self.invoke(request)
        text = result.assistant_text or ""
        if not text:
            # No text to stream — yield a single event with the final result
            yield type(
                "StreamEvent",
                (),
                {
                    "content_delta": "",
                    "result": result,
                },
            )()
            return
        for i, ch in enumerate(text):
            yield type(
                "StreamEvent",
                (),
                {
                    "content_delta": ch,
                    "result": result if i == len(text) - 1 else None,
                },
            )()
            await asyncio.sleep(0)


_dummy = _DummyTool()
_all_tools: list[Any] = [send_message, _dummy]


def _build_runtime(adapter: QueueAdapter, *, max_steps: int = 4) -> AgentRuntime:
    return AgentRuntime(
        adapter=adapter,
        tools=_all_tools,
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        tool_executor=ToolExecutor(_all_tools),
        max_steps=max_steps,
    )


def _terminal_step(text: str) -> StepExecutionResult:
    return StepExecutionResult(
        assistant_text=text,
        tool_calls=(ToolCall(id="tc-1", name="send_message", arguments={"message": text}),),
        usage=UsageStats(prompt_tokens=10, completion_tokens=5),
    )


def _thinking_step() -> StepExecutionResult:
    """A non-terminal tool-calling step that makes the loop continue.

    Under V3 semantics, non-terminal tools auto-continue the loop
    without needing any special parameter.
    """
    return StepExecutionResult(
        assistant_text="",
        tool_calls=(
            ToolCall(id="tc-think", name="dummy_think", arguments={}),
        ),
        usage=UsageStats(prompt_tokens=10, completion_tokens=5),
    )


# ---- Step-boundary cancellation ----


@pytest.mark.asyncio
async def test_cancel_at_step_boundary() -> None:
    """If cancel_event is set before a step starts, runtime stops immediately."""
    adapter = QueueAdapter([_terminal_step("hello")])
    runtime = _build_runtime(adapter)

    event = asyncio.Event()
    event.set()  # Already cancelled

    result = await runtime.invoke(
        "hi",
        user_id=1,
        history=[],
        cancel_event=event,
    )
    assert isinstance(result, AgentResult)
    assert result.stop_reason == StopReason.CANCELLED.value
    assert result.step_traces == []  # No step ran


@pytest.mark.asyncio
async def test_cancel_between_steps() -> None:
    """Cancel fires after first (non-terminal) step completes, before second step."""
    cancel_event = asyncio.Event()

    # Step 1: non-terminal tool call (loop continues)
    # Step 2: terminal call (should never run because cancel fires)
    adapter = QueueAdapter([_thinking_step(), _terminal_step("should not appear")])
    runtime = _build_runtime(adapter)

    step_count = 0

    async def cancel_after_first_timing(ev: AgentStreamEvent) -> None:
        nonlocal step_count
        if ev.event == "timing":
            step_count += 1
            if step_count == 1:
                cancel_event.set()

    result = await runtime.invoke(
        "hi",
        user_id=1,
        history=[],
        cancel_event=cancel_event,
        event_callback=cancel_after_first_timing,
    )
    assert isinstance(result, AgentResult)
    assert result.stop_reason == StopReason.CANCELLED.value
    # Only one step should have completed
    assert len(result.step_traces) == 1


@pytest.mark.asyncio
async def test_cancel_mid_stream() -> None:
    """If cancel_event fires during streaming, runtime stops."""
    cancel_event = asyncio.Event()

    class SlowStreamAdapter(BaseLLMAdapter):
        provider = "test"
        model = "test-model"

        async def invoke(self, request: LLMRequest) -> StepExecutionResult:
            raise NotImplementedError

        async def stream(self, request: LLMRequest):
            for i in range(10):
                if i == 3:
                    cancel_event.set()
                yield type(
                    "StreamEvent",
                    (),
                    {
                        "content_delta": "x",
                        "result": StepExecutionResult(
                            assistant_text="x" * 10,
                            tool_calls=(),
                            usage=UsageStats(prompt_tokens=10, completion_tokens=5),
                        )
                        if i == 9
                        else None,
                    },
                )()
                await asyncio.sleep(0)

    runtime = AgentRuntime(
        adapter=SlowStreamAdapter(),
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=4,
    )

    events_seen: list[AgentStreamEvent] = []

    async def capture_event(ev: AgentStreamEvent) -> None:
        events_seen.append(ev)

    result = await runtime.invoke(
        "hi",
        user_id=1,
        history=[],
        cancel_event=cancel_event,
        event_callback=capture_event,
    )
    assert isinstance(result, AgentResult)
    assert result.stop_reason == StopReason.CANCELLED.value


@pytest.mark.asyncio
async def test_cancel_response_is_empty() -> None:
    """Cancelled runs should return an empty response string."""
    event = asyncio.Event()
    event.set()

    adapter = QueueAdapter([_terminal_step("hello")])
    runtime = _build_runtime(adapter)
    result = await runtime.invoke("hi", user_id=1, history=[], cancel_event=event)
    assert isinstance(result, AgentResult)
    assert result.response == ""


# ---- Companion cancel-event lifecycle ----


class TestCompanionCancelEvents:
    def test_create_and_set(self) -> None:
        companion = AnimaCompanion.__new__(AnimaCompanion)
        companion._cancel_events = {}

        event = companion.create_cancel_event(42)
        assert not event.is_set()
        assert not companion.is_cancelled(42)

        companion.set_cancel(42)
        assert event.is_set()
        assert companion.is_cancelled(42)

    def test_clear(self) -> None:
        companion = AnimaCompanion.__new__(AnimaCompanion)
        companion._cancel_events = {}

        companion.create_cancel_event(42)
        companion.clear_cancel_event(42)
        assert companion.get_cancel_event(42) is None

    def test_get_nonexistent(self) -> None:
        companion = AnimaCompanion.__new__(AnimaCompanion)
        companion._cancel_events = {}
        assert companion.get_cancel_event(99) is None
        assert not companion.is_cancelled(99)

    def test_idempotent_set(self) -> None:
        """Setting cancel on non-existent run creates a pre-set event for late checks."""
        companion = AnimaCompanion.__new__(AnimaCompanion)
        companion._cancel_events = {}
        companion.set_cancel(99)  # Should not raise
        # After set_cancel on a non-existent run, a pre-set event is created
        # so late checks still see the cancellation
        assert companion.is_cancelled(99)

    def test_create_replaces_existing(self) -> None:
        """Creating a new cancel event replaces any existing one."""
        companion = AnimaCompanion.__new__(AnimaCompanion)
        companion._cancel_events = {}

        ev1 = companion.create_cancel_event(42)
        ev1.set()
        ev2 = companion.create_cancel_event(42)
        assert not ev2.is_set()
        assert ev1 is not ev2


# ---- No cancel_event → normal behaviour ---


@pytest.mark.asyncio
async def test_no_cancel_event_runs_normally() -> None:
    adapter = QueueAdapter([_terminal_step("hello world")])
    runtime = _build_runtime(adapter)

    result = await runtime.invoke("hi", user_id=1, history=[])
    assert isinstance(result, AgentResult)
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert result.response == "hello world"
