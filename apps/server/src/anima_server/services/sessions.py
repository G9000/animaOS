from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
from threading import Lock

SESSION_TTL = timedelta(hours=24)

DEFAULT_DOMAIN = "memories"


@dataclass(frozen=True, slots=True)
class UnlockSession:
    user_id: int
    deks: dict[str, bytes]
    expires_at: datetime


class UnlockSessionStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, UnlockSession] = {}
        self._latest_deks_by_user: dict[int, dict[str, bytes]] = {}
        self._db_viewer_verified_at: dict[str, float] = {}

    def create(self, user_id: int, deks: dict[str, bytes]) -> str:
        token = secrets.token_urlsafe(32)
        session = UnlockSession(
            user_id=user_id,
            deks=deks,
            expires_at=self._now() + SESSION_TTL,
        )
        with self._lock:
            self._purge_expired_locked()
            self._sessions[token] = session
            self._latest_deks_by_user[user_id] = deks
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
            self._db_viewer_verified_at.pop(token, None)
            if session is not None:
                self._refresh_latest_deks_locked(session.user_id)
                _zero_deks(session.deks)

    def revoke_user(self, user_id: int) -> None:
        with self._lock:
            matching_tokens = [
                token
                for token, session in self._sessions.items()
                if session.user_id == user_id
            ]
            for token in matching_tokens:
                session = self._sessions.pop(token, None)
                self._db_viewer_verified_at.pop(token, None)
                if session is not None:
                    _zero_deks(session.deks)
            self._latest_deks_by_user.pop(user_id, None)

    def clear(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                _zero_deks(session.deks)
            self._sessions.clear()
            self._latest_deks_by_user.clear()
            self._db_viewer_verified_at.clear()

    def get_active_dek(self, user_id: int, domain: str = DEFAULT_DOMAIN) -> bytes | None:
        with self._lock:
            self._purge_expired_locked()
            deks = self._latest_deks_by_user.get(user_id)
            if deks is None:
                return None
            return deks.get(domain)

    def get_active_deks(self, user_id: int) -> dict[str, bytes] | None:
        with self._lock:
            self._purge_expired_locked()
            return self._latest_deks_by_user.get(user_id)

    def set_db_viewer_verified_at(
        self,
        token: str | None,
        verified_at: float | None,
    ) -> None:
        if token is None:
            return
        with self._lock:
            self._purge_expired_locked()
            if token not in self._sessions:
                self._db_viewer_verified_at.pop(token, None)
                return
            if verified_at is None:
                self._db_viewer_verified_at.pop(token, None)
                return
            self._db_viewer_verified_at[token] = verified_at

    def get_db_viewer_verified_at(self, token: str | None) -> float | None:
        if token is None:
            return None
        with self._lock:
            self._purge_expired_locked()
            return self._db_viewer_verified_at.get(token)

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
            self._db_viewer_verified_at.pop(token, None)
            if expired is not None:
                _zero_deks(expired.deks)
        for user_id in affected_users:
            self._refresh_latest_deks_locked(user_id)

    def _refresh_latest_deks_locked(self, user_id: int) -> None:
        latest_session = next(
            (
                session
                for session in reversed(tuple(self._sessions.values()))
                if session.user_id == user_id
            ),
            None,
        )
        if latest_session is None:
            self._latest_deks_by_user.pop(user_id, None)
            return
        self._latest_deks_by_user[user_id] = latest_session.deks

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


unlock_session_store = UnlockSessionStore()


def get_active_dek(user_id: int, domain: str = DEFAULT_DOMAIN) -> bytes | None:
    return unlock_session_store.get_active_dek(user_id, domain)


def get_active_deks(user_id: int) -> dict[str, bytes] | None:
    return unlock_session_store.get_active_deks(user_id)


# ---------------------------------------------------------------------------
# SQLCipher key cache — set after authentication, read by engine creation.
# In unified passphrase mode the SQLCipher raw key is unwrapped from the
# manifest using the user's password. It lives here (not in config) because
# it is a runtime secret that must not be persisted to disk in plaintext.
# ---------------------------------------------------------------------------

_sqlcipher_key_lock = Lock()
_sqlcipher_key: bytes | None = None


def set_sqlcipher_key(key: bytes) -> None:
    global _sqlcipher_key  # noqa: PLW0603
    with _sqlcipher_key_lock:
        _sqlcipher_key = key


def get_sqlcipher_key() -> bytes | None:
    with _sqlcipher_key_lock:
        return _sqlcipher_key


def clear_sqlcipher_key() -> None:
    global _sqlcipher_key  # noqa: PLW0603
    with _sqlcipher_key_lock:
        if _sqlcipher_key is not None:
            _zero_dek(_sqlcipher_key)
        _sqlcipher_key = None


def _zero_deks(deks: dict[str, bytes]) -> None:
    """Best-effort zeroing of all DEK bytes in memory."""
    for dek in deks.values():
        _zero_dek(dek)


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
