from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from anima_server.services.agent.openai_compatible_client import _serialize_tool

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 4096


@dataclass(frozen=True, slots=True)
class AnthropicResponse:
    content: str
    tool_calls: tuple[dict[str, object], ...] = ()
    usage_metadata: dict[str, object] | None = None
    response_metadata: dict[str, object] | None = None
    reasoning_content: str | None = None
    reasoning_content_signature: str | None = None
    redacted_reasoning_content: str | None = None


@dataclass(frozen=True, slots=True)
class AnthropicStreamChunk:
    content_delta: str = ""
    tool_call_deltas: tuple[dict[str, object], ...] = ()
    usage_metadata: dict[str, object] | None = None
    done: bool = False


class AnthropicChatClient:
    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        tools: Sequence[Any] = (),
        tool_choice: dict[str, object] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self.provider = "anthropic"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._transport = transport
        self._tools = tuple(tools)
        self._tool_choice = tool_choice
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._shared_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._shared_client is None or self._shared_client.is_closed:
            self._shared_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._timeout,
                    read=max(self._timeout * 5, 600.0),
                ),
                transport=self._transport,
            )
        return self._shared_client

    async def aclose(self) -> None:
        if self._shared_client is not None and not self._shared_client.is_closed:
            await self._shared_client.aclose()
            self._shared_client = None

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **_: Any,
    ) -> AnthropicChatClient:
        new_client = AnthropicChatClient(
            model=self.model,
            base_url=self.base_url,
            headers=self._headers,
            timeout=self._timeout,
            transport=self._transport,
            tools=tools,
            tool_choice=_serialize_tool_choice(tool_choice, tools),
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        new_client._shared_client = self._shared_client
        return new_client

    async def ainvoke(self, input: Sequence[Any]) -> AnthropicResponse:
        payload = self._build_payload(input, stream=False)

        client = await self._get_client()
        response = await client.post(
            f"{self.base_url}/messages",
            headers=self._request_headers(),
            json=payload,
        )
        response.raise_for_status()
        return _normalize_response(response.json())

    async def astream(
        self,
        input: Sequence[Any],
    ) -> AsyncGenerator[AnthropicStreamChunk, None]:
        payload = self._build_payload(input, stream=True)

        client = await self._get_client()
        async with client.stream(
            "POST",
            f"{self.base_url}/messages",
            headers=self._request_headers(),
            json=payload,
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                response.raise_for_status()

            data_lines: list[str] = []
            async for line in response.aiter_lines():
                trimmed = line.strip()
                if not trimmed:
                    if data_lines:
                        for chunk in _chunks_from_sse_data("\n".join(data_lines)):
                            yield chunk
                        data_lines = []
                    continue
                if trimmed.startswith("data:"):
                    data_lines.append(trimmed[5:].strip())

            if data_lines:
                for chunk in _chunks_from_sse_data("\n".join(data_lines)):
                    yield chunk

    def _build_payload(
        self,
        input: Sequence[Any],
        *,
        stream: bool,
    ) -> dict[str, object]:
        system_prompt, messages = _serialize_messages(input)
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self._max_tokens or _DEFAULT_MAX_TOKENS,
            "messages": messages,
            "stream": stream,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if self._tools:
            payload["tools"] = [_serialize_anthropic_tool(tool) for tool in self._tools]
        if self._tool_choice is not None:
            payload["tool_choice"] = self._tool_choice
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload

    def _request_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "anthropic-version": _ANTHROPIC_VERSION,
            **self._headers,
        }


def _serialize_messages(messages: Sequence[Any]) -> tuple[str, list[dict[str, object]]]:
    system_parts: list[str] = []
    serialized: list[dict[str, object]] = []

    for message in messages:
        message_type = getattr(message, "type", "")
        if message_type == "system":
            content = _serialize_text(getattr(message, "content", ""))
            if content:
                system_parts.append(content)
            continue

        if message_type == "human":
            serialized.append(
                {"role": "user", "content": _serialize_text(getattr(message, "content", ""))}
            )
            continue

        if message_type == "tool":
            serialized.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(getattr(message, "tool_call_id", "") or ""),
                            "content": _serialize_text(getattr(message, "content", "")),
                        }
                    ],
                }
            )
            continue

        serialized.append(
            {
                "role": "assistant",
                "content": _serialize_assistant_content(message),
            }
        )

    return "\n\n".join(system_parts), _merge_adjacent_messages(serialized)


def _serialize_assistant_content(message: Any) -> str | list[dict[str, object]]:
    text = _serialize_text(getattr(message, "content", ""))
    tool_calls = _normalize_request_tool_calls(getattr(message, "tool_calls", ()))

    if not tool_calls:
        return text

    blocks: list[dict[str, object]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(
        {
            "type": "tool_use",
            "id": tool_call["id"],
            "name": tool_call["name"],
            "input": tool_call["args"],
        }
        for tool_call in tool_calls
    )
    return blocks


def _normalize_request_tool_calls(raw_tool_calls: object) -> list[dict[str, object]]:
    if not isinstance(raw_tool_calls, list):
        return []

    normalized: list[dict[str, object]] = []
    for index, raw_tool_call in enumerate(raw_tool_calls):
        if not isinstance(raw_tool_call, dict):
            continue
        name = str(raw_tool_call.get("name", "")).strip()
        if not name:
            continue
        args = raw_tool_call.get("args", {})
        normalized.append(
            {
                "id": str(raw_tool_call.get("id") or f"tool-call-{index}"),
                "name": name,
                "args": args if isinstance(args, dict) else {},
            }
        )
    return normalized


def _merge_adjacent_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for message in messages:
        role = message.get("role")
        if (
            merged
            and isinstance(role, str)
            and merged[-1].get("role") == role
        ):
            merged[-1]["content"] = _merge_content(
                merged[-1].get("content"),
                message.get("content"),
            )
            continue
        merged.append(dict(message))
    return merged


def _merge_content(left: object, right: object) -> str | list[dict[str, object]]:
    if isinstance(left, str) and isinstance(right, str):
        return "\n\n".join(part for part in (left, right) if part)
    left_blocks = _content_blocks(left)
    right_blocks = _content_blocks(right)
    return [*left_blocks, *right_blocks]


def _content_blocks(content: object) -> list[dict[str, object]]:
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    text = _serialize_text(content)
    return [{"type": "text", "text": text}] if text else []


def _serialize_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return str(content)


def _serialize_anthropic_tool(tool: Any) -> dict[str, object]:
    openai_tool = tool if isinstance(tool, dict) else _serialize_tool(tool)
    function = openai_tool.get("function") if isinstance(openai_tool, dict) else None
    if isinstance(function, dict):
        name = str(function.get("name", "")).strip()
        parameters = function.get("parameters")
        description = str(function.get("description", "")).strip()
    elif isinstance(openai_tool, dict):
        name = str(openai_tool.get("name", "")).strip()
        parameters = openai_tool.get("input_schema") or openai_tool.get("parameters")
        description = str(openai_tool.get("description", "")).strip()
    else:
        name = ""
        parameters = None
        description = ""

    if not name:
        raise ValueError("Tool name is required for Anthropic serialization.")

    payload: dict[str, object] = {
        "name": name,
        "input_schema": parameters if isinstance(parameters, dict) else {"type": "object"},
    }
    if description:
        payload["description"] = " ".join(description.split())
    return payload


def _serialize_tool_choice(
    tool_choice: str | None,
    tools: Sequence[Any],
) -> dict[str, object] | None:
    if tool_choice is None:
        return None

    normalized = tool_choice.strip().lower()
    if normalized == "required":
        if len(tools) == 1:
            tool = _serialize_anthropic_tool(tools[0])
            name = tool.get("name")
            if isinstance(name, str) and name:
                return {"type": "tool", "name": name}
        return {"type": "any"}
    if normalized in {"auto", "any", "none"}:
        return {"type": normalized}
    return {"type": "auto"}


def _normalize_response(payload: object) -> AnthropicResponse:
    if not isinstance(payload, dict):
        return AnthropicResponse(content="")

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_signature: str | None = None
    redacted_reasoning_parts: list[str] = []
    tool_calls: list[dict[str, object]] = []

    blocks = payload.get("content")
    if isinstance(blocks, list):
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            elif block_type == "tool_use":
                tool_call = _normalize_response_tool_call(block, index=index)
                if tool_call is not None:
                    tool_calls.append(tool_call)
            elif block_type == "thinking":
                thinking = block.get("thinking")
                if isinstance(thinking, str):
                    reasoning_parts.append(thinking)
                signature = block.get("signature")
                if isinstance(signature, str) and signature.strip():
                    reasoning_signature = signature
            elif block_type == "redacted_thinking":
                data = block.get("data")
                if isinstance(data, str):
                    redacted_reasoning_parts.append(data)

    return AnthropicResponse(
        content="".join(text_parts),
        tool_calls=tuple(tool_calls),
        usage_metadata=_extract_usage_metadata(payload),
        response_metadata={
            key: value
            for key, value in payload.items()
            if key not in {"content", "usage"}
        },
        reasoning_content="\n".join(reasoning_parts) or None,
        reasoning_content_signature=reasoning_signature,
        redacted_reasoning_content="\n".join(redacted_reasoning_parts) or None,
    )


def _normalize_response_tool_call(
    block: dict[str, object],
    *,
    index: int,
) -> dict[str, object] | None:
    name = str(block.get("name", "")).strip()
    if not name:
        return None

    raw_input = block.get("input", {})
    payload: dict[str, object] = {
        "id": str(block.get("id") or f"tool-call-{index}"),
        "name": name,
        "args": raw_input if isinstance(raw_input, dict) else {},
    }
    if raw_input not in ({}, None) and not isinstance(raw_input, dict):
        payload["parse_error"] = "Tool-call arguments must be a JSON object."
        payload["raw_arguments"] = str(raw_input)[:500]
    return payload


def _extract_usage_metadata(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None

    raw_usage = payload.get("usage")
    usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if (
        "total_tokens" not in usage
        and isinstance(input_tokens, int)
        and isinstance(output_tokens, int)
    ):
        usage["total_tokens"] = input_tokens + output_tokens

    return usage or None


def _chunks_from_sse_data(raw_data: str) -> tuple[AnthropicStreamChunk, ...]:
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError:
        return ()

    if not isinstance(payload, dict):
        return ()

    event_type = payload.get("type")
    if event_type == "error":
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        raise RuntimeError(message if isinstance(message, str) else "Anthropic stream error")

    if event_type == "message_start":
        message = payload.get("message")
        usage = _extract_usage_metadata(message) if isinstance(message, dict) else None
        return (AnthropicStreamChunk(usage_metadata=usage),) if usage else ()

    if event_type == "content_block_start":
        block = payload.get("content_block")
        index = payload.get("index", 0)
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return (
                AnthropicStreamChunk(
                    tool_call_deltas=(
                        {
                            "index": index if isinstance(index, int) else 0,
                            "id": block.get("id") if isinstance(block.get("id"), str) else None,
                            "name": block.get("name")
                            if isinstance(block.get("name"), str)
                            else None,
                            "arguments": "",
                        },
                    )
                ),
            )
        return ()

    if event_type == "content_block_delta":
        return _chunk_from_content_delta(payload)

    if event_type == "message_delta":
        usage = _extract_usage_metadata(payload)
        delta = payload.get("delta")
        done = bool(isinstance(delta, dict) and delta.get("stop_reason"))
        return (AnthropicStreamChunk(usage_metadata=usage, done=done),)

    if event_type == "message_stop":
        return (AnthropicStreamChunk(done=True),)

    return ()


def _chunk_from_content_delta(payload: dict[str, object]) -> tuple[AnthropicStreamChunk, ...]:
    delta = payload.get("delta")
    if not isinstance(delta, dict):
        return ()

    delta_type = delta.get("type")
    if delta_type == "text_delta":
        text = delta.get("text")
        return (AnthropicStreamChunk(content_delta=text),) if isinstance(text, str) else ()

    if delta_type == "input_json_delta":
        index = payload.get("index", 0)
        partial_json = delta.get("partial_json")
        return (
            AnthropicStreamChunk(
                tool_call_deltas=(
                    {
                        "index": index if isinstance(index, int) else 0,
                        "id": None,
                        "name": None,
                        "arguments": partial_json if isinstance(partial_json, str) else "",
                    },
                )
            ),
        )

    return ()
