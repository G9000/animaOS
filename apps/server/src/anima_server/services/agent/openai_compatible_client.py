from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class OpenAICompatibleResponse:
    content: str
    tool_calls: tuple[dict[str, object], ...] = ()
    usage_metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class OpenAICompatibleStreamChunk:
    content_delta: str = ""
    tool_call_deltas: tuple[dict[str, object], ...] = ()
    usage_metadata: dict[str, object] | None = None
    done: bool = False


class OpenAICompatibleChatClient:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        tools: Sequence[Any] = (),
        tool_choice: str | dict[str, object] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self.provider = provider
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
        """Return a shared httpx client for connection reuse."""
        if self._shared_client is None or self._shared_client.is_closed:
            self._shared_client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._shared_client

    async def aclose(self) -> None:
        """Close the shared httpx client."""
        if self._shared_client is not None and not self._shared_client.is_closed:
            await self._shared_client.aclose()
            self._shared_client = None

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **_: Any,
    ) -> OpenAICompatibleChatClient:
        resolved_tool_choice = _serialize_tool_choice(tool_choice, tools)
        new_client = OpenAICompatibleChatClient(
            provider=self.provider,
            model=self.model,
            base_url=self.base_url,
            headers=self._headers,
            timeout=self._timeout,
            transport=self._transport,
            tools=tools,
            tool_choice=resolved_tool_choice,
            max_tokens=self._max_tokens,
        )
        # Share the underlying httpx client for connection reuse
        new_client._shared_client = self._shared_client
        return new_client

    async def ainvoke(self, input: Sequence[Any]) -> OpenAICompatibleResponse:
        payload = self._build_payload(input, stream=False)

        client = await self._get_client()
        response = await client.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Content-Type": "application/json",
                **self._headers,
            },
            json=payload,
        )
        response.raise_for_status()

        body = response.json()
        choice = _first_choice(body)
        message = choice.get("message")
        if not isinstance(message, dict):
            return OpenAICompatibleResponse(content="")

        tool_calls = _normalize_response_tool_calls(message.get("tool_calls"))
        return OpenAICompatibleResponse(
            content=_coerce_response_content(message.get("content")),
            tool_calls=tool_calls,
            usage_metadata=_extract_usage_metadata(body),
        )

    async def astream(
        self,
        input: Sequence[Any],
    ) -> AsyncGenerator[OpenAICompatibleStreamChunk, None]:
        payload = self._build_payload(input, stream=True)

        client = await self._get_client()
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={
                "Content-Type": "application/json",
                **self._headers,
            },
            json=payload,
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                response.raise_for_status()

            async for line in response.aiter_lines():
                trimmed = line.strip()
                if not trimmed or not trimmed.startswith("data:"):
                    continue

                payload_text = trimmed[5:].strip()
                if payload_text == "[DONE]":
                    yield OpenAICompatibleStreamChunk(done=True)
                    break

                try:
                    body = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue

                choice = _first_choice(body)
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    delta = {}

                yield OpenAICompatibleStreamChunk(
                    content_delta=_coerce_response_content(delta.get("content")),
                    tool_call_deltas=_normalize_stream_tool_call_deltas(delta.get("tool_calls")),
                    usage_metadata=_extract_usage_metadata(body),
                    done=choice.get("finish_reason") is not None or body.get("done") is True,
                )

    def _build_payload(
        self,
        input: Sequence[Any],
        *,
        stream: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [_serialize_message(message) for message in input],
            "stream": stream,
        }
        if self._max_tokens is not None:
            payload["max_tokens"] = self._max_tokens
        if self._tools:
            payload["tools"] = [
                tool if isinstance(tool, dict) and "type" in tool and "function" in tool
                else _serialize_tool(tool)
                for tool in self._tools
            ]
        if self._tool_choice is not None:
            payload["tool_choice"] = self._tool_choice
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload


def _first_choice(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    choice = choices[0]
    return choice if isinstance(choice, dict) else {}


def _serialize_message(message: Any) -> dict[str, object]:
    message_type = getattr(message, "type", "")
    if message_type == "system":
        return {
            "role": "system",
            "content": _serialize_content(getattr(message, "content", "")),
        }
    if message_type == "human":
        return {
            "role": "user",
            "content": _serialize_content(getattr(message, "content", "")),
        }
    if message_type == "tool":
        payload: dict[str, object] = {
            "role": "tool",
            "content": _serialize_content(getattr(message, "content", "")),
            "tool_call_id": str(getattr(message, "tool_call_id", "") or ""),
        }
        name = getattr(message, "name", None)
        if isinstance(name, str) and name.strip():
            payload["name"] = name.strip()
        return payload

    payload = {
        "role": "assistant",
        "content": _serialize_content(getattr(message, "content", "")),
    }
    tool_calls = _normalize_request_tool_calls(getattr(message, "tool_calls", ()))
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


def _serialize_content(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if content is None:
        return None
    return str(content)


def _coerce_response_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


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
        call_id = str(raw_tool_call.get("id") or f"tool-call-{index}")
        arguments = raw_tool_call.get("args", {})
        parse_error = raw_tool_call.get("parse_error")
        raw_arguments = raw_tool_call.get("raw_arguments")
        if isinstance(parse_error, str) and parse_error.strip() and isinstance(raw_arguments, str):
            serialized_arguments = raw_arguments
        else:
            serialized_arguments = json.dumps(
                arguments if isinstance(arguments, dict) else {},
                ensure_ascii=True,
            )
        normalized.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": serialized_arguments,
                },
            }
        )
    return normalized


def _normalize_response_tool_calls(raw_tool_calls: object) -> tuple[dict[str, object], ...]:
    if not isinstance(raw_tool_calls, list):
        return ()

    normalized: list[dict[str, object]] = []
    for index, raw_tool_call in enumerate(raw_tool_calls):
        if not isinstance(raw_tool_call, dict):
            continue
        function = raw_tool_call.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name", "")).strip()
        if not name:
            continue
        arguments, parse_error, raw_arguments = _parse_tool_arguments(function.get("arguments"))
        payload: dict[str, object] = {
            "id": str(raw_tool_call.get("id") or f"tool-call-{index}"),
            "name": name,
            "args": arguments,
        }
        if parse_error is not None:
            payload["parse_error"] = parse_error
        if raw_arguments is not None:
            payload["raw_arguments"] = raw_arguments
        normalized.append(payload)
    return tuple(normalized)


def _normalize_stream_tool_call_deltas(
    raw_tool_calls: object,
) -> tuple[dict[str, object], ...]:
    if not isinstance(raw_tool_calls, list):
        return ()

    normalized: list[dict[str, object]] = []
    for index, raw_tool_call in enumerate(raw_tool_calls):
        if not isinstance(raw_tool_call, dict):
            continue
        function = raw_tool_call.get("function")
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments")
        normalized.append(
            {
                "index": raw_tool_call.get("index", index),
                "id": raw_tool_call.get("id"),
                "name": function.get("name"),
                "arguments": arguments if isinstance(arguments, str) else "",
            }
        )
    return tuple(normalized)


def _extract_usage_metadata(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None

    usage_payload = payload.get("usage")
    usage = dict(usage_payload) if isinstance(usage_payload, dict) else {}

    prompt_eval_count = payload.get("prompt_eval_count")
    eval_count = payload.get("eval_count")

    if isinstance(prompt_eval_count, int):
        usage.setdefault("prompt_tokens", prompt_eval_count)
        usage["prompt_eval_count"] = prompt_eval_count
    if isinstance(eval_count, int):
        usage.setdefault("completion_tokens", eval_count)
        usage["eval_count"] = eval_count

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if (
        "total_tokens" not in usage
        and isinstance(prompt_tokens, int)
        and isinstance(completion_tokens, int)
    ):
        usage["total_tokens"] = prompt_tokens + completion_tokens

    return usage or None


def _parse_tool_arguments(
    raw_arguments: object,
) -> tuple[dict[str, object], str | None, str | None]:
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments), None, None
    if not isinstance(raw_arguments, str) or not raw_arguments.strip():
        return {}, None, None
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}, "Malformed tool-call arguments (invalid JSON).", raw_arguments[:500]
    if not isinstance(parsed, dict):
        return (
            {},
            f"Tool-call arguments must be a JSON object, got {type(parsed).__name__}.",
            raw_arguments[:500],
        )
    return parsed, None, None


def _serialize_tool_choice(
    tool_choice: str | None,
    tools: Sequence[Any],
) -> str | dict[str, object] | None:
    if tool_choice is None:
        return None
    normalized = tool_choice.strip().lower()
    if normalized != "required":
        return normalized
    if len(tools) == 1:
        tool_name = _tool_name(tools[0])
        if tool_name:
            return {
                "type": "function",
                "function": {
                    "name": tool_name,
                },
            }
    return "required"


def _serialize_tool(tool: Any) -> dict[str, object]:
    name = _tool_name(tool)
    if not name:
        raise ValueError("Tool name is required for OpenAI-compatible serialization.")
    description = _tool_description(tool)
    parameters = _tool_parameters(tool)
    function_payload: dict[str, object] = {
        "name": name,
        "parameters": parameters,
    }
    if description:
        function_payload["description"] = description
    return {
        "type": "function",
        "function": function_payload,
    }


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        # Already-serialized OpenAI format: {"type": "function", "function": {"name": ...}}
        fn = tool.get("function", {})
        if isinstance(fn, dict):
            return str(fn.get("name", "")).strip()
        return str(tool.get("name", "")).strip()
    return (getattr(tool, "name", "") or getattr(tool, "__name__", "")).strip()


def _tool_description(tool: Any) -> str:
    return " ".join(str(getattr(tool, "description", "")).strip().split())


def _tool_parameters(tool: Any) -> dict[str, object]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        schema = args_schema.model_json_schema()
        if isinstance(schema, dict):
            return schema

    raw_args = getattr(tool, "args", None)
    if isinstance(raw_args, dict):
        properties = {
            str(name): spec
            for name, spec in raw_args.items()
            if isinstance(name, str) and isinstance(spec, dict)
        }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        }

    return {
        "type": "object",
        "properties": {},
    }
