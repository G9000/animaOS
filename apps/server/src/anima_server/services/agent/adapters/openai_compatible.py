from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from anima_server.config import settings
from anima_server.services.agent.llm import create_llm
from anima_server.services.agent.messages import message_content
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    ToolCall,
    UsageStats,
)

from .base import BaseLLMAdapter


class OpenAICompatibleAdapter(BaseLLMAdapter):
    def __init__(
        self,
        llm: BaseChatModel,
        *,
        provider: str,
        model: str,
    ) -> None:
        self._llm = llm
        self.provider = provider
        self.model = model

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
        response = await self._llm.ainvoke(list(request.messages))
        if not isinstance(response, AIMessage):
            return StepExecutionResult(
                assistant_text=message_content(response),
                raw_response=response,
            )

        return StepExecutionResult(
            assistant_text=message_content(response),
            tool_calls=_normalize_tool_calls(getattr(response, "tool_calls", [])),
            usage=_normalize_usage(response),
            raw_response=response,
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


def _normalize_usage(message: AIMessage) -> UsageStats | None:
    raw_usage = getattr(message, "usage_metadata", None)
    if not isinstance(raw_usage, dict):
        response_metadata = getattr(message, "response_metadata", {})
        if isinstance(response_metadata, dict):
            raw_usage = response_metadata.get("token_usage") or response_metadata.get("usage")

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
