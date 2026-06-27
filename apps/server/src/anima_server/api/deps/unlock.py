from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from anima_server.auth.context import GatewayRequestContext
from anima_server.auth.extractor import (
    X_AUTHORIZATION_HEADER,
    assign_request_context_to_state,
    build_request_context,
    get_request_context_from_state,
    read_header_value,
)
from anima_server.services.sessions import UnlockSession, unlock_session_store


def read_unlock_token(request: Request) -> str | None:
    context = get_request_context(request)
    if context.auth_token is not None:
        return context.auth_token

    token = request.headers.get(X_AUTHORIZATION_HEADER)
    if token is None:
        return None
    normalized = token.strip()
    return normalized or None


def get_request_context(request: Request) -> GatewayRequestContext:
    context = get_request_context_from_state(request)
    if context is None:
        context = build_request_context(request)
        assign_request_context_to_state(request, context)
    return context


def request_context_from_headers(request: Request) -> dict[str, str | None]:
    return {
        "x_request_id": read_header_value(request.headers, "x-request-id"),
        "x_trace_id": read_header_value(request.headers, "x-trace-id"),
        "x_session_id": read_header_value(request.headers, "x-anima-session-id"),
        "x_device_id": read_header_value(request.headers, "x-anima-device-id"),
        "x_device_secret": read_header_value(request.headers, "x-anima-device-secret"),
    }


def require_request_context(request: Request) -> GatewayRequestContext:
    return get_request_context(request)


def request_failure_data(request: Request, reason: str) -> dict[str, Any]:
    context = get_request_context(request)
    context.failure_reason = reason
    assign_request_context_to_state(request, context)
    return context.to_audit_dict() | {"path": str(request.url.path)}


def _resolve_session(context: GatewayRequestContext) -> UnlockSession | None:
    if context.auth_token is None:
        return None
    return unlock_session_store.resolve(context.auth_token)


def require_unlocked_session(request: Request) -> UnlockSession:
    context = get_request_context(request)
    session = _resolve_session(context)
    if session is None:
        request_failure_data(request, "missing_or_invalid_session")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session locked. Please sign in again.",
        )

    context.user_id = session.user_id
    context.session = session
    assign_request_context_to_state(request, context)
    return session


def require_unlocked_user(request: Request, user_id: int) -> UnlockSession:
    session = require_unlocked_session(request)
    if session.user_id != user_id:
        request_failure_data(request, "user_mismatch")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session user mismatch.",
        )
    return session
