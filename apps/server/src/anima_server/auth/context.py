from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anima_server.services.sessions import UnlockSession

from datetime import UTC, datetime


AuthTokenType = str


@dataclass
class GatewayRequestContext:
    auth_token: str | None
    token_type: AuthTokenType
    user_id: int | None
    device_id: str | None = None
    device_secret: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    session_id: str | None = None
    session: "UnlockSession" | None = None
    path: str | None = None
    method: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None
    authenticated_at: datetime | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.token_type not in {"bearer", "legacy", "none"}:
            raise ValueError("Invalid token type")

    @property
    def has_auth(self) -> bool:
        return bool(self.auth_token)

    @property
    def is_legacy(self) -> bool:
        return self.token_type == "legacy"

    @property
    def is_bearer(self) -> bool:
        return self.token_type == "bearer"

    @property
    def is_authenticated(self) -> bool:
        return bool(self.user_id is not None and self.session is not None)

    def to_audit_dict(self) -> dict[str, object]:
        auth = {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "session_id": self.session_id,
            "path": self.path,
            "method": self.method,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
        }
        if self.failure_reason:
            auth["failure_reason"] = self.failure_reason
        if self.authenticated_at is not None:
            auth["authenticated_at"] = self.authenticated_at.isoformat()
            auth["authenticated_at_utc"] = self.authenticated_at.replace(
                tzinfo=UTC).isoformat()
        return {
            **auth,
        }
