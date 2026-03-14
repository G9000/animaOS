from __future__ import annotations

from anima_server.services.agent.runtime_types import StepTrace, ToolCall, ToolExecutionResult, UsageStats
from anima_server.services.agent.state import AgentResult
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
        "tool_call",
        "tool_return",
        "chunk",
        "chunk",
        "chunk",
        "usage",
        "done",
    ]
    assert events[0].data == {
        "stepIndex": 0,
        "id": "call-1",
        "name": "send_message",
        "arguments": {"message": "hello world"},
    }
    assert events[1].data == {
        "stepIndex": 0,
        "callId": "call-1",
        "name": "send_message",
        "output": "hello world",
        "isError": False,
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
