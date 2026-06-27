from __future__ import annotations

import contextlib
import ctypes
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

SESSION_TTL = timedelta(hours=24)

DEFAULT_DOMAIN = "memories"


@dataclass(frozen=True, slots=True)
class UnlockSession:
    user_id: int
    deks: dict[str, bytes]
    expires_at: datetime
    session_id: str | None = None
    device_id: str | None = None


class UnlockSessionStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, UnlockSession] = {}
        self._latest_deks_by_user: dict[int, dict[str, bytes]] = {}
        self._db_viewer_verified_at: dict[str, float] = {}
        self._tokens_by_user_device: dict[tuple[int, str], set[str]] = {}

    def create(
        self,
        user_id: int,
        deks: dict[str, bytes],
        *,
        session_id: str | None = None,
        device_id: str | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        session = UnlockSession(
            user_id=user_id,
            deks=deks,
            expires_at=self._now() + SESSION_TTL,
            session_id=session_id,
            device_id=device_id,
        )
        with self._lock:
            self._purge_expired_locked()
            self._sessions[token] = session
            self._latest_deks_by_user[user_id] = deks
            if device_id:
                self._tokens_by_user_device.setdefault((user_id, device_id), set()).add(
                    token,
                )
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
                if session.device_id is not None:
                    key = (session.user_id, session.device_id)
                    self._tokens_by_user_device.setdefault(key, set()).discard(token)
                    if not self._tokens_by_user_device.get(key):
                        self._tokens_by_user_device.pop(key, None)
                self._refresh_latest_deks_locked(session.user_id)
                _zero_deks(session.deks)

    def revoke_user(self, user_id: int) -> None:
        with self._lock:
            matching_tokens = [
                token for token, session in self._sessions.items() if session.user_id == user_id
            ]
            for token in matching_tokens:
                session = self._sessions.pop(token, None)
                self._db_viewer_verified_at.pop(token, None)
                if session is not None:
                    if session.device_id is not None:
                        self._tokens_by_user_device.setdefault((session.user_id, session.device_id), set()).discard(token)
                    _zero_deks(session.deks)
            self._latest_deks_by_user.pop(user_id, None)
            for key in list(self._tokens_by_user_device):
                if key[0] == user_id:
                    self._tokens_by_user_device.pop(key, None)

    def revoke_user_device(self, user_id: int, device_id: str) -> int:
        key = (user_id, device_id)
        with self._lock:
            tokens = self._tokens_by_user_device.pop(key, set())
            revoked_count = 0
            for token in list(tokens):
                session = self._sessions.pop(token, None)
                if session is not None:
                    _zero_deks(session.deks)
                    revoked_count += 1
            self._refresh_latest_deks_locked(user_id)
            for token in list(tokens):
                self._db_viewer_verified_at.pop(token, None)
            return revoked_count

    def clear_user_devices(self, user_id: int) -> None:
        with self._lock:
            device_keys = [key for key in self._tokens_by_user_device if key[0] == user_id]
            for key in device_keys:
                tokens = self._tokens_by_user_device.pop(key, set())
                for token in list(tokens):
                    session = self._sessions.pop(token, None)
                    if session is not None:
                        _zero_deks(session.deks)
                    self._db_viewer_verified_at.pop(token, None)
            self._latest_deks_by_user.pop(user_id, None)

    def clear(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                _zero_deks(session.deks)
            self._sessions.clear()
            self._latest_deks_by_user.clear()
            self._db_viewer_verified_at.clear()
            self._tokens_by_user_device.clear()

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
                if expired.device_id:
                    key = (expired.user_id, expired.device_id)
                    self._tokens_by_user_device.setdefault(key, set()).discard(token)
                    if not self._tokens_by_user_device.get(key):
                        self._tokens_by_user_device.pop(key, None)
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
    global _sqlcipher_key
    with _sqlcipher_key_lock:
        _sqlcipher_key = key


def get_sqlcipher_key() -> bytes | None:
    with _sqlcipher_key_lock:
        return _sqlcipher_key


def clear_sqlcipher_key() -> None:
    global _sqlcipher_key
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
    with contextlib.suppress(Exception):
        ctypes.memset(id(dek) + bytes.__basicsize__ - 1, 0, len(dek))
