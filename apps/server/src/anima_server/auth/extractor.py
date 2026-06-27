from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import Request
from starlette.datastructures import Headers

from .context import GatewayRequestContext

AUTHORIZATION_HEADER = "authorization"
X_AUTHORIZATION_HEADER = "x-anima-unlock"
TRACE_ID_HEADER = "x-trace-id"
REQUEST_ID_HEADER = "x-request-id"
DEVICE_ID_HEADER = "x-anima-device-id"
DEVICE_SECRET_HEADER = "x-anima-device-secret"
SESSION_ID_HEADER = "x-anima-session-id"
REQUEST_TIMESTAMP_HEADER = "x-request-timestamp"


def build_request_context(request: Request) -> GatewayRequestContext:
    token, token_type = _read_auth_token(request.headers)
    trace_id = request.headers.get(TRACE_ID_HEADER) or request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
    request_id = request.headers.get(REQUEST_ID_HEADER)

    source_ip = None
    if request.client:
        source_ip = request.client.host

    return GatewayRequestContext(
        auth_token=token,
        token_type=token_type,
        user_id=None,
        device_id=request.headers.get(DEVICE_ID_HEADER),
        device_secret=request.headers.get(DEVICE_SECRET_HEADER),
        request_id=request_id,
        trace_id=trace_id,
        session_id=request.headers.get(SESSION_ID_HEADER),
        path=str(request.url.path),
        method=request.method,
        source_ip=source_ip,
        user_agent=request.headers.get("user-agent"),
    )


def _read_auth_token(headers: dict[str, str] | object) -> tuple[str | None, str]:
    if hasattr(headers, "get"):
        # Authorization: Bearer <token>
        authorization = str(headers.get(AUTHORIZATION_HEADER, "")).strip()
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
            if token:
                return token, "bearer"

        # Compatibility bridge: x-anima-unlock
        unlock = str(headers.get(X_AUTHORIZATION_HEADER, "")).strip()
        if unlock:
            return unlock, "legacy"

    return None, "none"


def read_header_value(headers: Headers, header: str, *, fallback: str | None = None) -> str | None:
    value = headers.get(header)
    if value is None:
        return fallback
    value = str(value).strip()
    return value or fallback


def get_request_context_from_state(request: object) -> GatewayRequestContext | None:
    if not hasattr(request, "state"):
        return None
    context = getattr(request.state, "auth_context", None)
    if isinstance(context, GatewayRequestContext):
        return context
    return None


def assign_request_context_to_state(request: Request, context: GatewayRequestContext) -> None:
    if hasattr(request, "state"):
        request.state.auth_context = context
