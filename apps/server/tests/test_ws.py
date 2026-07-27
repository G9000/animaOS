from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

import pytest
from anima_server.api.routes import ws as ws_route
from anima_server.services.agent.client_actions import ActionToolConnection
from anima_server.services.agent.runtime_types import ToolExecutionResult
from anima_server.services.agent.state import AgentResult
from anima_server.services.agent.streaming import (
    AgentStreamEvent,
    build_approval_pending_event,
    build_cancelled_event,
    build_done_event,
    build_error_event,
    build_run_started_event,
    build_tool_return_event,
)
from conftest import managed_test_client
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _register_user(
    client: TestClient,
    *,
    username: str = "alice",
    password: str = "pw123456",
    name: str = "Alice",
) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "name": name},
    )
    assert response.status_code == 201
    return response.json()


class TestWebSocketAuth:
    """Tests for WebSocket /ws/agent endpoint authentication."""

    def test_ws_auth_with_valid_token(self) -> None:
        """Client sends auth message with unlockToken, server responds auth_ok."""
        with managed_test_client("anima-ws-test-") as client:
            user = _register_user(client)
            unlock_token = str(user["unlockToken"])
            user_id = int(user["id"])

            with client.websocket_connect("/ws/agent") as ws:
                ws.send_json({"type": "auth", "unlockToken": unlock_token})
                response = ws.receive_json()
                assert response["type"] == "auth_ok"
                assert "user" in response
                assert response["user"]["id"] == user_id

    def test_ws_auth_rejected_without_token(self) -> None:
        """Client that sends non-auth message first gets error."""
        with managed_test_client("anima-ws-test-") as client:
            _register_user(client)

            with client.websocket_connect("/ws/agent") as ws:
                ws.send_json({"type": "user_message", "message": "hello"})
                response = ws.receive_json()
                assert response["type"] == "error"
                assert "auth" in response["message"].lower()

    def test_ws_auth_rejected_with_invalid_token(self) -> None:
        """Client with invalid unlock token gets auth error."""
        with managed_test_client("anima-ws-test-") as client:
            _register_user(client)

            with client.websocket_connect("/ws/agent") as ws:
                ws.send_json({"type": "auth", "unlockToken": "bogus-token"})
                response = ws.receive_json()
                assert response["type"] == "error"
                assert response["code"] == "AUTH_FAILED"

    def test_ws_tool_schemas_registration(self) -> None:
        """Client can register action tool schemas after auth."""
        with managed_test_client("anima-ws-test-") as client:
            user = _register_user(client)
            unlock_token = str(user["unlockToken"])

            with client.websocket_connect("/ws/agent") as ws:
                ws.send_json({"type": "auth", "unlockToken": unlock_token})
                auth_resp = ws.receive_json()
                assert auth_resp["type"] == "auth_ok"

                ws.send_json(
                    {
                        "type": "tool_schemas",
                        "tools": [{"name": "bash", "description": "Run shell", "parameters": {}}],
                    }
                )
                # No response expected for tool_schemas — verify no error by
                # sending another message and confirming the connection is still alive.
                ws.send_json({"type": "ping"})
                # Connection should still be open (no error response expected).


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class _DisconnectingWebSocket(_FakeWebSocket):
    async def send_json(self, payload: dict[str, Any]) -> None:
        del payload
        raise WebSocketDisconnect()


class _QueueWebSocket(_FakeWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.accepted = False
        self.incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict[str, Any]:
        message = await self.incoming.get()
        if message is None:
            raise WebSocketDisconnect()
        return message


class _TrackingUnlockStore:
    def __init__(self) -> None:
        self.created: list[tuple[int, dict[str, bytes], object | None]] = []
        self.revoked: list[str | None] = []

    async def create_async(
        self,
        user_id: int,
        deks: dict[str, bytes],
        *,
        corefs_keys: object | None = None,
    ) -> str:
        self.created.append((user_id, deks, corefs_keys))
        return "password-owned-token"

    async def revoke_async(self, token: str | None) -> None:
        self.revoked.append(token)


class _FakeScalarResult:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def all(self) -> list[Any]:
        return self._values


class _FakeDb:
    def __init__(self, run: Any | None = None) -> None:
        self.run = run
        self.closed = False

    def get(self, model: object, row_id: int) -> Any | None:
        return self.run if self.run is not None and self.run.id == row_id else None

    def scalar(self, statement: object) -> int | None:
        del statement
        if self.run is not None and self.run.status == "awaiting_approval":
            return int(self.run.id)
        return None

    def scalars(self, statement: object) -> _FakeScalarResult:
        del statement
        if self.run is not None and self.run.status == "awaiting_approval":
            return _FakeScalarResult([self.run])
        return _FakeScalarResult([])

    def close(self) -> None:
        self.closed = True


class _FakeRun:
    def __init__(
        self,
        *,
        run_id: int,
        user_id: int,
        status: str = "awaiting_approval",
        pending_approval_message_id: int | None = None,
    ) -> None:
        self.id = run_id
        self.user_id = user_id
        self.status = status
        self.pending_approval_message_id = pending_approval_message_id


class TestWebSocketFrameTranslation:
    def test_translate_event_maps_approval_pending_to_approval_required(self) -> None:
        event = build_approval_pending_event(
            run_id=42,
            tool_name="bash",
            tool_call_id="call-1",
            tool_arguments={"command": "git status"},
        )

        assert ws_route._translate_event(event) == {
            "type": "approval_required",
            "run_id": 42,
            "tool_call_id": "call-1",
            "tool_name": "bash",
            "args": {"command": "git status"},
        }

    def test_translate_event_maps_run_cancel_done_tool_return_and_error_frames(self) -> None:
        run_started = build_run_started_event(run_id=7, thread_id=9)
        cancelled = build_cancelled_event(7)
        done = build_done_event(
            AgentResult(
                response="ok",
                model="model-a",
                provider="provider-a",
                tools_used=("bash",),
            ),
            thread_id=9,
        )
        tool_return = build_tool_return_event(
            0,
            ToolExecutionResult(
                call_id="call-1",
                name="bash",
                output="denied",
                is_error=True,
            ),
        )
        error = build_error_event("boom")

        assert ws_route._translate_event(run_started) == {
            "type": "run_started",
            "run_id": 7,
            "thread_id": 9,
        }
        assert ws_route._translate_event(cancelled) == {
            "type": "cancelled",
            "run_id": 7,
        }
        assert ws_route._translate_event(done) == {
            "type": "turn_complete",
            "response": "",
            "model": "model-a",
            "provider": "provider-a",
            "tools_used": ["bash"],
        }
        assert ws_route._translate_event(tool_return) == {
            "type": "tool_return",
            "tool_call_id": "call-1",
            "tool_name": "bash",
            "result": "denied",
            "is_error": True,
        }
        assert ws_route._translate_event(error) == {
            "type": "error",
            "message": "boom",
            "code": "AGENT_ERROR",
        }


class TestWebSocketRunHandlers:
    @pytest.mark.asyncio
    async def test_password_auth_returns_owned_unlock_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_ws = _QueueWebSocket()
        store = _TrackingUnlockStore()
        deks = {"memories": b"a" * 32}
        corefs_keys = object()
        await fake_ws.incoming.put(
            {"type": "auth", "username": "alice", "password": "pw123456"}
        )

        monkeypatch.setattr(ws_route, "unlock_session_store", store)
        monkeypatch.setattr(
            ws_route,
            "authenticate_account",
            lambda username, password: (
                {"id": 5, "username": username},
                deks,
                corefs_keys,
            ),
        )

        authenticated = await ws_route._authenticate(fake_ws)  # type: ignore[arg-type]

        assert authenticated is not None
        connection, owned_token = authenticated
        assert connection.user_id == 5
        assert connection.username == "alice"
        assert owned_token == "password-owned-token"
        assert store.created == [(5, deks, corefs_keys)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("owned_token", "expected_revocations"),
        [
            ("password-owned-token", ["password-owned-token"]),
            (None, []),
        ],
    )
    async def test_ws_agent_only_revokes_connection_owned_unlock_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
        owned_token: str | None,
        expected_revocations: list[str],
    ) -> None:
        fake_ws = _QueueWebSocket()
        store = _TrackingUnlockStore()
        connection = ActionToolConnection(
            websocket=fake_ws,
            user_id=5,
            username="alice",
        )

        async def fake_authenticate(
            websocket: Any,
        ) -> tuple[ActionToolConnection, str | None]:
            assert websocket is fake_ws
            return connection, owned_token

        monkeypatch.setattr(ws_route, "_authenticate", fake_authenticate)
        monkeypatch.setattr(ws_route, "unlock_session_store", store)
        monkeypatch.setattr(ws_route, "_pending_approval_frames", lambda _user_id: [])

        await fake_ws.incoming.put(None)
        await ws_route.ws_agent(fake_ws)  # type: ignore[arg-type]

        assert store.revoked == expected_revocations

    @pytest.mark.asyncio
    async def test_user_message_disconnect_closes_service_stream(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        closed = asyncio.Event()
        user_db = _FakeDb()
        runtime_db = _FakeDb()

        async def tracked_stream(*_args, **_kwargs) -> AsyncGenerator[AgentStreamEvent, None]:
            try:
                yield AgentStreamEvent(event="chunk", data={"content": "hello"})
                await asyncio.Event().wait()
            finally:
                closed.set()

        import anima_server.db.runtime as runtime_module
        import anima_server.services.agent.service as service_module

        monkeypatch.setattr(
            ws_route,
            "get_user_session_factory",
            lambda _user_id: lambda: user_db,
        )
        monkeypatch.setattr(
            runtime_module,
            "get_runtime_session_factory",
            lambda: lambda: runtime_db,
        )
        monkeypatch.setattr(service_module, "stream_agent", tracked_stream)

        conn = ActionToolConnection(
            websocket=_DisconnectingWebSocket(),
            user_id=5,
            username="alice",
        )
        with pytest.raises(WebSocketDisconnect):
            await ws_route._handle_user_message(conn, {"message": "hello"})

        assert closed.is_set()
        assert user_db.closed is True
        assert runtime_db.closed is True

    @pytest.mark.asyncio
    async def test_approval_disconnect_closes_service_stream(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        closed = asyncio.Event()
        user_db = _FakeDb()
        runtime_db = _FakeDb(_FakeRun(run_id=42, user_id=5))

        async def tracked_stream(*_args, **_kwargs) -> AsyncGenerator[AgentStreamEvent, None]:
            try:
                yield AgentStreamEvent(event="chunk", data={"content": "resumed"})
                await asyncio.Event().wait()
            finally:
                closed.set()

        import anima_server.db.runtime as runtime_module
        import anima_server.services.agent.service as service_module

        monkeypatch.setattr(
            ws_route,
            "get_user_session_factory",
            lambda _user_id: lambda: user_db,
        )
        monkeypatch.setattr(
            runtime_module,
            "get_runtime_session_factory",
            lambda: lambda: runtime_db,
        )
        monkeypatch.setattr(service_module, "stream_approve_or_deny", tracked_stream)

        conn = ActionToolConnection(
            websocket=_DisconnectingWebSocket(),
            user_id=5,
            username="alice",
        )
        with pytest.raises(WebSocketDisconnect):
            await ws_route._handle_approval_response(
                conn,
                {"run_id": 42, "approved": True},
            )

        assert closed.is_set()
        assert user_db.closed is True
        assert runtime_db.closed is True

    @pytest.mark.asyncio
    async def test_ws_agent_replays_pending_approval_after_auth(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_ws = _QueueWebSocket()
        pending_frame = {
            "type": "approval_required",
            "run_id": 42,
            "tool_call_id": "call-1",
            "tool_name": "bash",
            "args": {"command": "git status"},
        }

        async def fake_authenticate(
            websocket: Any,
        ) -> tuple[ActionToolConnection, None]:
            return (
                ActionToolConnection(
                    websocket=websocket,
                    user_id=5,
                    username="alice",
                ),
                None,
            )

        monkeypatch.setattr(ws_route, "_authenticate", fake_authenticate)
        monkeypatch.setattr(
            ws_route,
            "_pending_approval_frames",
            lambda user_id: [pending_frame] if user_id == 5 else [],
        )

        task = asyncio.create_task(ws_route.ws_agent(fake_ws))  # type: ignore[arg-type]
        try:
            for _ in range(20):
                if len(fake_ws.sent) >= 2:
                    break
                await asyncio.sleep(0.01)
        finally:
            await fake_ws.incoming.put(None)
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task

        assert fake_ws.sent[:2] == [
            {
                "type": "auth_ok",
                "user": {"id": 5, "username": "alice"},
            },
            pending_frame,
        ]

    @pytest.mark.asyncio
    async def test_ws_agent_processes_cancel_while_approval_resume_streams(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_ws = _QueueWebSocket()
        approval_started = asyncio.Event()
        release_approval = asyncio.Event()
        cancel_called = asyncio.Event()

        async def fake_authenticate(
            websocket: Any,
        ) -> tuple[ActionToolConnection, None]:
            return (
                ActionToolConnection(
                    websocket=websocket,
                    user_id=5,
                    username="alice",
                ),
                None,
            )

        async def fake_approval_response(
            conn: ActionToolConnection,
            data: dict[str, Any],
        ) -> None:
            approval_started.set()
            await release_approval.wait()

        async def fake_cancel(conn: ActionToolConnection, data: dict[str, Any]) -> None:
            cancel_called.set()
            raise WebSocketDisconnect()

        monkeypatch.setattr(ws_route, "_authenticate", fake_authenticate)
        monkeypatch.setattr(ws_route, "_handle_approval_response", fake_approval_response)
        monkeypatch.setattr(ws_route, "_handle_cancel", fake_cancel)

        task = asyncio.create_task(ws_route.ws_agent(fake_ws))  # type: ignore[arg-type]
        try:
            await fake_ws.incoming.put(
                {"type": "approval_response", "run_id": 42, "approved": True}
            )
            await asyncio.wait_for(approval_started.wait(), timeout=1)

            await fake_ws.incoming.put({"type": "cancel", "run_id": 42})

            await asyncio.wait_for(cancel_called.wait(), timeout=1)
        finally:
            release_approval.set()
            await fake_ws.incoming.put(None)
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task

    @pytest.mark.asyncio
    async def test_ws_agent_rejects_user_message_while_run_awaits_approval(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_ws = _QueueWebSocket()
        runtime_db = _FakeDb(_FakeRun(run_id=42, user_id=5, status="running"))
        handled: list[str] = []
        first_handled = asyncio.Event()
        second_handled = asyncio.Event()

        async def fake_authenticate(
            websocket: Any,
        ) -> tuple[ActionToolConnection, None]:
            return (
                ActionToolConnection(
                    websocket=websocket,
                    user_id=5,
                    username="alice",
                ),
                None,
            )

        async def fake_user_message(
            conn: ActionToolConnection,
            data: dict[str, Any],
        ) -> None:
            del conn
            message = str(data["message"])
            handled.append(message)
            if message == "first":
                runtime_db.run.status = "awaiting_approval"
                first_handled.set()
            if message == "second":
                second_handled.set()

        import anima_server.db.runtime as runtime_module

        monkeypatch.setattr(ws_route, "_authenticate", fake_authenticate)
        monkeypatch.setattr(ws_route, "_handle_user_message", fake_user_message)
        monkeypatch.setattr(
            runtime_module,
            "get_runtime_session_factory",
            lambda: lambda: runtime_db,
        )

        task = asyncio.create_task(ws_route.ws_agent(fake_ws))  # type: ignore[arg-type]
        try:
            await fake_ws.incoming.put({"type": "user_message", "message": "first"})
            await asyncio.wait_for(first_handled.wait(), timeout=1)

            await fake_ws.incoming.put({"type": "user_message", "message": "second"})
            for _ in range(20):
                if second_handled.is_set() or any(
                    frame.get("code") == "BUSY" for frame in fake_ws.sent
                ):
                    break
                await asyncio.sleep(0.01)
        finally:
            await fake_ws.incoming.put(None)
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task

        assert handled == ["first"]
        assert fake_ws.sent[-1] == {
            "type": "error",
            "message": "Turn already awaiting approval",
            "code": "BUSY",
        }
        assert runtime_db.closed is True

    @pytest.mark.asyncio
    async def test_handle_approval_response_streams_translated_resume_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_db = _FakeDb()
        runtime_db = _FakeDb(_FakeRun(run_id=42, user_id=5))
        calls: list[dict[str, Any]] = []

        monkeypatch.setattr(
            ws_route,
            "get_user_session_factory",
            lambda user_id: lambda: user_db,
        )

        import anima_server.db.runtime as runtime_module
        import anima_server.services.agent.service as service_module

        monkeypatch.setattr(
            runtime_module,
            "get_runtime_session_factory",
            lambda: lambda: runtime_db,
        )

        async def fake_stream_approve_or_deny(
            run_id: int,
            user_id: int,
            approved: bool,
            db: Any,
            runtime: Any,
            *,
            denial_reason: str | None = None,
        ) -> AsyncGenerator[AgentStreamEvent, None]:
            calls.append(
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "approved": approved,
                    "db": db,
                    "runtime_db": runtime,
                    "denial_reason": denial_reason,
                }
            )
            yield AgentStreamEvent(event="chunk", data={"content": "resumed"})
            yield build_done_event(
                AgentResult(response="ok", model="model-a", provider="provider-a"),
                thread_id=9,
            )

        monkeypatch.setattr(
            service_module,
            "stream_approve_or_deny",
            fake_stream_approve_or_deny,
        )

        fake_ws = _FakeWebSocket()
        conn = ActionToolConnection(websocket=fake_ws, user_id=5, username="alice")

        await ws_route._handle_approval_response(
            conn,
            {
                "type": "approval_response",
                "run_id": 42,
                "tool_call_id": "call-1",
                "approved": False,
                "reason": "nope",
            },
        )

        assert calls == [
            {
                "run_id": 42,
                "user_id": 5,
                "approved": False,
                "db": user_db,
                "runtime_db": runtime_db,
                "denial_reason": "nope",
            }
        ]
        assert fake_ws.sent == [
            {"type": "stream_token", "token": "resumed"},
            {
                "type": "turn_complete",
                "response": "",
                "model": "model-a",
                "provider": "provider-a",
                "tools_used": [],
            },
        ]
        assert user_db.closed is True
        assert runtime_db.closed is True

    @pytest.mark.asyncio
    async def test_handle_approval_response_rejects_non_numeric_run_id(self) -> None:
        fake_ws = _FakeWebSocket()
        conn = ActionToolConnection(websocket=fake_ws, user_id=5, username="alice")

        await ws_route._handle_approval_response(
            conn,
            {"type": "approval_response", "run_id": "not-a-number", "approved": True},
        )

        assert fake_ws.sent == [
            {
                "type": "error",
                "message": "approval_response requires a numeric run_id",
                "code": "BAD_REQUEST",
            }
        ]

    @pytest.mark.asyncio
    async def test_handle_cancel_sends_cancelled_frame_after_successful_cancel(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runtime_db = _FakeDb(_FakeRun(run_id=42, user_id=5, status="running"))
        calls: list[tuple[int, int, Any]] = []

        import anima_server.db.runtime as runtime_module
        import anima_server.services.agent.service as service_module

        monkeypatch.setattr(
            runtime_module,
            "get_runtime_session_factory",
            lambda: lambda: runtime_db,
        )

        async def fake_cancel_agent_run(run_id: int, user_id: int, runtime: Any) -> Any:
            calls.append((run_id, user_id, runtime))
            return runtime_db.run

        monkeypatch.setattr(service_module, "cancel_agent_run", fake_cancel_agent_run)

        fake_ws = _FakeWebSocket()
        conn = ActionToolConnection(websocket=fake_ws, user_id=5, username="alice")

        await ws_route._handle_cancel(conn, {"type": "cancel", "run_id": 42})

        assert calls == [(42, 5, runtime_db)]
        assert fake_ws.sent == [{"type": "cancelled", "run_id": 42}]
        assert runtime_db.closed is True
