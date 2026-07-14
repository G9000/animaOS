from __future__ import annotations

import base64
import binascii
import contextlib
import ctypes
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any

from anima_server.services.dev_session_snapshot import DevSessionSnapshot

SESSION_TTL = timedelta(hours=24)

DEFAULT_DOMAIN = "memories"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UnlockSession:
    user_id: int
    deks: dict[str, bytes]
    expires_at: datetime


class UnlockSessionStore:
    def __init__(self, *, snapshot: Any | None = None) -> None:
        self._lock = RLock()
        self._snapshot = snapshot
        self._sessions: dict[str, UnlockSession] = {}
        self._latest_deks_by_user: dict[int, dict[str, bytes]] = {}
        self._db_viewer_verified_at: dict[str, float] = {}
        self._sqlcipher_key: bytes | None = None
        self._restore_snapshot()

    def create(self, user_id: int, deks: dict[str, bytes]) -> str:
        token = secrets.token_urlsafe(32)
        session = UnlockSession(
            user_id=user_id,
            deks={domain: _copy_key(dek) for domain, dek in deks.items()},
            expires_at=self._now() + SESSION_TTL,
        )
        with self._lock:
            self._purge_expired_locked()
            next_sessions = dict(self._sessions)
            next_sessions[token] = session
            self._commit_locked(next_sessions, self._sqlcipher_key)
        return token

    def resolve(self, token: str | None) -> UnlockSession | None:
        if token is None:
            return None

        with self._lock:
            self._purge_expired_locked()
            session = self._sessions.get(token)
            if session is None or session.expires_at <= self._now():
                return None
            return session

    def revoke(self, token: str | None) -> None:
        if token is None:
            return
        with self._lock:
            if token not in self._sessions:
                self._db_viewer_verified_at.pop(token, None)
                return
            next_sessions = dict(self._sessions)
            next_sessions.pop(token, None)
            self._commit_locked(next_sessions, self._sqlcipher_key)

    def revoke_user(self, user_id: int) -> None:
        with self._lock:
            next_sessions = {
                token: session
                for token, session in self._sessions.items()
                if session.user_id != user_id
            }
            if len(next_sessions) == len(self._sessions):
                self._latest_deks_by_user.pop(user_id, None)
                return
            self._commit_locked(next_sessions, self._sqlcipher_key)

    def clear(self) -> None:
        with self._lock:
            self._commit_locked({}, None)

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
            if token not in self._sessions or verified_at is None:
                self._db_viewer_verified_at.pop(token, None)
                return
            self._db_viewer_verified_at[token] = verified_at

    def get_db_viewer_verified_at(self, token: str | None) -> float | None:
        if token is None:
            return None
        with self._lock:
            self._purge_expired_locked()
            return self._db_viewer_verified_at.get(token)

    def set_sqlcipher_key(self, key: bytes) -> None:
        copied_key = _copy_key(key)
        with self._lock:
            self._commit_locked(dict(self._sessions), copied_key)

    def get_sqlcipher_key(self) -> bytes | None:
        with self._lock:
            return self._sqlcipher_key

    def clear_sqlcipher_key(self) -> None:
        with self._lock:
            if self._sqlcipher_key is None:
                return
            self._commit_locked(dict(self._sessions), None)

    def _purge_expired_locked(self) -> None:
        now = self._now()
        next_sessions = {
            token: session
            for token, session in self._sessions.items()
            if session.expires_at > now
        }
        if len(next_sessions) == len(self._sessions):
            return
        try:
            self._commit_locked(next_sessions, self._sqlcipher_key)
        except Exception as exc:
            # Expiry is checked independently during every resolve and restore,
            # so committing the in-memory purge remains fail-closed even if the
            # cleanup snapshot cannot be rewritten.
            logger.warning(
                "Failed to persist expired dev sessions; keeping them invalid: %s",
                type(exc).__name__,
            )
            self._apply_state_locked(next_sessions, self._sqlcipher_key)

    def _commit_locked(
        self,
        next_sessions: dict[str, UnlockSession],
        next_sqlcipher_key: bytes | None,
    ) -> None:
        if self._snapshot is not None:
            self._snapshot.write(
                self._snapshot_payload(next_sessions, next_sqlcipher_key)
            )
        self._apply_state_locked(next_sessions, next_sqlcipher_key)

    def _apply_state_locked(
        self,
        next_sessions: dict[str, UnlockSession],
        next_sqlcipher_key: bytes | None,
    ) -> None:
        removed_sessions = [
            session
            for token, session in self._sessions.items()
            if token not in next_sessions
        ]
        previous_sqlcipher_key = self._sqlcipher_key

        self._sessions = next_sessions
        self._sqlcipher_key = next_sqlcipher_key
        self._db_viewer_verified_at = {
            token: verified_at
            for token, verified_at in self._db_viewer_verified_at.items()
            if token in next_sessions
        }
        self._rebuild_latest_deks_locked()

        for session in removed_sessions:
            _zero_deks(session.deks)
        if (
            previous_sqlcipher_key is not None
            and previous_sqlcipher_key is not next_sqlcipher_key
        ):
            _zero_dek(previous_sqlcipher_key)

    def _restore_snapshot(self) -> None:
        if self._snapshot is None:
            return
        try:
            payload = self._snapshot.load()
            if payload is None:
                return
            sessions, sqlcipher_key, discarded_expired = self._decode_snapshot(payload)
            self._sessions = sessions
            self._sqlcipher_key = sqlcipher_key
            self._rebuild_latest_deks_locked()
            if discarded_expired:
                try:
                    self._snapshot.write(self._snapshot_payload(sessions, sqlcipher_key))
                except Exception as exc:
                    logger.warning(
                        "Failed to clean expired dev session snapshot: %s",
                        type(exc).__name__,
                    )
        except Exception as exc:
            logger.warning(
                "Ignoring unusable dev session snapshot; runtime remains locked: %s",
                type(exc).__name__,
            )
            self._sessions = {}
            self._latest_deks_by_user = {}
            self._sqlcipher_key = None

    def _decode_snapshot(
        self,
        payload: dict[str, object],
    ) -> tuple[dict[str, UnlockSession], bytes | None, bool]:
        raw_sessions = payload["sessions"]
        if not isinstance(raw_sessions, list):
            raise ValueError("Invalid snapshot sessions")
        now = self._now()
        sessions: dict[str, UnlockSession] = {}
        discarded_expired = False
        for raw_session in raw_sessions:
            if not isinstance(raw_session, dict):
                raise ValueError("Invalid snapshot session")
            token = raw_session["token"]
            user_id = raw_session["userId"]
            expires_at = _parse_expiry(raw_session["expiresAt"])
            raw_deks = raw_session["deks"]
            if not isinstance(token, str) or not isinstance(user_id, int):
                raise ValueError("Invalid snapshot identity")
            if not isinstance(raw_deks, dict):
                raise ValueError("Invalid snapshot DEKs")
            deks = {
                str(domain): _decode_key(encoded_key)
                for domain, encoded_key in raw_deks.items()
            }
            if expires_at <= now:
                discarded_expired = True
                _zero_deks(deks)
                continue
            sessions[token] = UnlockSession(
                user_id=user_id,
                deks=deks,
                expires_at=expires_at,
            )
        raw_sqlcipher_key = payload["sqlcipherKey"]
        sqlcipher_key = (
            None if raw_sqlcipher_key is None else _decode_key(raw_sqlcipher_key)
        )
        return sessions, sqlcipher_key, discarded_expired

    @staticmethod
    def _snapshot_payload(
        sessions: dict[str, UnlockSession],
        sqlcipher_key: bytes | None,
    ) -> dict[str, object]:
        return {
            "version": 1,
            "sessions": [
                {
                    "token": token,
                    "userId": session.user_id,
                    "expiresAt": _format_expiry(session.expires_at),
                    "deks": {
                        domain: base64.b64encode(dek).decode("ascii")
                        for domain, dek in sorted(session.deks.items())
                    },
                }
                for token, session in sorted(sessions.items())
            ],
            "sqlcipherKey": (
                None
                if sqlcipher_key is None
                else base64.b64encode(sqlcipher_key).decode("ascii")
            ),
        }

    def _rebuild_latest_deks_locked(self) -> None:
        self._latest_deks_by_user = {}
        for session in self._sessions.values():
            self._latest_deks_by_user[session.user_id] = session.deks

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


def _format_expiry(value: datetime) -> str:
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_expiry(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("Invalid snapshot expiry")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _decode_key(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("Invalid snapshot key")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid snapshot key") from exc
    if len(decoded) != 32:
        raise ValueError("Invalid snapshot key length")
    return decoded


def _copy_key(value: bytes) -> bytes:
    return bytes(bytearray(value))


def _zero_deks(deks: dict[str, bytes]) -> None:
    """Best-effort zeroing of all DEK bytes in memory."""
    for dek in deks.values():
        _zero_dek(dek)


def _zero_dek(dek: bytes) -> None:
    """Best-effort zeroing of a runtime secret buffer.

    Python ``bytes`` objects are immutable, so this is defense in depth rather
    than a guarantee against all memory inspection techniques.
    """
    with contextlib.suppress(Exception):
        ctypes.memset(id(dek) + bytes.__basicsize__ - 1, 0, len(dek))


# Initialize the process-global store only after every restore helper above is
# defined. Dev reloads import this module with a snapshot already present.
unlock_session_store = UnlockSessionStore(
    snapshot=DevSessionSnapshot.from_environment()
)


def get_active_dek(user_id: int, domain: str = DEFAULT_DOMAIN) -> bytes | None:
    return unlock_session_store.get_active_dek(user_id, domain)


def get_active_deks(user_id: int) -> dict[str, bytes] | None:
    return unlock_session_store.get_active_deks(user_id)


def set_sqlcipher_key(key: bytes) -> None:
    unlock_session_store.set_sqlcipher_key(key)


def get_sqlcipher_key() -> bytes | None:
    return unlock_session_store.get_sqlcipher_key()


def clear_sqlcipher_key() -> None:
    unlock_session_store.clear_sqlcipher_key()
