from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from anima_server.db.session import get_user_session_factory
from anima_server.db.user_store import authenticate_account
from anima_server.models.user import User
from anima_server.services.sessions import unlock_session_store

logger = logging.getLogger(__name__)

router = APIRouter()


@dataclass
class ClientConnection:
    """A connected WebSocket client."""

    websocket: WebSocket
    user_id: int
    username: str
    action_tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    connected_at: float = 0.0
    _delegator: Any = field(default=None, repr=False)  # ToolDelegator, set per-turn


class ConnectionRegistry:
    """Tracks connected WebSocket clients per user."""

    def __init__(self) -> None:
        self._connections: dict[int, list[ClientConnection]] = {}

    def add(self, conn: ClientConnection) -> None:
        self._connections.setdefault(conn.user_id, []).append(conn)

    def remove(self, conn: ClientConnection) -> None:
        conns = self._connections.get(conn.user_id, [])
        if conn in conns:
            conns.remove(conn)
        if not conns:
            self._connections.pop(conn.user_id, None)

    def get_delegate(self, user_id: int, tool_name: str) -> ClientConnection | None:
        """Get the client that should execute a tool. Most recently connected wins."""
        conns = self._connections.get(user_id, [])
        for conn in reversed(conns):
            tool_names = [t.get("name", "") for t in conn.action_tool_schemas]
            if tool_name in tool_names:
                return conn
        return None

    def get_connections(self, user_id: int) -> list[ClientConnection]:
        return list(self._connections.get(user_id, []))

    def has_connections(self, user_id: int) -> bool:
        return bool(self._connections.get(user_id))

    def get_action_tool_schemas(self, user_id: int) -> list[dict[str, Any]]:
        """Get merged action tool schemas from all connections for a user."""
        seen: set[str] = set()
        schemas: list[dict[str, Any]] = []
        for conn in reversed(self._connections.get(user_id, [])):
            for schema in conn.action_tool_schemas:
                name = schema.get("name", "")
                if name not in seen:
                    seen.add(name)
                    schemas.append(schema)
        return schemas

    def get_action_tool_names(self, user_id: int) -> frozenset[str]:
        """Get names of all action tools registered by a user's connections."""
        return frozenset(s.get("name", "") for s in self.get_action_tool_schemas(user_id))


# Global singleton
registry = ConnectionRegistry()


async def _authenticate(ws: WebSocket) -> ClientConnection | None:
    """Wait for auth message, validate, return connection or None."""
    try:
        raw = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
    except (TimeoutError, WebSocketDisconnect):
        return None

    if raw.get("type") != "auth":
        await ws.send_json(
            {
                "type": "error",
                "message": "Expected auth message first",
                "code": "AUTH_REQUIRED",
            }
        )
        return None

    unlock_token = raw.get("unlockToken")
    username = raw.get("username")
    password = raw.get("password")

    # Try token-based auth first
    if unlock_token:
        session = unlock_session_store.resolve(unlock_token)
        if session is None:
            await ws.send_json(
                {
                    "type": "error",
                    "message": "Invalid unlock token",
                    "code": "AUTH_FAILED",
                }
            )
            return None
        # Look up username from DB
        db = get_user_session_factory(session.user_id)()
        try:
            user = db.get(User, session.user_id)
            resolved_username = user.username if user else (username or "")
        finally:
            db.close()
        return ClientConnection(
            websocket=ws,
            user_id=session.user_id,
            username=resolved_username,
            connected_at=time.monotonic(),
        )

    # Try username/password auth
    if username and password:
        try:
            response, deks = authenticate_account(username, password)
            user_id = int(response["id"])
            unlock_session_store.create(user_id, deks)
            return ClientConnection(
                websocket=ws,
                user_id=user_id,
                username=str(response.get("username", username)),
                connected_at=time.monotonic(),
            )
        except ValueError:
            await ws.send_json(
                {
                    "type": "error",
                    "message": "Invalid credentials",
                    "code": "AUTH_FAILED",
                }
            )
            return None
        except Exception:
            logger.exception("Unexpected error during password authentication")
            await ws.send_json(
                {
                    "type": "error",
                    "message": "Authentication error",
                    "code": "AUTH_FAILED",
                }
            )
            return None

    await ws.send_json(
        {
            "type": "error",
            "message": "Provide unlockToken or username/password",
            "code": "AUTH_REQUIRED",
        }
    )
    return None


@router.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket) -> None:
    await websocket.accept()
    conn = await _authenticate(websocket)
    if conn is None:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    registry.add(conn)
    await websocket.send_json(
        {
            "type": "auth_ok",
            "user": {"id": conn.user_id, "username": conn.username},
        }
    )

    logger.info("WebSocket client connected: user_id=%d", conn.user_id)

    turn_task: asyncio.Task[None] | None = None

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "tool_schemas":
                conn.action_tool_schemas = data.get("tools", [])
                names = [t.get("name", "") for t in conn.action_tool_schemas]
                logger.info(
                    "Client registered %d action tools: %s",
                    len(conn.action_tool_schemas),
                    names,
                )

            elif msg_type == "user_message":
                # Reject if a turn is already in progress — the reader
                # loop must stay free to receive tool_result messages.
                if turn_task is not None and not turn_task.done():
                    await conn.websocket.send_json(
                        {
                            "type": "error",
                            "message": "Turn already in progress",
                            "code": "BUSY",
                        }
                    )
                    continue
                turn_task = asyncio.create_task(
                    _handle_user_message(conn, data),
                )

            elif msg_type == "tool_result":
                _handle_tool_result(conn, data)

            elif msg_type == "approval_response":
                await _handle_approval_response(conn, data)

            elif msg_type == "cancel":
                await _handle_cancel(conn, data)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected: user_id=%d", conn.user_id)
    finally:
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
        if conn._delegator:
            conn._delegator.cancel_all("Client disconnected")
        registry.remove(conn)


async def _handle_user_message(conn: ClientConnection, data: dict) -> None:
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.services.agent.delegation import ToolDelegator
    from anima_server.services.agent.service import stream_agent

    message = data.get("message", "")
    delegator = ToolDelegator(send_fn=lambda msg: conn.websocket.send_json(msg))
    conn._delegator = delegator

    action_tool_names = registry.get_action_tool_names(conn.user_id)
    action_tool_schemas = registry.get_action_tool_schemas(conn.user_id)
    logger.info(
        "Handling user message: %d action tools registered (%s)",
        len(action_tool_schemas),
        ", ".join(action_tool_names),
    )

    db = get_user_session_factory(conn.user_id)()
    runtime_db = get_runtime_session_factory()()
    try:
        async for event in stream_agent(
            message,
            conn.user_id,
            db,
            runtime_db,
            tool_delegate=delegator.delegate,
            delegated_tool_names=action_tool_names,
            extra_tool_schemas=action_tool_schemas,
        ):
            ws_msg = _translate_event(event)
            if ws_msg is not None:
                await conn.websocket.send_json(ws_msg)
    except Exception as exc:
        logger.exception("Agent error for user_id=%d", conn.user_id)
        await conn.websocket.send_json(
            {
                "type": "error",
                "message": str(exc),
                "code": "AGENT_ERROR",
            }
        )
    finally:
        runtime_db.close()
        db.close()
        conn._delegator = None


def _handle_tool_result(conn: ClientConnection, data: dict) -> None:
    if conn._delegator:
        conn._delegator.resolve(data.get("tool_call_id", ""), data)


async def _handle_approval_response(conn: ClientConnection, data: dict) -> None:
    pass


async def _handle_cancel(conn: ClientConnection, data: dict) -> None:
    pass


def _translate_event(event: Any) -> dict[str, Any] | None:
    """Translate server AgentStreamEvent into CLI protocol message.

    Returns None for events the CLI doesn't need (thought, timing,
    step_state, usage, warning).
    """
    etype = event.event
    data = event.data

    if etype == "chunk":
        return {
            "type": "stream_token",
            "token": data.get("content", ""),
        }

    if etype == "done":
        return {
            "type": "turn_complete",
            "response": "",
            "model": data.get("model", ""),
            "provider": data.get("provider", ""),
            "tools_used": data.get("toolsUsed", []),
        }

    if etype == "error":
        return {
            "type": "error",
            "message": data.get("error", "Unknown error"),
            "code": "AGENT_ERROR",
        }

    if etype == "reasoning":
        return {
            "type": "reasoning",
            "content": data.get("content", ""),
        }

    if etype == "tool_call":
        return {
            "type": "tool_call",
            "tool_call_id": data.get("id", ""),
            "tool_name": data.get("name", ""),
            "args": data.get("arguments", {}),
        }

    if etype == "tool_return":
        return {
            "type": "tool_return",
            "tool_call_id": data.get("callId", ""),
            "tool_name": data.get("name", ""),
            "result": data.get("output", ""),
        }

    # thought, timing, step_state, usage, warning, cancelled — skip
    return None
