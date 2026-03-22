"""Integration tests for SSE event sequence including reasoning and timing events."""

from __future__ import annotations

from anima_server.services.agent.runtime_types import (
    MessageSnapshot,
    StepTiming,
    StepTrace,
    ToolCall,
    ToolExecutionResult,
    UsageStats,
)
from anima_server.services.agent.state import AgentResult
from anima_server.services.agent.streaming import (
    build_reasoning_event,
    build_stream_events,
    build_timing_event,
    build_usage_event,
    summarize_usage,
)


# --------------------------------------------------------------------------- #
# Full event sequence with reasoning + timing
# --------------------------------------------------------------------------- #


def test_full_event_sequence_with_reasoning_and_timing() -> None:
    result = AgentResult(
        response="hello",
        model="test-model",
        provider="test-provider",
        stop_reason="terminal_tool",
        tools_used=["send_message"],
        step_traces=[
            StepTrace(
                step_index=0,
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="send_message",
                        arguments={"message": "hello"},
                    ),
                ),
                tool_results=(
                    ToolExecutionResult(
                        call_id="call-1",
                        name="send_message",
                        output="hello",
                        is_terminal=True,
                    ),
                ),
                usage=UsageStats(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    reasoning_tokens=3,
                ),
                timing=StepTiming(
                    step_duration_ms=150.0,
                    llm_duration_ms=120.0,
                    ttft_ms=50.0,
                ),
                reasoning_content="internal thinking",
                reasoning_signature="sig-1",
            ),
        ],
    )

    events = list(build_stream_events(result, chunk_size=10))
    event_types = [e.event for e in events]

    # Reasoning comes before tool events; timing after; usage and done at end.
    assert event_types == [
        "step_state",
        "step_state",
        "reasoning",
        "tool_call",
        "tool_return",
        "timing",
        "chunk",
        "usage",
        "done",
    ]

    # Reasoning event.
    assert events[2].data["content"] == "internal thinking"
    assert events[2].data["signature"] == "sig-1"

    # Timing event.
    assert events[5].data["stepDurationMs"] == 150.0
    assert events[5].data["llmDurationMs"] == 120.0
    assert events[5].data["ttftMs"] == 50.0

    # Usage event includes reasoning tokens.
    assert events[7].data["reasoningTokens"] == 3


# --------------------------------------------------------------------------- #
# No reasoning → no reasoning event emitted
# --------------------------------------------------------------------------- #


def test_stream_events_without_reasoning_or_timing() -> None:
    result = AgentResult(
        response="ok",
        model="m",
        provider="p",
        stop_reason="end_turn",
        step_traces=[
            StepTrace(
                step_index=0,
                assistant_text="ok",
                usage=UsageStats(
                    prompt_tokens=1, completion_tokens=1, total_tokens=2),
            ),
        ],
    )

    events = list(build_stream_events(result, chunk_size=10))
    event_types = [e.event for e in events]

    # No reasoning, no tool events, no timing (timing is None).
    assert event_types == ["step_state", "step_state", "chunk", "usage", "done"]


def test_stream_events_include_step_state_and_empty_warning() -> None:
    result = AgentResult(
        response="",
        model="m",
        provider="p",
        stop_reason="end_turn",
        step_traces=[
            StepTrace(
                step_index=0,
                request_messages=(
                    MessageSnapshot(role="system", content="system prompt"),
                    MessageSnapshot(role="user", content="hello there"),
                ),
                allowed_tools=("current_datetime", "send_message"),
                force_tool_call=True,
                timing=StepTiming(
                    step_duration_ms=100.0,
                    llm_duration_ms=90.0,
                ),
            ),
        ],
    )

    events = list(build_stream_events(result, chunk_size=10))
    event_types = [e.event for e in events]

    assert event_types == [
        "step_state",
        "step_state",
        "warning",
        "timing",
        "done",
    ]

    assert events[0].data == {
        "stepIndex": 0,
        "phase": "request",
        "messageCount": 2,
        "allowedTools": ["current_datetime", "send_message"],
        "forceToolCall": True,
        "messages": [
            {
                "role": "system",
                "chars": 13,
                "preview": "system prompt",
            },
            {
                "role": "user",
                "chars": 11,
                "preview": "hello there",
            },
        ],
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
# build_reasoning_event unit
# --------------------------------------------------------------------------- #


def test_build_reasoning_event_with_signature() -> None:
    event = build_reasoning_event(0, "thinking...", "sig-abc")
    assert event.event == "reasoning"
    assert event.data["content"] == "thinking..."
    assert event.data["signature"] == "sig-abc"


def test_build_reasoning_event_without_signature() -> None:
    event = build_reasoning_event(1, "just thinking")
    assert event.event == "reasoning"
    assert event.data["stepIndex"] == 1
    assert "signature" not in event.data


# --------------------------------------------------------------------------- #
# build_timing_event unit
# --------------------------------------------------------------------------- #


def test_build_timing_event_with_values() -> None:
    timing = StepTiming(step_duration_ms=100.0,
                        llm_duration_ms=80.0, ttft_ms=30.0)
    event = build_timing_event(0, timing)
    assert event.event == "timing"
    assert event.data["stepDurationMs"] == 100.0
    assert event.data["llmDurationMs"] == 80.0
    assert event.data["ttftMs"] == 30.0


def test_build_timing_event_with_none() -> None:
    event = build_timing_event(0, None)
    assert event.event == "timing"
    assert event.data["stepIndex"] == 0


# --------------------------------------------------------------------------- #
# summarize_usage with reasoning and cached tokens
# --------------------------------------------------------------------------- #


def test_summarize_usage_aggregates_reasoning_and_cached_tokens() -> None:
    result = AgentResult(
        response="x",
        model="m",
        provider="p",
        stop_reason="end_turn",
        step_traces=[
            StepTrace(
                step_index=0,
                usage=UsageStats(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    reasoning_tokens=3,
                    cached_input_tokens=2,
                ),
            ),
            StepTrace(
                step_index=1,
                usage=UsageStats(
                    prompt_tokens=8,
                    completion_tokens=4,
                    total_tokens=12,
                    reasoning_tokens=2,
                    cached_input_tokens=1,
                ),
            ),
        ],
    )

    usage = summarize_usage(result)
    assert usage is not None
    assert usage.prompt_tokens == 18
    assert usage.completion_tokens == 9
    assert usage.total_tokens == 27
    assert usage.reasoning_tokens == 5
    assert usage.cached_input_tokens == 3


def test_summarize_usage_omits_reasoning_when_absent() -> None:
    result = AgentResult(
        response="x",
        model="m",
        provider="p",
        stop_reason="end_turn",
        step_traces=[
            StepTrace(
                step_index=0,
                usage=UsageStats(prompt_tokens=10,
                                 completion_tokens=5, total_tokens=15),
            ),
        ],
    )

    usage = summarize_usage(result)
    assert usage is not None
    assert usage.reasoning_tokens is None
    assert usage.cached_input_tokens is None


# --------------------------------------------------------------------------- #
# build_usage_event includes optional fields
# --------------------------------------------------------------------------- #


def test_build_usage_event_with_reasoning_tokens() -> None:
    usage = UsageStats(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        reasoning_tokens=3,
        cached_input_tokens=2,
    )
    event = build_usage_event(usage)
    assert event.data["reasoningTokens"] == 3
    assert event.data["cachedInputTokens"] == 2


def test_build_usage_event_without_optional_tokens() -> None:
    usage = UsageStats(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    event = build_usage_event(usage)
    assert "reasoningTokens" not in event.data
    assert "cachedInputTokens" not in event.data
