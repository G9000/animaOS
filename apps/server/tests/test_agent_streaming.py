from __future__ import annotations

from anima_server.services.agent.runtime_types import (
    StepTrace,
    ToolCall,
    ToolExecutionResult,
    UsageStats,
)
from anima_server.services.agent.state import (
    AgentCitation,
    AgentContextFragment,
    AgentResult,
    AgentRetrievalStats,
    AgentRetrievalTrace,
)
from anima_server.services.agent.streaming import build_stream_events


def test_build_stream_events_emits_tool_events_chunks_usage_and_done() -> None:
    result = AgentResult(
        response="hello world",
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
                        arguments={"message": "hello world"},
                    ),
                ),
                tool_results=(
                    ToolExecutionResult(
                        call_id="call-1",
                        name="send_message",
                        output="hello world",
                        is_terminal=True,
                    ),
                ),
                usage=UsageStats(prompt_tokens=4, completion_tokens=6, total_tokens=10),
            )
        ],
    )

    events = list(build_stream_events(result, chunk_size=5))

    assert [event.event for event in events] == [
        "step_state",
        "step_state",
        "tool_call",
        "tool_return",
        "chunk",
        "chunk",
        "chunk",
        "usage",
        "done",
    ]
    assert events[2].data == {
        "stepIndex": 0,
        "id": "call-1",
        "name": "send_message",
        "arguments": {"message": "hello world"},
    }
    assert events[3].data == {
        "stepIndex": 0,
        "callId": "call-1",
        "name": "send_message",
        "output": "hello world",
        "isError": False,
        "toolSucceeded": True,
        "isTerminal": True,
    }
    assert [event.data["content"] for event in events if event.event == "chunk"] == [
        "hello",
        " worl",
        "d",
    ]
    assert events[-2].data == {
        "promptTokens": 4,
        "completionTokens": 6,
        "totalTokens": 10,
    }
    assert events[-1].data == {
        "status": "complete",
        "stopReason": "terminal_tool",
        "provider": "test-provider",
        "model": "test-model",
        "toolsUsed": ["send_message"],
    }


def test_build_stream_events_include_tool_parse_error_metadata() -> None:
    result = AgentResult(
        response="",
        model="test-model",
        provider="test-provider",
        stop_reason="end_turn",
        step_traces=[
            StepTrace(
                step_index=0,
                tool_calls=(
                    ToolCall(
                        id="call-bad",
                        name="send_message",
                        arguments={},
                        parse_error="Malformed tool-call arguments (invalid JSON).",
                        raw_arguments="{broken json",
                    ),
                ),
                tool_results=(
                    ToolExecutionResult(
                        call_id="call-bad",
                        name="send_message",
                        output="Tool send_message received malformed arguments.",
                        is_error=True,
                    ),
                ),
            )
        ],
    )

    events = list(build_stream_events(result, chunk_size=16))

    assert events[2].data == {
        "stepIndex": 0,
        "id": "call-bad",
        "name": "send_message",
        "arguments": {},
        "parseError": "Malformed tool-call arguments (invalid JSON).",
        "rawArguments": "{broken json",
    }


def test_build_stream_events_include_retrieval_metadata_on_done() -> None:
    result = AgentResult(
        response="hello world",
        model="test-model",
        provider="test-provider",
        stop_reason="terminal_tool",
        tools_used=["send_message"],
        retrieval=AgentRetrievalTrace(
            retriever="hybrid",
            citations=(
                AgentCitation(
                    index=1,
                    memory_item_id=11,
                    uri="memory://items/11",
                    score=0.88,
                    category="preference",
                ),
            ),
            context_fragments=(
                AgentContextFragment(
                    rank=1,
                    memory_item_id=11,
                    uri="memory://items/11",
                    text="The user prefers concise replies.",
                    score=0.88,
                    category="preference",
                ),
            ),
            stats=AgentRetrievalStats(
                retrieval_ms=9.25,
                total_considered=5,
                returned=1,
                cutoff_index=1,
                cutoff_score=0.88,
                top_score=0.88,
                cutoff_ratio=1.0,
                triggered_by="adaptive_ratio",
            ),
        ),
        step_traces=[
            StepTrace(
                step_index=0,
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="send_message",
                        arguments={"message": "hello world"},
                    ),
                ),
                tool_results=(
                    ToolExecutionResult(
                        call_id="call-1",
                        name="send_message",
                        output="hello world",
                        is_terminal=True,
                    ),
                ),
                usage=UsageStats(prompt_tokens=4, completion_tokens=6, total_tokens=10),
            )
        ],
    )

    events = list(build_stream_events(result, chunk_size=5))

    assert events[-1].data["retrieval"] == {
        "retriever": "hybrid",
        "citations": [
            {
                "index": 1,
                "memoryItemId": 11,
                "uri": "memory://items/11",
                "score": 0.88,
                "category": "preference",
            }
        ],
        "contextFragments": [
            {
                "rank": 1,
                "memoryItemId": 11,
                "uri": "memory://items/11",
                "text": "The user prefers concise replies.",
                "score": 0.88,
                "category": "preference",
            }
        ],
        "stats": {
            "retrievalMs": 9.25,
            "totalConsidered": 5,
            "returned": 1,
            "cutoffIndex": 1,
            "cutoffScore": 0.88,
            "topScore": 0.88,
            "cutoffRatio": 1.0,
            "triggeredBy": "adaptive_ratio",
        },
    }
