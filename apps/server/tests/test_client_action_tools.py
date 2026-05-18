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
        animus_conn,
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


@pytest.mark.asyncio
async def test_action_runtime_scopes_duplicate_call_ids_by_connection() -> None:
    registry = ClientActionRegistry()
    socket_one = FakeWebSocket()
    socket_two = FakeWebSocket()
    conn_one = _connection(
        user_id=1,
        username="one",
        websocket=socket_one,
        tools=[
            {
                "name": "bash",
                "description": "Run shell commands",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )
    conn_two = _connection(
        user_id=2,
        username="two",
        websocket=socket_two,
        tools=[
            {
                "name": "bash",
                "description": "Run shell commands",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )
    registry.add(conn_one)
    registry.add(conn_two)

    runtime_one = build_client_action_runtime(1, registry=registry)
    runtime_two = build_client_action_runtime(2, registry=registry)
    assert runtime_one is not None
    assert runtime_two is not None

    task_one = asyncio.create_task(
        runtime_one.delegate("same-call-id", "bash", {"command": "one"})
    )
    task_two = asyncio.create_task(
        runtime_two.delegate("same-call-id", "bash", {"command": "two"})
    )
    await asyncio.sleep(0)

    assert registry.resolve_tool_result(
        conn_one,
        "same-call-id",
        {
            "tool_call_id": "same-call-id",
            "tool_name": "bash",
            "status": "success",
            "result": "one-result",
        },
    )
    assert registry.resolve_tool_result(
        conn_two,
        "same-call-id",
        {
            "tool_call_id": "same-call-id",
            "tool_name": "bash",
            "status": "success",
            "result": "two-result",
        },
    )

    result_one = await task_one
    result_two = await task_two
    assert result_one.output == "one-result"
    assert result_two.output == "two-result"


@pytest.mark.asyncio
async def test_action_runtime_scopes_duplicate_call_ids_on_same_connection() -> None:
    registry = ClientActionRegistry()
    socket = FakeWebSocket()
    conn = _connection(
        user_id=1,
        username="animus",
        websocket=socket,
        tools=[
            {
                "name": "bash",
                "description": "Run shell commands",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )
    registry.add(conn)

    runtime = build_client_action_runtime(1, registry=registry)
    assert runtime is not None

    task_one = asyncio.create_task(
        runtime.delegate("same-call-id", "bash", {"command": "one"})
    )
    task_two = asyncio.create_task(
        runtime.delegate("same-call-id", "bash", {"command": "two"})
    )
    await asyncio.sleep(0)

    assert len(socket.sent) == 2
    wire_call_id_one = str(socket.sent[0]["tool_call_id"])
    wire_call_id_two = str(socket.sent[1]["tool_call_id"])
    assert wire_call_id_one != wire_call_id_two

    assert registry.resolve_tool_result(
        conn,
        wire_call_id_one,
        {
            "tool_call_id": wire_call_id_one,
            "tool_name": "bash",
            "status": "success",
            "result": "one-result",
        },
    )
    assert registry.resolve_tool_result(
        conn,
        wire_call_id_two,
        {
            "tool_call_id": wire_call_id_two,
            "tool_name": "bash",
            "status": "success",
            "result": "two-result",
        },
    )

    result_one = await task_one
    result_two = await task_two
    assert result_one.call_id == "same-call-id"
    assert result_two.call_id == "same-call-id"
    assert result_one.output == "one-result"
    assert result_two.output == "two-result"


@pytest.mark.asyncio
async def test_action_runtime_reports_mismatched_result_tool_as_error() -> None:
    registry = ClientActionRegistry()
    socket = FakeWebSocket()
    conn = _connection(
        user_id=1,
        username="animus",
        websocket=socket,
        tools=[
            {
                "name": "bash",
                "description": "Run shell commands",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )
    registry.add(conn)

    runtime = build_client_action_runtime(1, registry=registry)
    assert runtime is not None

    task = asyncio.create_task(
        runtime.delegate("call-1", "bash", {"command": "pwd"})
    )
    await asyncio.sleep(0)

    assert registry.resolve_tool_result(
        conn,
        "call-1",
        {
            "tool_call_id": "call-1",
            "tool_name": "python",
            "status": "success",
            "result": "wrong tool",
        },
    )

    result = await task
    assert result.name == "bash"
    assert result.is_error is True
    assert "did not match" in result.output


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
