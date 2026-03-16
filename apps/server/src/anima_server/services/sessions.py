from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
from threading import Lock

SESSION_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class UnlockSession:
    user_id: int
    dek: bytes
    expires_at: datetime


class UnlockSessionStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, UnlockSession] = {}
        self._latest_dek_by_user: dict[int, bytes] = {}

    def create(self, user_id: int, dek: bytes) -> str:
        token = secrets.token_urlsafe(32)
        session = UnlockSession(
            user_id=user_id,
            dek=dek,
            expires_at=self._now() + SESSION_TTL,
        )
        with self._lock:
            self._purge_expired_locked()
            self._sessions[token] = session
            self._latest_dek_by_user[user_id] = dek
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
            session = self._sessions.pop(token, None)
            if session is not None:
                self._refresh_latest_dek_locked(session.user_id)
                _zero_dek(session.dek)

    def revoke_user(self, user_id: int) -> None:
        with self._lock:
            matching_tokens = [
                token
                for token, session in self._sessions.items()
                if session.user_id == user_id
            ]
            for token in matching_tokens:
                self._sessions.pop(token, None)
            self._latest_dek_by_user.pop(user_id, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._latest_dek_by_user.clear()

    def get_active_dek(self, user_id: int) -> bytes | None:
        with self._lock:
            self._purge_expired_locked()
            return self._latest_dek_by_user.get(user_id)

    def _purge_expired_locked(self) -> None:
        now = self._now()
        expired_tokens = []
        affected_users: set[int] = set()
        for token, session in self._sessions.items():
            if session.expires_at <= now:
                expired_tokens.append(token)
                affected_users.add(session.user_id)
        for token in expired_tokens:
            expired = self._sessions.pop(token, None)
            if expired is not None:
                _zero_dek(expired.dek)
        for user_id in affected_users:
            self._refresh_latest_dek_locked(user_id)

    def _refresh_latest_dek_locked(self, user_id: int) -> None:
        latest_session = next(
            (
                session
                for session in reversed(tuple(self._sessions.values()))
                if session.user_id == user_id
            ),
            None,
        )
        if latest_session is None:
            self._latest_dek_by_user.pop(user_id, None)
            return
        self._latest_dek_by_user[user_id] = latest_session.dek

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


unlock_session_store = UnlockSessionStore()


def get_active_dek(user_id: int) -> bytes | None:
    return unlock_session_store.get_active_dek(user_id)


def _zero_dek(dek: bytes) -> None:
    """Best-effort zeroing of DEK bytes in memory.

    Python ``bytes`` objects are immutable, so we cannot guarantee the
    original buffer is wiped.  ``ctypes.memset`` overwrites the buffer
    backing the object — this is a defence-in-depth measure, not a
    guarantee against all memory inspection techniques.
    """
    try:
        ctypes.memset(id(dek) + bytes.__basicsize__ - 1, 0, len(dek))
    except Exception:  # noqa: BLE001
        pass
