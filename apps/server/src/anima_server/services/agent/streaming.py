from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from anima_server.services.agent.runtime_types import (
    MessageSnapshot,
    StepExecutionResult,
    StepTiming,
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


_TRACE_PREVIEW_LIMIT = 160


def build_chunk_event(content: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="chunk",
        data={"content": content},
    )


def build_reasoning_event(
    step_index: int,
    reasoning_content: str,
    reasoning_signature: str | None = None,
) -> AgentStreamEvent:
    data: dict[str, object] = {
        "stepIndex": step_index,
        "content": reasoning_content,
    }
    if reasoning_signature is not None:
        data["signature"] = reasoning_signature
    return AgentStreamEvent(event="reasoning", data=data)


def build_timing_event(
    step_index: int,
    timing: StepTiming | None,
) -> AgentStreamEvent:
    data: dict[str, object] = {"stepIndex": step_index}
    if timing is not None:
        data["stepDurationMs"] = timing.step_duration_ms
        data["llmDurationMs"] = timing.llm_duration_ms
        data["ttftMs"] = timing.ttft_ms
    return AgentStreamEvent(event="timing", data=data)


def build_step_request_event(
    step_index: int,
    *,
    request_messages: tuple[MessageSnapshot, ...],
    allowed_tools: tuple[str, ...],
    force_tool_call: bool,
) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="step_state",
        data={
            "stepIndex": step_index,
            "phase": "request",
            "messageCount": len(request_messages),
            "allowedTools": list(allowed_tools),
            "forceToolCall": force_tool_call,
            "messages": [_serialize_message_preview(message) for message in request_messages],
        },
    )


def build_step_result_event(
    step_index: int,
    *,
    step_result: StepExecutionResult,
) -> AgentStreamEvent:
    assistant_text = step_result.assistant_text or ""
    reasoning_content = step_result.reasoning_content or ""
    return AgentStreamEvent(
        event="step_state",
        data={
            "stepIndex": step_index,
            "phase": "result",
            "assistantTextChars": len(assistant_text),
            "assistantTextPreview": _preview_text(assistant_text),
            "toolCallCount": len(step_result.tool_calls),
            "reasoningChars": len(reasoning_content),
            "reasoningCaptured": bool(reasoning_content),
        },
    )


def build_warning_event(
    step_index: int,
    *,
    code: str,
    message: str,
) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="warning",
        data={
            "stepIndex": step_index,
            "code": code,
            "message": message,
        },
    )


def build_thought_event(step_index: int, thought: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="thought",
        data={
            "stepIndex": step_index,
            "content": thought,
        },
    )


def build_tool_call_event(step_index: int, tool_call: ToolCall) -> AgentStreamEvent:
    # Strip injected kwargs from the client-facing event.
    _injected = {"thinking"}
    args = {k: v for k, v in tool_call.arguments.items() if k not in _injected}
    data: dict[str, object] = {
        "stepIndex": step_index,
        "id": tool_call.id,
        "name": tool_call.name,
        "arguments": args,
    }
    if tool_call.parse_error is not None:
        data["parseError"] = tool_call.parse_error
    if tool_call.raw_arguments is not None:
        # Redact ``thinking`` from raw JSON to avoid leaking private
        # reasoning on malformed tool call responses.
        data["rawArguments"] = _redact_injected_kwargs_from_raw(tool_call.raw_arguments)
    return AgentStreamEvent(
        event="tool_call",
        data=data,
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
    data: dict[str, object] = {
        "promptTokens": usage.prompt_tokens,
        "completionTokens": usage.completion_tokens,
        "totalTokens": usage.total_tokens,
    }
    if usage.reasoning_tokens is not None:
        data["reasoningTokens"] = usage.reasoning_tokens
    if usage.cached_input_tokens is not None:
        data["cachedInputTokens"] = usage.cached_input_tokens
    return AgentStreamEvent(event="usage", data=data)


def build_done_event(result: AgentResult, *, thread_id: int | None = None) -> AgentStreamEvent:
    data = {
        "status": "complete",
        "stopReason": result.stop_reason,
        "provider": result.provider,
        "model": result.model,
        "toolsUsed": list(result.tools_used),
    }
    if thread_id is not None:
        data["threadId"] = thread_id
    return AgentStreamEvent(event="done", data=data)


def build_error_event(error_text: str) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="error",
        data={"error": error_text},
    )


def build_cancelled_event(run_id: int) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="cancelled",
        data={"runId": run_id},
    )


def build_approval_pending_event(
    run_id: int,
    tool_name: str,
    tool_call_id: str,
    tool_arguments: dict[str, object],
) -> AgentStreamEvent:
    return AgentStreamEvent(
        event="approval_pending",
        data={
            "runId": run_id,
            "toolName": tool_name,
            "toolCallId": tool_call_id,
            "arguments": tool_arguments,
        },
    )


def build_stream_events(
    result: AgentResult,
    *,
    chunk_size: int,
) -> Iterator[AgentStreamEvent]:
    for trace in result.step_traces:
        if trace.llm_invoked:
            yield build_step_request_event(
                trace.step_index,
                request_messages=trace.request_messages,
                allowed_tools=trace.allowed_tools,
                force_tool_call=trace.force_tool_call,
            )
            yield build_step_result_event(
                trace.step_index,
                step_result=StepExecutionResult(
                    assistant_text=trace.assistant_text,
                    tool_calls=trace.tool_calls,
                    usage=trace.usage,
                    reasoning_content=trace.reasoning_content,
                    reasoning_signature=trace.reasoning_signature,
                ),
            )
            if not trace.assistant_text and not trace.tool_calls:
                yield build_warning_event(
                    trace.step_index,
                    code="empty_step_result",
                    message="LLM returned no assistant text and no tool calls for this step.",
                )
        # Reasoning before content (model thinks before it speaks).
        if trace.reasoning_content:
            yield build_reasoning_event(
                trace.step_index,
                trace.reasoning_content,
                trace.reasoning_signature,
            )
        yield from _build_tool_events(trace)
        if trace.timing is not None:
            yield build_timing_event(trace.step_index, trace.timing)

    if result.response:
        for start in range(0, len(result.response), max(1, chunk_size)):
            yield build_chunk_event(result.response[start : start + max(1, chunk_size)])

    usage = summarize_usage(result)
    if usage is not None:
        yield build_usage_event(usage)

    yield build_done_event(result)


def summarize_usage(result: AgentResult) -> UsageStats | None:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    reasoning_tokens = 0
    cached_input_tokens = 0
    saw_usage = False
    saw_reasoning = False
    saw_cached = False

    for trace in result.step_traces:
        if trace.usage is None:
            continue
        saw_usage = True
        prompt_tokens += trace.usage.prompt_tokens or 0
        completion_tokens += trace.usage.completion_tokens or 0
        total_tokens += trace.usage.total_tokens or 0
        if trace.usage.reasoning_tokens is not None:
            saw_reasoning = True
            reasoning_tokens += trace.usage.reasoning_tokens
        if trace.usage.cached_input_tokens is not None:
            saw_cached = True
            cached_input_tokens += trace.usage.cached_input_tokens

    if not saw_usage:
        return None

    return UsageStats(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens if saw_reasoning else None,
        cached_input_tokens=cached_input_tokens if saw_cached else None,
    )


def _build_tool_events(trace: StepTrace) -> Iterator[AgentStreamEvent]:
    for tool_call in trace.tool_calls:
        yield build_tool_call_event(trace.step_index, tool_call)

    for tool_result in trace.tool_results:
        yield build_tool_return_event(trace.step_index, tool_result)
        if tool_result.inner_thinking:
            yield build_thought_event(trace.step_index, tool_result.inner_thinking)


def _preview_text(text: str, *, limit: int = _TRACE_PREVIEW_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


_INJECTED_KWARG_KEYS = ("thinking",)


def _redact_injected_kwargs_from_raw(raw: str) -> str:
    """Remove injected kwargs from a raw JSON arguments string.

    Tries JSON parse first (handles any value type), falls back to
    regex for malformed JSON.
    """
    import json as _json

    try:
        parsed = _json.loads(raw)
        if isinstance(parsed, dict):
            changed = False
            for key in _INJECTED_KWARG_KEYS:
                if key in parsed:
                    parsed.pop(key)
                    changed = True
            if changed:
                return _json.dumps(parsed)
    except (ValueError, TypeError):
        pass
    # Regex fallback for unparseable JSON — strip string values only.
    import re as _re

    result = raw
    for key in _INJECTED_KWARG_KEYS:
        result = _re.sub(
            rf'"{key}"\s*:\s*"(?:[^"\\]|\\.)*"\s*,?\s*',
            "",
            result,
        )
        # Also strip boolean values (for request_heartbeat)
        result = _re.sub(
            rf'"{key}"\s*:\s*(?:true|false)\s*,?\s*',
            "",
            result,
        )
    return result


def _serialize_message_preview(message: MessageSnapshot) -> dict[str, object]:
    data: dict[str, object] = {
        "role": message.role,
        "chars": len(message.content),
        "preview": _preview_text(message.content),
    }
    if message.tool_name is not None:
        data["toolName"] = message.tool_name
    if message.tool_call_id is not None:
        data["toolCallId"] = message.tool_call_id
    if message.tool_calls:
        data["toolCallCount"] = len(message.tool_calls)
    return data
