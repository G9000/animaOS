from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from anima_server.db.session import get_user_session_factory
from anima_server.db.user_store import authenticate_account
from anima_server.models.runtime import RuntimeMessage, RuntimeRun
from anima_server.models.user import User
from anima_server.services.agent.client_actions import (
    ActionToolConnection as ClientConnection,
)
from anima_server.services.agent.client_actions import (
    action_registry as registry,
)
from anima_server.services.sessions import unlock_session_store

logger = logging.getLogger(__name__)

router = APIRouter()


async def _authenticate(
    ws: WebSocket,
) -> tuple[ClientConnection, str | None] | None:
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
        session = await unlock_session_store.resolve_async(unlock_token)
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
        return (
            ClientConnection(
                websocket=ws,
                user_id=session.user_id,
                username=resolved_username,
            ),
            None,
        )

    # Try username/password auth
    if username and password:
        try:
            response, deks, corefs_keys = authenticate_account(username, password)
            user_id = int(response["id"])
            owned_unlock_token = await unlock_session_store.create_async(
                user_id,
                deks,
                corefs_keys=corefs_keys,
            )
            return (
                ClientConnection(
                    websocket=ws,
                    user_id=user_id,
                    username=str(response.get("username", username)),
                ),
                owned_unlock_token,
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
    authenticated = await _authenticate(websocket)
    if authenticated is None:
        await websocket.close(code=4001, reason="Authentication failed")
        return
    conn, owned_unlock_token = authenticated

    turn_task: asyncio.Task[None] | None = None
    approval_task: asyncio.Task[None] | None = None
    registered = False

    try:
        registry.add(conn)
        registered = True
        await websocket.send_json(
            {
                "type": "auth_ok",
                "user": {"id": conn.user_id, "username": conn.username},
            }
        )
        for frame in _pending_approval_frames(conn.user_id):
            await websocket.send_json(frame)

        logger.info("WebSocket client connected: user_id=%d", conn.user_id)

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
                if (turn_task is not None and not turn_task.done()) or (
                    approval_task is not None and not approval_task.done()
                ):
                    await conn.websocket.send_json(
                        {
                            "type": "error",
                            "message": "Turn already in progress",
                            "code": "BUSY",
                        }
                    )
                    continue
                if _has_awaiting_approval_run(conn.user_id):
                    await conn.websocket.send_json(
                        {
                            "type": "error",
                            "message": "Turn already awaiting approval",
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
                if approval_task is not None and not approval_task.done():
                    await conn.websocket.send_json(
                        {
                            "type": "error",
                            "message": "Approval resume already in progress",
                            "code": "BUSY",
                        }
                    )
                    continue
                approval_task = asyncio.create_task(
                    _handle_approval_response(conn, data),
                )

            elif msg_type == "cancel":
                await _handle_cancel(conn, data)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected: user_id=%d", conn.user_id)
    finally:
        try:
            if turn_task is not None and not turn_task.done():
                turn_task.cancel()
            if approval_task is not None and not approval_task.done():
                approval_task.cancel()
            if registered:
                registry.remove(conn)
        finally:
            if owned_unlock_token is not None:
                await unlock_session_store.revoke_async(owned_unlock_token)


def _has_awaiting_approval_run(user_id: int) -> bool:
    from anima_server.db.runtime import get_runtime_session_factory

    runtime_db = get_runtime_session_factory()()
    try:
        return (
            runtime_db.scalar(
                select(RuntimeRun.id)
                .where(
                    RuntimeRun.user_id == user_id,
                    RuntimeRun.status == "awaiting_approval",
                )
                .limit(1)
            )
            is not None
        )
    finally:
        runtime_db.close()


def _pending_approval_frames(user_id: int) -> list[dict[str, Any]]:
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.services.agent.persistence import cancel_run

    runtime_db = get_runtime_session_factory()()
    cleared_unresumable = False
    try:
        runs = runtime_db.scalars(
            select(RuntimeRun)
            .where(
                RuntimeRun.user_id == user_id,
                RuntimeRun.status == "awaiting_approval",
            )
            .order_by(RuntimeRun.started_at, RuntimeRun.id)
        ).all()

        frames: list[dict[str, Any]] = []
        for run in runs:
            approval_msg = None
            if run.pending_approval_message_id is not None:
                approval_msg = runtime_db.get(
                    RuntimeMessage,
                    run.pending_approval_message_id,
                )

            if approval_msg is None:
                cancel_run(runtime_db, run.id)
                cleared_unresumable = True
                continue

            frames.append(_approval_required_frame(run, approval_msg))

        if cleared_unresumable:
            runtime_db.commit()

        return frames
    finally:
        runtime_db.close()


def _approval_required_frame(
    run: RuntimeRun,
    approval_msg: RuntimeMessage,
) -> dict[str, Any]:
    tool_args = approval_msg.tool_args_json
    return {
        "type": "approval_required",
        "run_id": run.id,
        "tool_call_id": approval_msg.tool_call_id or "",
        "tool_name": approval_msg.tool_name or "",
        "args": tool_args if isinstance(tool_args, dict) else {},
    }


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
        service_stream = stream_agent(
            message,
            conn.user_id,
            db,
            runtime_db,
        )
        async with contextlib.aclosing(service_stream):
            async for event in service_stream:
                ws_msg = _translate_event(event)
                if ws_msg is not None:
                    await conn.websocket.send_json(ws_msg)
    except Exception as exc:
        logger.exception("Agent error for user_id=%d", conn.user_id)
        from anima_server.services.agent.service import client_error_message

        await conn.websocket.send_json(
            {
                "type": "error",
                "message": client_error_message(exc),
                "code": "AGENT_ERROR",
            }
        )
    finally:
        runtime_db.close()
        db.close()


def _handle_tool_result(conn: ClientConnection, data: dict) -> None:
    registry.resolve_tool_result(conn, data.get("tool_call_id", ""), data)


async def _handle_approval_response(conn: ClientConnection, data: dict) -> None:
    """Resume an awaiting-approval run from the websocket client."""
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.services.agent.service import (
        client_error_message,
        stream_approve_or_deny,
    )

    raw_run_id = data.get("run_id", data.get("runId"))
    try:
        run_id = int(raw_run_id)
    except (TypeError, ValueError):
        await conn.websocket.send_json(
            {
                "type": "error",
                "message": "approval_response requires a numeric run_id",
                "code": "BAD_REQUEST",
            }
        )
        return

    approved = data.get("approved")
    if not isinstance(approved, bool):
        await conn.websocket.send_json(
            {
                "type": "error",
                "message": "approval_response requires boolean approved",
                "code": "BAD_REQUEST",
            }
        )
        return

    reason = data.get("reason")
    denial_reason = reason if isinstance(reason, str) else None

    db = get_user_session_factory(conn.user_id)()
    runtime_db = get_runtime_session_factory()()
    try:
        run = runtime_db.get(RuntimeRun, run_id)
        if run is None:
            await conn.websocket.send_json(
                {
                    "type": "error",
                    "message": f"Run {run_id} not found",
                    "code": "RUN_NOT_FOUND",
                }
            )
            return
        if run.user_id != conn.user_id:
            await conn.websocket.send_json(
                {
                    "type": "error",
                    "message": f"Not authorized for run {run_id}",
                    "code": "FORBIDDEN",
                }
            )
            return
        if run.status != "awaiting_approval":
            await conn.websocket.send_json(
                {
                    "type": "error",
                    "message": f"Run {run_id} is not awaiting approval",
                    "code": "RUN_CONFLICT",
                }
            )
            return

        service_stream = stream_approve_or_deny(
            run_id,
            conn.user_id,
            approved,
            db,
            runtime_db,
            denial_reason=denial_reason,
        )
        async with contextlib.aclosing(service_stream):
            async for event in service_stream:
                ws_msg = _translate_event(event)
                if ws_msg is not None:
                    await conn.websocket.send_json(ws_msg)
    except PermissionError as exc:
        await conn.websocket.send_json(
            {
                "type": "error",
                "message": str(exc),
                "code": "FORBIDDEN",
            }
        )
    except ValueError as exc:
        await conn.websocket.send_json(
            {
                "type": "error",
                "message": str(exc),
                "code": "RUN_CONFLICT",
            }
        )
    except Exception as exc:
        logger.exception("Approval response failed for run %s (user %s)", run_id, conn.user_id)
        await conn.websocket.send_json(
            {
                "type": "error",
                "message": client_error_message(exc),
                "code": "APPROVAL_ERROR",
            }
        )
    finally:
        runtime_db.close()
        db.close()


async def _handle_cancel(conn: ClientConnection, data: dict) -> None:
    """Cancel an in-flight run: {"type": "cancel", "run_id": <int>}."""
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.services.agent.service import cancel_agent_run

    raw_run_id = data.get("run_id", data.get("runId"))
    try:
        run_id = int(raw_run_id)
    except (TypeError, ValueError):
        await conn.websocket.send_json(
            {
                "type": "error",
                "message": "cancel requires a numeric run_id",
                "code": "BAD_REQUEST",
            }
        )
        return

    runtime_db = get_runtime_session_factory()()
    try:
        run = runtime_db.get(RuntimeRun, run_id)
        if run is None or run.user_id != conn.user_id:
            await conn.websocket.send_json(
                {
                    "type": "error",
                    "message": f"Run {run_id} not found",
                    "code": "RUN_NOT_FOUND",
                }
            )
            return
        cancelled = await cancel_agent_run(run_id, conn.user_id, runtime_db)
        if cancelled is not None:
            await conn.websocket.send_json(
                {
                    "type": "cancelled",
                    "run_id": cancelled.id,
                }
            )
    except Exception:
        logger.exception("Cancel failed for run %s (user %s)", run_id, conn.user_id)
    finally:
        runtime_db.close()


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

    if etype == "run_started":
        return {
            "type": "run_started",
            "run_id": data.get("runId"),
            "thread_id": data.get("threadId"),
        }

    if etype == "cancelled":
        return {
            "type": "cancelled",
            "run_id": data.get("runId"),
        }

    if etype == "approval_pending":
        return {
            "type": "approval_required",
            "run_id": data.get("runId"),
            "tool_call_id": data.get("toolCallId", ""),
            "tool_name": data.get("toolName", ""),
            "args": data.get("arguments", {}),
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
            "is_error": data.get("isError"),
        }

    # thought, timing, step_state, usage, warning — skip
    return None
