from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
from threading import Lock

SESSION_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class UnlockSession:
    user_id: int
    expires_at: datetime


class UnlockSessionStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, UnlockSession] = {}

    def create(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        session = UnlockSession(
            user_id=user_id,
            expires_at=self._now() + SESSION_TTL,
        )
        with self._lock:
            self._purge_expired_locked()
            self._sessions[token] = session
        return token

    def resolve(self, token: str | None) -> UnlockSession | None:
        if token is None:
            return None

        with self._lock:
            self._purge_expired_locked()
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expires_at <= self._now():
                self._sessions.pop(token, None)
                return None
            return session

    def revoke(self, token: str | None) -> None:
        if token is None:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def revoke_user(self, user_id: int) -> None:
        with self._lock:
            matching_tokens = [
                token
                for token, session in self._sessions.items()
                if session.user_id == user_id
            ]
            for token in matching_tokens:
                self._sessions.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _purge_expired_locked(self) -> None:
        now = self._now()
        expired_tokens = [
            token
            for token, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for token in expired_tokens:
            self._sessions.pop(token, None)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


unlock_session_store = UnlockSessionStore()
