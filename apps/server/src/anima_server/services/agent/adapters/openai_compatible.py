from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
import json
import time
from typing import Any

from anima_server.config import settings
from anima_server.services.agent.llm import (
    ChatClient,
    create_llm,
    resolve_base_url,
    wrap_llm_error,
)
from anima_server.services.agent.output_filter import (
    ReasoningTraceFilter,
    strip_reasoning_traces,
)
from anima_server.services.agent.messages import (
    message_content,
    message_tool_calls,
    message_usage_payload,
)
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepStreamEvent,
    StepExecutionResult,
    ToolCall,
    UsageStats,
)

from .base import BaseLLMAdapter


class OpenAICompatibleAdapter(BaseLLMAdapter):
    def __init__(
        self,
        llm: ChatClient,
        *,
        provider: str,
        model: str,
    ) -> None:
        self._llm = llm
        self.provider = provider
        self.model = model
        self._base_url = resolve_base_url(provider)

    @classmethod
    def create(cls) -> "OpenAICompatibleAdapter":
        return cls(
            create_llm(),
            provider=settings.agent_provider,
            model=settings.agent_model,
        )

    def prepare(self) -> None:
        return None

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        llm: Any = self._llm
        if request.available_tools:
            llm = self._llm.bind_tools(
                list(request.available_tools),
                tool_choice="required" if request.force_tool_call else "auto",
            )

        try:
            response = await llm.ainvoke(list(request.messages))
        except Exception as exc:
            raise wrap_llm_error(
                exc,
                provider=self.provider,
                base_url=self._base_url,
            ) from exc

        visible_text, tag_reasoning = strip_reasoning_traces(
            message_content(response))
        native_reasoning, native_signature = _extract_native_reasoning(
            response)
        reasoning_content = native_reasoning or tag_reasoning
        reasoning_signature = native_signature

        return StepExecutionResult(
            assistant_text=visible_text,
            tool_calls=_normalize_tool_calls(message_tool_calls(response)),
            usage=_normalize_usage(response),
            raw_response=response,
            reasoning_content=reasoning_content,
            reasoning_signature=reasoning_signature,
        )

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StepStreamEvent, None]:
        llm: Any = self._llm
        if request.available_tools:
            llm = self._llm.bind_tools(
                list(request.available_tools),
                tool_choice="required" if request.force_tool_call else "auto",
            )

        content_parts: list[str] = []
        tool_call_state: dict[int, dict[str, object]] = {}
        usage: UsageStats | None = None
        reasoning_filter = ReasoningTraceFilter()
        request_start_time = time.monotonic()
        first_content_time: float | None = None

        try:
            async for chunk in llm.astream(list(request.messages)):
                content_delta = getattr(chunk, "content_delta", "")
                if content_delta:
                    if first_content_time is None:
                        first_content_time = time.monotonic()
                    visible_delta = reasoning_filter.feed(content_delta)
                    if visible_delta:
                        content_parts.append(visible_delta)
                        yield StepStreamEvent(content_delta=visible_delta)

                for delta in _stream_tool_call_deltas(chunk):
                    index = delta.get("index", 0)
                    if not isinstance(index, int):
                        continue
                    state = tool_call_state.setdefault(
                        index,
                        {"id": None, "name": "", "arguments_parts": []},
                    )
                    call_id = delta.get("id")
                    if isinstance(call_id, str) and call_id.strip():
                        state["id"] = call_id
                    name = delta.get("name")
                    if isinstance(name, str) and name.strip():
                        state["name"] = name
                    arguments = delta.get("arguments")
                    if isinstance(arguments, str) and arguments:
                        state["arguments_parts"].append(arguments)

                chunk_usage = _normalize_usage(chunk)
                if chunk_usage is not None:
                    usage = chunk_usage
        except Exception as exc:
            raise wrap_llm_error(
                exc,
                provider=self.provider,
                base_url=self._base_url,
            ) from exc

        remaining_visible_text = reasoning_filter.flush()
        if remaining_visible_text:
            content_parts.append(remaining_visible_text)
            yield StepStreamEvent(content_delta=remaining_visible_text)

        tag_reasoning = reasoning_filter.captured_reasoning
        # Native fields not available during streaming (no final response
        # object), so we rely on tag-based capture only.
        yield StepStreamEvent(
            result=StepExecutionResult(
                assistant_text="".join(content_parts),
                tool_calls=_finalize_stream_tool_calls(tool_call_state),
                usage=usage,
                reasoning_content=tag_reasoning,
                ttft_ms=round((first_content_time - request_start_time) * 1000, 2)
                if first_content_time is not None
                else None,
            )
        )


def _normalize_tool_calls(raw_tool_calls: Sequence[Any]) -> tuple[ToolCall, ...]:
    normalized: list[ToolCall] = []

    for index, raw_tool_call in enumerate(raw_tool_calls):
        if isinstance(raw_tool_call, dict):
            name = str(raw_tool_call.get("name", "")).strip()
            call_id = str(raw_tool_call.get("id") or f"tool-call-{index}")
            arguments = raw_tool_call.get("args", {})
            parse_error = raw_tool_call.get("parse_error")
            raw_arguments = raw_tool_call.get("raw_arguments")
        else:
            name = str(getattr(raw_tool_call, "name", "")).strip()
            call_id = str(getattr(raw_tool_call, "id", None)
                          or f"tool-call-{index}")
            arguments = getattr(raw_tool_call, "args", {})
            parse_error = getattr(raw_tool_call, "parse_error", None)
            raw_arguments = getattr(raw_tool_call, "raw_arguments", None)

        if not name:
            continue

        normalized_arguments: dict[str, object] = arguments if isinstance(
            arguments, dict) else {}
        normalized_parse_error = (
            str(parse_error).strip() if isinstance(
                parse_error, str) and parse_error.strip() else None
        )
        normalized_raw_arguments = (
            str(raw_arguments)[:500]
            if isinstance(raw_arguments, str) and raw_arguments
            else None
        )
        if normalized_parse_error is None and arguments not in ({}, None) and not isinstance(arguments, dict):
            normalized_parse_error = "Tool-call arguments must be a JSON object."
            normalized_raw_arguments = str(arguments)[:500]

        normalized.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=normalized_arguments,
                parse_error=normalized_parse_error,
                raw_arguments=normalized_raw_arguments,
            )
        )

    return tuple(normalized)


def _normalize_usage(message: Any) -> UsageStats | None:
    raw_usage = message_usage_payload(message)
    if not isinstance(raw_usage, dict):
        return None

    # Reasoning tokens — provider-specific extraction.
    reasoning_tokens: int | None = None
    completion_details = raw_usage.get("completion_tokens_details")
    if isinstance(completion_details, dict):
        reasoning_tokens = _coerce_optional_int(
            completion_details.get("reasoning_tokens"))
    if reasoning_tokens is None:
        reasoning_tokens = _coerce_optional_int(
            raw_usage.get("thoughts_token_count"))

    cached_input_tokens: int | None = None
    prompt_details = raw_usage.get("prompt_tokens_details")
    if isinstance(prompt_details, dict):
        cached_input_tokens = _coerce_optional_int(
            prompt_details.get("cached_tokens"))
    if cached_input_tokens is None:
        cached_input_tokens = _coerce_optional_int(
            raw_usage.get("cache_read_input_tokens"))

    return UsageStats(
        prompt_tokens=_coerce_optional_int(
            raw_usage.get("input_tokens") or raw_usage.get("prompt_tokens")
        ),
        completion_tokens=_coerce_optional_int(
            raw_usage.get("output_tokens") or raw_usage.get(
                "completion_tokens")
        ),
        total_tokens=_coerce_optional_int(raw_usage.get("total_tokens")),
        reasoning_tokens=reasoning_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def _coerce_optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _extract_native_reasoning(response: Any) -> tuple[str | None, str | None]:
    """Extract reasoning from native provider fields on the response object.

    Returns ``(reasoning_content, reasoning_signature)``.
    """
    # DeepSeek / Anthropic: reasoning_content
    reasoning = getattr(response, "reasoning_content", None)
    if isinstance(reasoning, str) and reasoning.strip():
        signature = getattr(response, "reasoning_content_signature", None)
        return reasoning.strip(), (
            signature if isinstance(
                signature, str) and signature.strip() else None
        )
    # Anthropic: redacted reasoning
    redacted = getattr(response, "redacted_reasoning_content", None)
    if isinstance(redacted, str) and redacted.strip():
        return redacted.strip(), None
    # OpenAI o1/o3: omitted flag (no content, just a signal)
    omitted = getattr(response, "omitted_reasoning_content", None)
    if omitted is True:
        return "[reasoning omitted by provider]", None
    return None, None


def _stream_tool_call_deltas(message: Any) -> Sequence[dict[str, object]]:
    raw_tool_calls = getattr(message, "tool_call_deltas", ())
    return raw_tool_calls if isinstance(raw_tool_calls, tuple) else ()


def _finalize_stream_tool_calls(
    tool_call_state: dict[int, dict[str, object]],
) -> tuple[ToolCall, ...]:
    normalized: list[ToolCall] = []
    for index in sorted(tool_call_state):
        state = tool_call_state[index]
        name = state.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        call_id = state.get("id")
        arguments_text = "".join(
            part
            for part in state.get("arguments_parts", ())
            if isinstance(part, str)
        )
        try:
            arguments = _parse_stream_arguments(arguments_text)
            parse_error: str | None = None
            raw_arguments: str | None = None
        except MalformedToolArgumentsError:
            parse_error = "Malformed tool-call arguments (invalid JSON)."
            raw_arguments = arguments_text[:500]
            arguments = {}
        normalized.append(
            ToolCall(
                id=call_id if isinstance(
                    call_id, str) and call_id else f"tool-call-{index}",
                name=name.strip(),
                arguments=arguments,
                parse_error=parse_error,
                raw_arguments=raw_arguments,
            )
        )
    return tuple(normalized)


class MalformedToolArgumentsError(RuntimeError):
    """Raised when streamed tool-call arguments cannot be parsed as valid JSON."""


def _parse_stream_arguments(arguments_text: str) -> dict[str, object]:
    if not arguments_text.strip():
        return {}
    try:
        parsed = json.loads(arguments_text)
    except json.JSONDecodeError as exc:
        raise MalformedToolArgumentsError(
            f"Malformed tool-call arguments (invalid JSON): {arguments_text[:200]}"
        ) from exc
    if not isinstance(parsed, dict):
        raise MalformedToolArgumentsError(
            f"Tool-call arguments must be a JSON object, got {type(parsed).__name__}"
        )
    return parsed
