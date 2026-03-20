"""Tests for StepProgression, StepFailedError, reasoning capture, and timing."""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

from anima_server.services.agent.adapters.openai_compatible import OpenAICompatibleAdapter
from anima_server.services.agent.openai_compatible_client import OpenAICompatibleStreamChunk
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.rules import TerminalToolRule
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StepFailedError,
    StepProgression,
    StopReason,
    ToolCall,
    UsageStats,
)
from anima_server.services.agent.streaming import AgentStreamEvent
from anima_server.services.agent.tools import send_message


# --------------------------------------------------------------------------- #
# Helpers — test adapter
# --------------------------------------------------------------------------- #


class QueueAdapter(BaseLLMAdapter):
    provider = "test"
    model = "test-model"

    def __init__(self, responses: list[StepExecutionResult]) -> None:
        self._responses = deque(responses)
        self.requests: list[LLMRequest] = []

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("No queued LLM responses remain.")
        return self._responses.popleft()


class FailingAdapter(BaseLLMAdapter):
    """Adapter that always raises at the LLM call stage."""

    provider = "test"
    model = "test-model"

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        raise RuntimeError("LLM timeout")


class TimeoutAdapter(BaseLLMAdapter):
    """Adapter that times out (sleeps forever)."""

    provider = "test"
    model = "test-model"

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        await asyncio.sleep(999)
        return StepExecutionResult()  # never reached


# --------------------------------------------------------------------------- #
# StepProgression ordering
# --------------------------------------------------------------------------- #


def test_step_progression_ordering() -> None:
    assert StepProgression.START < StepProgression.LLM_REQUESTED
    assert StepProgression.LLM_REQUESTED < StepProgression.RESPONSE_RECEIVED
    assert StepProgression.RESPONSE_RECEIVED < StepProgression.TOOLS_STARTED
    assert StepProgression.TOOLS_STARTED < StepProgression.TOOLS_COMPLETED
    assert StepProgression.TOOLS_COMPLETED < StepProgression.PERSISTED
    assert StepProgression.PERSISTED < StepProgression.FINISHED


# --------------------------------------------------------------------------- #
# StepFailedError wrapping
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_llm_failure_raises_step_failed_error() -> None:
    runtime = AgentRuntime(
        adapter=FailingAdapter(),
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )

    with pytest.raises(StepFailedError) as exc_info:
        await runtime.invoke("hello", user_id=1, history=[])

    err = exc_info.value
    assert isinstance(err.cause, RuntimeError)
    assert "LLM timeout" in str(err.cause)
    # Failure happened before RESPONSE_RECEIVED.
    assert err.progression <= StepProgression.LLM_REQUESTED


# --------------------------------------------------------------------------- #
# Reasoning capture
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reasoning_content_passed_through_to_step_trace() -> None:
    adapter = QueueAdapter([
        StepExecutionResult(
            assistant_text="answer",
            reasoning_content="<think>deep thought</think>",
            reasoning_signature="sig-123",
        ),
    ])
    runtime = AgentRuntime(adapter=adapter, max_steps=1)

    result = await runtime.invoke("hello", user_id=1, history=[])

    assert len(result.step_traces) == 1
    trace = result.step_traces[0]
    assert trace.reasoning_content == "<think>deep thought</think>"
    assert trace.reasoning_signature == "sig-123"


@pytest.mark.asyncio
async def test_reasoning_event_emitted_via_callback() -> None:
    adapter = QueueAdapter([
        StepExecutionResult(
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="send_message",
                    arguments={"message": "hi"},
                ),
            ),
            reasoning_content="internal reasoning",
            reasoning_signature="sig-abc",
        ),
    ])
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )

    events: list[AgentStreamEvent] = []

    async def collect(event: AgentStreamEvent) -> None:
        events.append(event)

    await runtime.invoke("hello", user_id=1, history=[], event_callback=collect)

    reasoning_events = [e for e in events if e.event == "reasoning"]
    assert len(reasoning_events) == 1
    assert reasoning_events[0].data["content"] == "internal reasoning"
    assert reasoning_events[0].data["signature"] == "sig-abc"


# --------------------------------------------------------------------------- #
# Timing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_step_timing_populated_in_traces() -> None:
    adapter = QueueAdapter([
        StepExecutionResult(
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="send_message",
                    arguments={"message": "hi"},
                ),
            ),
        )
    ])
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )

    result = await runtime.invoke("hello", user_id=1, history=[])

    assert len(result.step_traces) == 1
    timing = result.step_traces[0].timing
    assert timing is not None
    assert timing.step_duration_ms is not None
    assert timing.step_duration_ms >= 0
    assert timing.llm_duration_ms is not None
    assert timing.llm_duration_ms >= 0


@pytest.mark.asyncio
async def test_timing_event_emitted_via_callback() -> None:
    adapter = QueueAdapter([StepExecutionResult(assistant_text="ok")])
    runtime = AgentRuntime(adapter=adapter, max_steps=1)

    events: list[AgentStreamEvent] = []

    async def collect(event: AgentStreamEvent) -> None:
        events.append(event)

    await runtime.invoke("hello", user_id=1, history=[], event_callback=collect)

    timing_events = [e for e in events if e.event == "timing"]
    assert len(timing_events) == 1
    assert timing_events[0].data["stepIndex"] == 0
    assert timing_events[0].data["stepDurationMs"] >= 0


@pytest.mark.asyncio
async def test_streaming_ttft_uses_first_ollama_chunk_even_when_hidden_by_reasoning() -> None:
    class HiddenReasoningChatClient:
        async def ainvoke(self, input: list[object]) -> object:
            raise AssertionError("streaming path should be used")

        async def astream(self, input: list[object]):
            del input
            yield OpenAICompatibleStreamChunk(content_delta="<think>hidden")
            await asyncio.sleep(0.15)
            yield OpenAICompatibleStreamChunk(
                content_delta=" plan</think>\n\nvisible",
                done=True,
            )

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            del tools, tool_choice, kwargs
            return self

    runtime = AgentRuntime(
        adapter=OpenAICompatibleAdapter(
            HiddenReasoningChatClient(),
            provider="ollama",
            model="test-model",
        ),
        max_steps=1,
    )

    events: list[AgentStreamEvent] = []

    async def collect(event: AgentStreamEvent) -> None:
        events.append(event)

    result = await runtime.invoke("hello", user_id=1, history=[], event_callback=collect)

    assert result.response == "visible"
    timing = result.step_traces[0].timing
    assert timing is not None
    assert timing.ttft_ms is not None
    assert timing.ttft_ms < 100


@pytest.mark.asyncio
async def test_step_state_and_empty_warning_emitted_via_callback() -> None:
    adapter = QueueAdapter([StepExecutionResult()])
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=1,
    )

    events: list[AgentStreamEvent] = []

    async def collect(event: AgentStreamEvent) -> None:
        events.append(event)

    await runtime.invoke("hello", user_id=1, history=[], event_callback=collect)

    assert [event.event for event in events] == [
        "step_state",
        "step_state",
        "warning",
        "timing",
    ]
    assert events[0].data["stepIndex"] == 0
    assert events[0].data["phase"] == "request"
    assert events[0].data["messageCount"] == 2
    assert events[0].data["allowedTools"] == ["send_message"]
    assert events[0].data["forceToolCall"] is True
    assert events[0].data["messages"][0]["role"] == "system"
    assert events[0].data["messages"][0]["chars"] > 0
    assert events[0].data["messages"][1] == {
        "role": "user",
        "chars": 5,
        "preview": "hello",
    }
    assert events[1].data == {
        "stepIndex": 0,
        "phase": "result",
        "assistantTextChars": 0,
        "assistantTextPreview": "",
        "toolCallCount": 0,
        "reasoningChars": 0,
        "reasoningCaptured": False,
    }
    assert events[2].data == {
        "stepIndex": 0,
        "code": "empty_step_result",
        "message": "LLM returned no assistant text and no tool calls for this step.",
    }


# --------------------------------------------------------------------------- #
# Enhanced usage stats
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reasoning_tokens_in_usage() -> None:
    adapter = QueueAdapter([
        StepExecutionResult(
            assistant_text="ok",
            usage=UsageStats(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                reasoning_tokens=3,
                cached_input_tokens=2,
            ),
        ),
    ])
    runtime = AgentRuntime(adapter=adapter, max_steps=1)

    result = await runtime.invoke("hello", user_id=1, history=[])

    assert result.step_traces[0].usage is not None
    assert result.step_traces[0].usage.reasoning_tokens == 3
    assert result.step_traces[0].usage.cached_input_tokens == 2
