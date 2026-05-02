from __future__ import annotations

import asyncio
from typing import Any

import pytest
from anima_server.services.agent.client_actions import (
    ActionToolConnection,
    ClientActionRegistry,
    build_client_action_runtime,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


def _connection(
    *,
    user_id: int,
    username: str,
    websocket: FakeWebSocket,
    tools: list[dict[str, Any]],
) -> ActionToolConnection:
    return ActionToolConnection(
        websocket=websocket,
        user_id=user_id,
        username=username,
        action_tool_schemas=tools,
    )


@pytest.mark.asyncio
async def test_action_runtime_routes_tool_execute_to_registered_client() -> None:
    registry = ClientActionRegistry()
    chat_socket = FakeWebSocket()
    animus_socket = FakeWebSocket()
    chat_conn = _connection(
        user_id=1,
        username="desktop",
        websocket=chat_socket,
        tools=[],
    )
    animus_conn = _connection(
        user_id=1,
        username="animus",
        websocket=animus_socket,
        tools=[
            {
                "name": "bash",
                "description": "Run shell commands",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )
    registry.add(chat_conn)
    registry.add(animus_conn)

    runtime = build_client_action_runtime(1, registry=registry)
    assert runtime is not None

    task = asyncio.create_task(
        runtime.delegate("call-1", "bash", {"command": "pwd"})
    )
    await asyncio.sleep(0)

    assert chat_socket.sent == []
    assert animus_socket.sent == [
        {
            "type": "tool_execute",
            "tool_call_id": "call-1",
            "tool_name": "bash",
            "args": {"command": "pwd"},
        }
    ]

    resolved = registry.resolve_tool_result(
        "call-1",
        {
            "tool_call_id": "call-1",
            "tool_name": "bash",
            "status": "success",
            "result": "ok",
        },
    )

    assert resolved is True
    result = await task
    assert result.name == "bash"
    assert result.output == "ok"
    runtime.close()


def test_action_runtime_is_absent_without_registered_tools() -> None:
    registry = ClientActionRegistry()
    registry.add(
        _connection(
            user_id=1,
            username="desktop",
            websocket=FakeWebSocket(),
            tools=[],
        )
    )

    assert build_client_action_runtime(1, registry=registry) is None
