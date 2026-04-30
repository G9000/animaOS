from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from anima_server.db.session import get_user_session_factory
from anima_server.db.user_store import authenticate_account
from anima_server.models.user import User
from anima_server.services.agent.client_actions import (
    ActionToolConnection as ClientConnection,
    action_registry as registry,
)
from anima_server.services.sessions import unlock_session_store

logger = logging.getLogger(__name__)

router = APIRouter()


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
                registry.update_tool_schemas(conn, data.get("tools", []))
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
        registry.remove(conn)


async def _handle_user_message(conn: ClientConnection, data: dict) -> None:
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.services.agent.service import stream_agent

    message = data.get("message", "")

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


def _handle_tool_result(conn: ClientConnection, data: dict) -> None:
    registry.resolve_tool_result(data.get("tool_call_id", ""), data)


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
