from __future__ import annotations

import json

import httpx
import pytest
from anima_server.services.agent.anthropic_client import (
    AnthropicChatClient,
    AnthropicStreamChunk,
)
from anima_server.services.agent.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from anima_server.services.agent.tools import send_message


@pytest.mark.asyncio
async def test_anthropic_chat_client_serializes_messages_tools_and_usage() -> None:
    captured_payload: dict[str, object] = {}
    captured_headers: httpx.Headers | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload, captured_headers
        captured_headers = request.headers
        captured_payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-4-5-20251001",
                "content": [
                    {"type": "text", "text": "done"},
                    {
                        "type": "tool_use",
                        "id": "toolu-1",
                        "name": "send_message",
                        "input": {"message": "done"},
                    },
                ],
                "usage": {"input_tokens": 11, "output_tokens": 7},
            },
        )

    client = AnthropicChatClient(
        model="claude-haiku-4-5-20251001",
        base_url="https://anthropic.test/v1",
        headers={"x-api-key": "test-key"},
        transport=httpx.MockTransport(handler),
        max_tokens=1024,
    ).bind_tools([send_message], tool_choice="required")

    response = await client.ainvoke(
        [
            SystemMessage(content="system prompt"),
            HumanMessage(content="hello"),
            AIMessage(
                content="partial",
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

    assert captured_headers is not None
    assert captured_headers["x-api-key"] == "test-key"
    assert captured_headers["anthropic-version"] == "2023-06-01"
    assert captured_payload["model"] == "claude-haiku-4-5-20251001"
    assert captured_payload["max_tokens"] == 1024
    assert captured_payload["system"] == "system prompt"
    assert captured_payload["tool_choice"] == {"type": "tool", "name": "send_message"}
    assert captured_payload["messages"] == [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "partial"},
                {
                    "type": "tool_use",
                    "id": "call-0",
                    "name": "send_message",
                    "input": {"message": "partial"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-0",
                    "content": "partial",
                }
            ],
        },
    ]
    tools = captured_payload["tools"]
    assert isinstance(tools, list)
    assert tools[0]["name"] == "send_message"
    assert tools[0]["input_schema"]["type"] == "object"
    assert response.content == "done"
    assert response.tool_calls == (
        {
            "id": "toolu-1",
            "name": "send_message",
            "args": {"message": "done"},
        },
    )
    assert response.usage_metadata == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }


@pytest.mark.asyncio
async def test_anthropic_chat_client_streams_content_tool_calls_and_usage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["stream"] is True
        body = "\n\n".join(
            [
                "event: message_start\n"
                + "data: "
                + json.dumps(
                    {
                        "type": "message_start",
                        "message": {"usage": {"input_tokens": 9}},
                    }
                ),
                "event: content_block_delta\n"
                + "data: "
                + json.dumps(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "hel"},
                    }
                ),
                "event: content_block_start\n"
                + "data: "
                + json.dumps(
                    {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu-1",
                            "name": "send_message",
                            "input": {},
                        },
                    }
                ),
                "event: content_block_delta\n"
                + "data: "
                + json.dumps(
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"message":"hi"}',
                        },
                    }
                ),
                "event: message_delta\n"
                + "data: "
                + json.dumps(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 4},
                    }
                ),
                "event: message_stop\n" + "data: " + json.dumps({"type": "message_stop"}),
            ]
        )
        return httpx.Response(
            200,
            content=(body + "\n\n").encode("utf-8"),
            headers={"Content-Type": "text/event-stream"},
        )

    client = AnthropicChatClient(
        model="claude-haiku-4-5-20251001",
        base_url="https://anthropic.test/v1",
        headers={"x-api-key": "test-key"},
        transport=httpx.MockTransport(handler),
    )

    chunks = [chunk async for chunk in client.astream([HumanMessage(content="hello")])]

    assert chunks == [
        AnthropicStreamChunk(usage_metadata={"input_tokens": 9}),
        AnthropicStreamChunk(content_delta="hel"),
        AnthropicStreamChunk(
            tool_call_deltas=(
                {"index": 1, "id": "toolu-1", "name": "send_message", "arguments": ""},
            )
        ),
        AnthropicStreamChunk(
            tool_call_deltas=(
                {
                    "index": 1,
                    "id": None,
                    "name": None,
                    "arguments": '{"message":"hi"}',
                },
            )
        ),
        AnthropicStreamChunk(
            usage_metadata={"output_tokens": 4},
            done=True,
        ),
        AnthropicStreamChunk(done=True),
    ]
