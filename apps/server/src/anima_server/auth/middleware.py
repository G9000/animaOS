from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from anima_server.auth.extractor import assign_request_context_to_state, build_request_context
from anima_server.auth.policy import (
    DeviceTrustStore,
    IdempotencyStore,
    RateLimiter,
    ReplayStore,
    device_trust_store,
    request_rate_limiter,
    request_replay_store,
    webhook_idempotency_store,
)
from anima_server.services.health.event_logger import emit
from anima_server.services.sessions import unlock_session_store

logger = logging.getLogger(__name__)


class GatewayAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        device_store: DeviceTrustStore = device_trust_store,
        replay_store: ReplayStore = request_replay_store,
        rate_limiter: RateLimiter = request_rate_limiter,
        webhook_store: IdempotencyStore = webhook_idempotency_store,
        public_paths: set[str] | None = None,
        require_device_secret: bool = False,
        max_requests_per_minute: int = 120,
        replay_ttl_seconds: int = 180,
        rate_window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self.device_store = device_store
        self.replay_store = replay_store
        self.rate_limiter = rate_limiter
        self.webhook_store = webhook_store
        self.public_paths = public_paths or self._default_public_paths()
        self.require_device_secret = require_device_secret
        self.max_requests_per_minute = max_requests_per_minute
        self.replay_ttl_seconds = replay_ttl_seconds
        self.rate_window_seconds = rate_window_seconds
        self.replay_store._ttl_seconds = replay_ttl_seconds
        self.rate_limiter._window_seconds = rate_window_seconds

    async def dispatch(self, request, call_next):
        context = build_request_context(request)
        assign_request_context_to_state(request, context)
        path = request.url.path
        method = request.method.upper()

        if self._is_public_path(path):
            return await call_next(request)

        if path.startswith("/api/webhook/"):
            return await call_next(request)

        # Rate limit.
        rate_key = self._rate_limit_key(context)
        allowed, retry_after = self.rate_limiter.allow(
            rate_key,
            self.max_requests_per_minute,
        )
        if not allowed:
            emit(
                category="http",
                event="auth.rate_limited",
                level="warn",
                user_id=context.user_id,
                data={
                    "path": path,
                    "method": method,
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                },
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after or 1)},
                content={"error": "Too many requests."},
            )

        # Replay checks for state-changing requests.
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            if context.request_id is None:
                context.request_id = str(int(time.time()))
            key = f"{context.request_id}:{method}:{path}:{context.source_ip or context.user_agent}"
            if not self.replay_store.consume(key):
                emit(
                    category="http",
                    event="auth.replay",
                    level="warn",
                    data={
                        "path": path,
                        "method": method,
                        "request_id": context.request_id,
                        "trace_id": context.trace_id,
                    },
                )
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={"error": "Duplicate request detected."},
                )

        # Resolve session from token.
        session = self._resolve_session(context)
        if session is None:
            context.failure_reason = "missing_or_invalid_session"
            assign_request_context_to_state(request, context)
            emit(
                category="http",
                event="auth.failed",
                level="warn",
                data={
                    "path": path,
                    "method": method,
                    "token_type": context.token_type,
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                },
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "Session locked. Please sign in again."},
            )

        context.user_id = session.user_id
        context.session = session
        context.authenticated_at = datetime.now(UTC)
        assign_request_context_to_state(request, context)

        if self.require_device_secret:
            if context.device_id and not self.device_store.validate_device(context):
                emit(
                    category="http",
                    event="auth.device_failed",
                    level="warn",
                    user_id=context.user_id,
                    data={
                        "path": path,
                        "method": method,
                        "device_id": context.device_id,
                        "request_id": context.request_id,
                        "trace_id": context.trace_id,
                    },
                )
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Device not trusted for this session."},
                )

        if context.device_id:
            self.device_store.touch(
                user_id=session.user_id,
                device_id=context.device_id,
            )

        emit(
            category="http",
            event="auth.success",
            level="info",
            user_id=context.user_id,
            data={
                "path": path,
                "method": method,
                "token_type": context.token_type,
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "session_id": context.session_id,
                "device_id": context.device_id,
            },
        )
        return await call_next(request)

    def _resolve_session(self, context):
        if not context.auth_token:
            return None
        session = unlock_session_store.resolve(context.auth_token)
        if session is None:
            return None

        if context.session_id and session.session_id is not None:
            return session if context.session_id == session.session_id else None
        return session

    def _default_public_paths(self) -> set[str]:
        return {
            "/health",
            "/api/health",
            "/api/health/detailed",
            "/api/health/check",
            "/api/health/logs",
            "/api/health/logs/summary",
            "/api/auth/login",
            "/api/auth/register",
            "/api/auth/recover",
            "/api/auth/create-ai/chat",
            "/ws/agent",
            "/docs",
            "/openapi.json",
        }

    def _is_public_path(self, path: str) -> bool:
        if path in self.public_paths:
            return True
        for public_path in self.public_paths:
            if path.startswith(public_path):
                return True
        return False

    def _rate_limit_key(self, context) -> str:
        if context.user_id is not None:
            return f"user:{context.user_id}"
        if context.source_ip:
            return f"ip:{context.source_ip}"
        return "anonymous"
