"""Authentication primitives shared by gateway middleware and runtime entry points."""

from .context import GatewayRequestContext
from .extractor import (
    SESSION_ID_HEADER,
    TRACE_ID_HEADER,
    X_AUTHORIZATION_HEADER,
    build_request_context,
    get_request_context_from_state,
    read_header_value,
)
from .middleware import GatewayAuthMiddleware

__all__ = [
    "GatewayRequestContext",
    "SESSION_ID_HEADER",
    "TRACE_ID_HEADER",
    "X_AUTHORIZATION_HEADER",
    "GatewayAuthMiddleware",
    "build_request_context",
    "get_request_context_from_state",
    "read_header_value",
]
