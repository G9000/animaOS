from __future__ import annotations

import json

import httpx
import pytest
from anima_server.services.agent.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from anima_server.services.agent.openai_compatible_client import (
    OpenAICompatibleChatClient,
    OpenAICompatibleStreamChunk,
)
from anima_server.services.agent.tools import send_message


@pytest.mark.asyncio
async def test_openai_compatible_chat_client_serializes_messages_and_tools() -> None:
    captured_payload: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "done",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "send_message",
                                        "arguments": json.dumps({"message": "done"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "model": "llama3.2",
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )

    client = OpenAICompatibleChatClient(
        provider="ollama",
        model="llama3.2",
        base_url="http://ollama.local/v1",
        transport=httpx.MockTransport(handler),
    ).bind_tools([send_message], tool_choice="required")

    response = await client.ainvoke(
        [
            SystemMessage(content="system prompt"),
            HumanMessage(content="hello"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-0",
                        "name": "send_message",
                        "args": {"message": "partial"},
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="partial",
                tool_call_id="call-0",
                name="send_message",
            ),
        ]
    )

    assert captured_payload["model"] == "llama3.2"
    assert captured_payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "send_message"},
    }
    assert captured_payload["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-0",
                    "type": "function",
                    "function": {
                        "name": "send_message",
                        "arguments": json.dumps({"message": "partial"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "partial",
            "tool_call_id": "call-0",
            "name": "send_message",
        },
    ]
    tools = captured_payload["tools"]
    assert isinstance(tools, list)
    assert tools[0]["function"]["name"] == "send_message"
    assert response.content == "done"
    assert response.tool_calls == (
        {
            "id": "call-1",
            "name": "send_message",
            "args": {"message": "done"},
        },
    )
    assert response.usage_metadata == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


@pytest.mark.asyncio
async def test_openai_compatible_chat_client_streams_sse_chunks() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["stream"] is True
        body = (
            "data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "hel",
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            )
            + "\n\n"
            + "data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "send_message",
                                            "arguments": "{\"message\":\"hi\"}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 9,
                        "completion_tokens": 4,
                        "total_tokens": 13,
                    },
                }
            )
            + "\n\n"
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"Content-Type": "text/event-stream"},
        )

    client = OpenAICompatibleChatClient(
        provider="ollama",
        model="llama3.2",
        base_url="http://ollama.local/v1",
        transport=httpx.MockTransport(handler),
    )

    chunks = [chunk async for chunk in client.astream([HumanMessage(content="hello")])]

    assert chunks == [
        OpenAICompatibleStreamChunk(content_delta="hel", done=False),
        OpenAICompatibleStreamChunk(
            tool_call_deltas=(
                {
                    "index": 0,
                    "id": "call-1",
                    "name": "send_message",
                    "arguments": "{\"message\":\"hi\"}",
                },
            ),
            usage_metadata={
                "prompt_tokens": 9,
                "completion_tokens": 4,
                "total_tokens": 13,
            },
            done=True,
        ),
        OpenAICompatibleStreamChunk(done=True),
    ]


@pytest.mark.asyncio
async def test_openai_compatible_chat_client_preserves_malformed_tool_args() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-bad",
                                    "type": "function",
                                    "function": {
                                        "name": "send_message",
                                        "arguments": "{broken json",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleChatClient(
        provider="ollama",
        model="llama3.2",
        base_url="http://ollama.local/v1",
        transport=httpx.MockTransport(handler),
    )

    response = await client.ainvoke([HumanMessage(content="hello")])

    assert response.tool_calls == (
        {
            "id": "call-bad",
            "name": "send_message",
            "args": {},
            "parse_error": "Malformed tool-call arguments (invalid JSON).",
            "raw_arguments": "{broken json",
        },
    )
