from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
import json
from typing import Any

from anima_server.config import settings
from anima_server.services.agent.llm import (
    ChatClient,
    create_llm,
    resolve_base_url,
    wrap_llm_error,
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
        return StepExecutionResult(
            assistant_text=message_content(response),
            tool_calls=_normalize_tool_calls(message_tool_calls(response)),
            usage=_normalize_usage(response),
            raw_response=response,
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

        try:
            async for chunk in llm.astream(list(request.messages)):
                content_delta = getattr(chunk, "content_delta", "")
                if content_delta:
                    content_parts.append(content_delta)
                    yield StepStreamEvent(content_delta=content_delta)

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

        yield StepStreamEvent(
            result=StepExecutionResult(
                assistant_text="".join(content_parts),
                tool_calls=_finalize_stream_tool_calls(tool_call_state),
                usage=usage,
            )
        )


def _normalize_tool_calls(raw_tool_calls: Sequence[Any]) -> tuple[ToolCall, ...]:
    normalized: list[ToolCall] = []

    for index, raw_tool_call in enumerate(raw_tool_calls):
        if isinstance(raw_tool_call, dict):
            name = str(raw_tool_call.get("name", "")).strip()
            call_id = str(raw_tool_call.get("id") or f"tool-call-{index}")
            arguments = raw_tool_call.get("args", {})
        else:
            name = str(getattr(raw_tool_call, "name", "")).strip()
            call_id = str(getattr(raw_tool_call, "id", None) or f"tool-call-{index}")
            arguments = getattr(raw_tool_call, "args", {})

        if not name:
            continue

        normalized.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )

    return tuple(normalized)


def _normalize_usage(message: Any) -> UsageStats | None:
    raw_usage = message_usage_payload(message)
    if not isinstance(raw_usage, dict):
        return None

    return UsageStats(
        prompt_tokens=_coerce_optional_int(
            raw_usage.get("input_tokens") or raw_usage.get("prompt_tokens")
        ),
        completion_tokens=_coerce_optional_int(
            raw_usage.get("output_tokens") or raw_usage.get("completion_tokens")
        ),
        total_tokens=_coerce_optional_int(raw_usage.get("total_tokens")),
    )


def _coerce_optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


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
        normalized.append(
            ToolCall(
                id=call_id if isinstance(call_id, str) and call_id else f"tool-call-{index}",
                name=name.strip(),
                arguments=_parse_stream_arguments(arguments_text),
            )
        )
    return tuple(normalized)


def _parse_stream_arguments(arguments_text: str) -> dict[str, object]:
    if not arguments_text.strip():
        return {}
    try:
        parsed = json.loads(arguments_text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
