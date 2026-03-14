from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from anima_server.services.agent.runtime_types import (
    StepTrace,
    ToolCall,
    ToolExecutionResult,
    UsageStats,
)
from anima_server.services.agent.state import AgentResult


@dataclass(frozen=True, slots=True)
class AgentStreamEvent:
    event: str
    data: dict[str, object]


def build_chunk_event(content: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="chunk",
        data={"content": content},
    )


def build_tool_call_event(step_index: int, tool_call: ToolCall) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="tool_call",
        data={
            "stepIndex": step_index,
            "id": tool_call.id,
            "name": tool_call.name,
            "arguments": dict(tool_call.arguments),
        },
    )


def build_tool_return_event(
    step_index: int,
    tool_result: ToolExecutionResult,
) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="tool_return",
        data={
            "stepIndex": step_index,
            "callId": tool_result.call_id,
            "name": tool_result.name,
            "output": tool_result.output,
            "isError": tool_result.is_error,
            "isTerminal": tool_result.is_terminal,
        },
    )


def build_usage_event(usage: UsageStats) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="usage",
        data={
            "promptTokens": usage.prompt_tokens,
            "completionTokens": usage.completion_tokens,
            "totalTokens": usage.total_tokens,
        },
    )


def build_done_event(result: AgentResult) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="done",
        data={
            "status": "complete",
            "stopReason": result.stop_reason,
            "provider": result.provider,
            "model": result.model,
            "toolsUsed": list(result.tools_used),
        },
    )


def build_error_event(error_text: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="error",
        data={"error": error_text},
    )


def build_stream_events(
    result: AgentResult,
    *,
    chunk_size: int,
) -> Iterator[AgentStreamEvent]:
    for trace in result.step_traces:
        yield from _build_tool_events(trace)

    if result.response:
        for start in range(0, len(result.response), max(1, chunk_size)):
            yield build_chunk_event(
                result.response[start : start + max(1, chunk_size)]
            )

    usage = summarize_usage(result)
    if usage is not None:
        yield build_usage_event(usage)

    yield build_done_event(result)


def summarize_usage(result: AgentResult) -> UsageStats | None:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    saw_usage = False

    for trace in result.step_traces:
        if trace.usage is None:
            continue
        saw_usage = True
        prompt_tokens += trace.usage.prompt_tokens or 0
        completion_tokens += trace.usage.completion_tokens or 0
        total_tokens += trace.usage.total_tokens or 0

    if not saw_usage:
        return None

    return UsageStats(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _build_tool_events(trace: StepTrace) -> Iterator[AgentStreamEvent]:
    for tool_call in trace.tool_calls:
        yield build_tool_call_event(trace.step_index, tool_call)

    for tool_result in trace.tool_results:
        yield build_tool_return_event(trace.step_index, tool_result)
