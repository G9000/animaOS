from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ctypes
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Condition, Event, RLock
from typing import Any

import anima_core

from anima_server.services.core import get_core_dir, get_core_id
from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
from anima_server.services.dev_session_snapshot import DevSessionSnapshot

SESSION_TTL = timedelta(hours=24)

DEFAULT_DOMAIN = "memories"

logger = logging.getLogger(__name__)


def _create_native_corefs_session() -> object:
    return anima_core.CorefsSession(str(get_core_dir()), get_core_id())


def _create_runtime_index(
    _corefs_keys: object,
    sqlcipher_key: bytes | None,
) -> CoreFSProgressiveIndex | None:
    if sqlcipher_key is None:
        return None
    from anima_server.config import settings

    if not settings.runtime_instance_data_dir:
        return None
    local_instance_id = Path(settings.runtime_instance_data_dir).name
    index = CoreFSProgressiveIndex(get_core_id())
    index.unlock(
        sqlcipher_key=sqlcipher_key,
        local_instance_id=local_instance_id,
    )
    return index


@dataclass(frozen=True, slots=True)
class UnlockSession:
    user_id: int
    deks: dict[str, bytes]
    expires_at: datetime
    corefs_keys: object | None = field(default=None, repr=False, compare=False)
    corefs_session: object | None = field(default=None, repr=False, compare=False)
    runtime_index: CoreFSProgressiveIndex | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(slots=True)
class _SessionCloseRecord:
    session: UnlockSession
    done: Event = field(default_factory=Event)
    error: Exception | None = None


@dataclass(slots=True)
class _CleanupBatch:
    records: list[_SessionCloseRecord] = field(default_factory=list)
    sqlcipher_keys: list[bytes] = field(default_factory=list)

    def extend(self, other: _CleanupBatch) -> None:
        self.records.extend(other.records)
        self.sqlcipher_keys.extend(other.sqlcipher_keys)


class UnlockSessionStore:
    def __init__(
        self,
        *,
        snapshot: Any | None = None,
        corefs_session_factory: Callable[[], object] | None = None,
        runtime_index_factory: (
            Callable[[object, bytes | None], CoreFSProgressiveIndex | None] | None
        ) = None,
    ) -> None:
        self._lock = RLock()
        self._construction_condition = Condition(self._lock)
        self._snapshot = snapshot
        self._corefs_session_factory = (
            corefs_session_factory or _create_native_corefs_session
        )
        self._runtime_index_factory = runtime_index_factory or _create_runtime_index
        self._sessions: dict[str, UnlockSession] = {}
        self._latest_deks_by_user: dict[int, dict[str, bytes]] = {}
        self._db_viewer_verified_at: dict[str, float] = {}
        self._sqlcipher_key: bytes | None = None
        self._closing_sessions: dict[int, _SessionCloseRecord] = {}
        self._active_constructions = 0
        self._active_shutdowns = 0
        self._shut_down = False
        self._restore_snapshot()

    def create(
        self,
        user_id: int,
        deks: dict[str, bytes],
        *,
        corefs_keys: object | None = None,
    ) -> str:
        self._begin_construction()
        session: UnlockSession | None = None
        cleanup = _CleanupBatch()
        try:
            token = secrets.token_urlsafe(32)
            session = self._new_session(user_id, deks, corefs_keys)
            with self._lock:
                self._ensure_running_locked()
                cleanup.extend(self._purge_expired_locked())
                next_sessions = dict(self._sessions)
                next_sessions[token] = session
                cleanup.extend(
                    self._commit_locked(next_sessions, self._sqlcipher_key)
                )
            self._run_cleanup(cleanup)
            return token
        except Exception:
            self._run_cleanup(cleanup)
            if session is not None:
                self._destroy_unpublished_session(session)
            raise
        finally:
            self._finish_construction()

    async def create_async(
        self,
        user_id: int,
        deks: dict[str, bytes],
        *,
        corefs_keys: object | None = None,
    ) -> str:
        return await self._to_thread(
            self.create,
            user_id,
            deks,
            corefs_keys=corefs_keys,
            _cancel_result=self.revoke,
        )

    def replace_user(
        self,
        user_id: int,
        deks: dict[str, bytes],
        *,
        corefs_keys: object | None = None,
    ) -> str:
        self._begin_construction()
        replacement: UnlockSession | None = None
        cleanup = _CleanupBatch()
        try:
            token = secrets.token_urlsafe(32)
            replacement = self._new_session(user_id, deks, corefs_keys)
            with self._lock:
                self._ensure_running_locked()
                cleanup.extend(self._purge_expired_locked())
                next_sessions = {
                    current_token: session
                    for current_token, session in self._sessions.items()
                    if session.user_id != user_id
                }
                next_sessions[token] = replacement
                cleanup.extend(
                    self._commit_locked(next_sessions, self._sqlcipher_key)
                )
            self._run_cleanup(cleanup)
            return token
        except Exception:
            self._run_cleanup(cleanup)
            if replacement is not None:
                self._destroy_unpublished_session(replacement)
            raise
        finally:
            self._finish_construction()

    async def replace_user_async(
        self,
        user_id: int,
        deks: dict[str, bytes],
        *,
        corefs_keys: object | None = None,
    ) -> str:
        return await self._to_thread(
            self.replace_user,
            user_id,
            deks,
            corefs_keys=corefs_keys,
            _cancel_result=self.revoke,
        )

    def resolve(self, token: str | None) -> UnlockSession | None:
        if token is None:
            return None

        cleanup = _CleanupBatch()
        with self._lock:
            cleanup.extend(self._purge_expired_locked())
            session = self._sessions.get(token)
            if session is None or session.expires_at <= self._now():
                session = None
        self._run_cleanup(cleanup)
        return session

    async def resolve_async(self, token: str | None) -> UnlockSession | None:
        return await self._to_thread(self.resolve, token)

    def revoke(self, token: str | None) -> None:
        if token is None:
            return
        cleanup = _CleanupBatch()
        with self._lock:
            if token not in self._sessions:
                self._db_viewer_verified_at.pop(token, None)
                return
            next_sessions = dict(self._sessions)
            next_sessions.pop(token, None)
            cleanup.extend(self._commit_locked(next_sessions, self._sqlcipher_key))
        self._run_cleanup(cleanup)

    async def revoke_async(self, token: str | None) -> None:
        await self._to_thread(self.revoke, token)

    def revoke_user(self, user_id: int) -> None:
        cleanup = _CleanupBatch()
        with self._lock:
            next_sessions = {
                token: session
                for token, session in self._sessions.items()
                if session.user_id != user_id
            }
            if len(next_sessions) == len(self._sessions):
                self._latest_deks_by_user.pop(user_id, None)
                return
            cleanup.extend(self._commit_locked(next_sessions, self._sqlcipher_key))
        self._run_cleanup(cleanup)

    async def revoke_user_async(self, user_id: int) -> None:
        await self._to_thread(self.revoke_user, user_id)

    def clear(self) -> None:
        cleanup = _CleanupBatch()
        with self._lock:
            cleanup.extend(self._commit_locked({}, None))
        self._run_cleanup(cleanup)

    async def clear_async(self) -> None:
        await self._to_thread(self.clear)

    def start(self) -> None:
        with self._lock:
            if (
                self._active_constructions
                or self._active_shutdowns
                or self._closing_sessions
            ):
                raise RuntimeError(
                    "Unlock session store cannot start while teardown is active"
                )
            self._shut_down = False

    def get_active_dek(self, user_id: int, domain: str = DEFAULT_DOMAIN) -> bytes | None:
        cleanup = _CleanupBatch()
        with self._lock:
            cleanup.extend(self._purge_expired_locked())
            deks = self._latest_deks_by_user.get(user_id)
            dek = None if deks is None else deks.get(domain)
        self._run_cleanup(cleanup)
        return dek

    async def get_active_dek_async(
        self,
        user_id: int,
        domain: str = DEFAULT_DOMAIN,
    ) -> bytes | None:
        return await self._to_thread(self.get_active_dek, user_id, domain)

    def get_active_deks(self, user_id: int) -> dict[str, bytes] | None:
        cleanup = _CleanupBatch()
        with self._lock:
            cleanup.extend(self._purge_expired_locked())
            deks = self._latest_deks_by_user.get(user_id)
        self._run_cleanup(cleanup)
        return deks

    def set_db_viewer_verified_at(
        self,
        token: str | None,
        verified_at: float | None,
    ) -> None:
        if token is None:
            return
        cleanup = _CleanupBatch()
        with self._lock:
            cleanup.extend(self._purge_expired_locked())
            if token not in self._sessions or verified_at is None:
                self._db_viewer_verified_at.pop(token, None)
            else:
                self._db_viewer_verified_at[token] = verified_at
        self._run_cleanup(cleanup)

    def get_db_viewer_verified_at(self, token: str | None) -> float | None:
        if token is None:
            return None
        cleanup = _CleanupBatch()
        with self._lock:
            cleanup.extend(self._purge_expired_locked())
            verified_at = self._db_viewer_verified_at.get(token)
        self._run_cleanup(cleanup)
        return verified_at

    def set_sqlcipher_key(self, key: bytes) -> None:
        copied_key = _copy_key(key)
        cleanup = _CleanupBatch()
        try:
            with self._lock:
                self._ensure_running_locked()
                cleanup.extend(
                    self._commit_locked(dict(self._sessions), copied_key)
                )
        except Exception:
            _zero_dek(copied_key)
            raise
        self._run_cleanup(cleanup)

    def get_sqlcipher_key(self) -> bytes | None:
        with self._lock:
            return self._sqlcipher_key

    def clear_sqlcipher_key(self) -> None:
        cleanup = _CleanupBatch()
        with self._lock:
            if self._sqlcipher_key is None:
                return
            cleanup.extend(self._commit_locked(dict(self._sessions), None))
        self._run_cleanup(cleanup)

    def _purge_expired_locked(self) -> _CleanupBatch:
        now = self._now()
        next_sessions = {
            token: session
            for token, session in self._sessions.items()
            if session.expires_at > now
        }
        if len(next_sessions) == len(self._sessions):
            return _CleanupBatch()
        try:
            return self._commit_locked(next_sessions, self._sqlcipher_key)
        except Exception as exc:
            # Expiry is checked independently during every resolve and restore,
            # so committing the in-memory purge remains fail-closed even if the
            # cleanup snapshot cannot be rewritten.
            logger.warning(
                "Failed to persist expired dev sessions; keeping them invalid: %s",
                type(exc).__name__,
            )
            return self._apply_state_locked(next_sessions, self._sqlcipher_key)

    def _commit_locked(
        self,
        next_sessions: dict[str, UnlockSession],
        next_sqlcipher_key: bytes | None,
    ) -> _CleanupBatch:
        if self._snapshot is not None:
            self._snapshot.write(
                self._snapshot_payload(next_sessions, next_sqlcipher_key)
            )
        return self._apply_state_locked(next_sessions, next_sqlcipher_key)

    def _apply_state_locked(
        self,
        next_sessions: dict[str, UnlockSession],
        next_sqlcipher_key: bytes | None,
    ) -> _CleanupBatch:
        removed_sessions = [
            session
            for token, session in self._sessions.items()
            if next_sessions.get(token) is not session
        ]
        previous_sqlcipher_key = self._sqlcipher_key
        cleanup = _CleanupBatch()

        for session in removed_sessions:
            session_id = id(session)
            if session_id in self._closing_sessions:
                continue
            record = _SessionCloseRecord(session=session)
            self._closing_sessions[session_id] = record
            cleanup.records.append(record)

        self._sessions = next_sessions
        self._sqlcipher_key = next_sqlcipher_key
        self._db_viewer_verified_at = {
            token: verified_at
            for token, verified_at in self._db_viewer_verified_at.items()
            if token in next_sessions
        }
        self._rebuild_latest_deks_locked()

        if (
            previous_sqlcipher_key is not None
            and previous_sqlcipher_key is not next_sqlcipher_key
        ):
            cleanup.sqlcipher_keys.append(previous_sqlcipher_key)
        return cleanup

    def _new_session(
        self,
        user_id: int,
        deks: dict[str, bytes],
        corefs_keys: object | None,
    ) -> UnlockSession:
        copied_deks = {
            domain: _copy_key(dek)
            for domain, dek in deks.items()
        }
        corefs_session: object | None = None
        runtime_index: CoreFSProgressiveIndex | None = None
        try:
            corefs_session = (
                None
                if corefs_keys is None
                else self._corefs_session_factory()
            )
            if corefs_session is not None and not callable(
                getattr(corefs_session, "begin_close", None)
            ):
                raise RuntimeError(
                    "CoreFS native session does not implement begin_close"
                )
            if corefs_keys is not None:
                with self._lock:
                    sqlcipher_key = self._sqlcipher_key
                runtime_index = self._runtime_index_factory(
                    corefs_keys,
                    sqlcipher_key,
                )
        except Exception:
            if runtime_index is not None:
                runtime_index.clear_unlocked_state()
            if corefs_session is not None:
                try:
                    corefs_session.close()
                except Exception:
                    logger.error(
                        "Failed to close incompatible CoreFS session",
                        exc_info=True,
                    )
            _zero_deks(copied_deks)
            raise
        return UnlockSession(
            user_id=user_id,
            deks=copied_deks,
            expires_at=self._now() + SESSION_TTL,
            corefs_keys=corefs_keys,
            corefs_session=corefs_session,
            runtime_index=runtime_index,
        )

    def _ensure_running_locked(self) -> None:
        if self._shut_down:
            raise RuntimeError("Unlock session store is shut down")

    def _begin_construction(self) -> None:
        with self._construction_condition:
            self._ensure_running_locked()
            self._active_constructions += 1

    def _finish_construction(self) -> None:
        with self._construction_condition:
            self._active_constructions -= 1
            if self._active_constructions == 0:
                self._construction_condition.notify_all()

    def _wait_for_constructions(self) -> None:
        with self._construction_condition:
            while self._active_constructions:
                self._construction_condition.wait()

    def _destroy_unpublished_session(self, session: UnlockSession) -> None:
        try:
            runtime_index = getattr(session, "runtime_index", None)
            if runtime_index is not None:
                runtime_index.clear_unlocked_state()
            if session.corefs_session is not None:
                session.corefs_session.close()
        except Exception:
            logger.error(
                "Failed to close unpublished CoreFS session",
                exc_info=True,
            )
        finally:
            _zero_deks(session.deks)

    def _finish_close_record(self, record: _SessionCloseRecord) -> None:
        try:
            runtime_index = getattr(record.session, "runtime_index", None)
            if runtime_index is not None:
                runtime_index.clear_unlocked_state()
            if record.session.corefs_session is not None:
                record.session.corefs_session.close()
        except Exception as exc:
            record.error = exc
            logger.error("Failed to close CoreFS session: %s", exc, exc_info=True)
        finally:
            _zero_deks(record.session.deks)
            with self._lock:
                if self._closing_sessions.get(id(record.session)) is record:
                    self._closing_sessions.pop(id(record.session), None)
            record.done.set()

    @staticmethod
    def _begin_close_record(record: _SessionCloseRecord) -> None:
        native_session = record.session.corefs_session
        begin_close = (
            None if native_session is None else getattr(native_session, "begin_close", None)
        )
        if begin_close is None:
            if native_session is not None:
                record.error = RuntimeError(
                    "CoreFS native session does not implement begin_close"
                )
                logger.error("%s", record.error)
            return
        try:
            begin_close()
        except Exception as exc:
            record.error = exc
            logger.error(
                "Failed to terminalize CoreFS session: %s",
                exc,
                exc_info=True,
            )

    def _run_cleanup(self, cleanup: _CleanupBatch) -> None:
        for record in cleanup.records:
            self._begin_close_record(record)
        for record in cleanup.records:
            self._finish_close_record(record)
        for key in cleanup.sqlcipher_keys:
            _zero_dek(key)

    @staticmethod
    def _wait_for_records(records: list[_SessionCloseRecord]) -> None:
        for record in records:
            record.done.wait()

    @staticmethod
    async def _to_thread(
        function: Callable[..., Any],
        *args: object,
        _cancel_result: Callable[[Any], Any] | None = None,
        **kwargs: object,
    ) -> Any:
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        cancellation_requested = await UnlockSessionStore._drain_worker(worker)
        result = worker.result()
        if cancellation_requested:
            if _cancel_result is not None:
                cleanup_worker = asyncio.create_task(
                    asyncio.to_thread(_cancel_result, result)
                )
                await UnlockSessionStore._drain_worker(cleanup_worker)
                cleanup_worker.result()
            raise asyncio.CancelledError
        return result

    @staticmethod
    async def _drain_worker(worker: asyncio.Task[Any]) -> bool:
        cancellation_requested = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancellation_requested = True
        return cancellation_requested

    def _shutdown_blocking(self) -> None:
        self._wait_for_constructions()
        cleanup = _CleanupBatch()
        try:
            with self._lock:
                already_closing = list(self._closing_sessions.values())
                if self._sessions or self._sqlcipher_key is not None:
                    # Process shutdown is not an explicit revocation. Preserve
                    # the parent-scoped dev snapshot for a replacement child.
                    cleanup.extend(self._apply_state_locked({}, None))

            self._run_cleanup(cleanup)
            self._wait_for_records(already_closing)
        finally:
            with self._construction_condition:
                self._active_shutdowns -= 1
                self._construction_condition.notify_all()

    async def shutdown(self) -> None:
        with self._construction_condition:
            self._shut_down = True
            self._active_shutdowns += 1

        try:
            worker = asyncio.create_task(asyncio.to_thread(self._shutdown_blocking))
        except Exception:
            with self._construction_condition:
                self._active_shutdowns -= 1
                self._construction_condition.notify_all()
            raise

        cancellation_requested = await self._drain_worker(worker)

        worker.result()
        if cancellation_requested:
            raise asyncio.CancelledError

    def _restore_snapshot(self) -> None:
        if self._snapshot is None:
            return
        try:
            payload = self._snapshot.load()
            if payload is None:
                return
            sessions, sqlcipher_key, discarded_sessions = self._decode_snapshot(payload)
            self._sessions = sessions
            self._sqlcipher_key = sqlcipher_key
            self._rebuild_latest_deks_locked()
            if discarded_sessions:
                try:
                    self._snapshot.write(self._snapshot_payload(sessions, sqlcipher_key))
                except Exception as exc:
                    logger.warning(
                        "Failed to clean unusable dev session snapshot: %s",
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
        discarded_sessions = False
        for raw_session in raw_sessions:
            if not isinstance(raw_session, dict):
                raise ValueError("Invalid snapshot session")
            token = raw_session["token"]
            user_id = raw_session["userId"]
            expires_at = _parse_expiry(raw_session["expiresAt"])
            raw_deks = raw_session["deks"]
            had_corefs_keys = raw_session["hadCorefsKeys"]
            if not isinstance(token, str) or not isinstance(user_id, int):
                raise ValueError("Invalid snapshot identity")
            if not isinstance(raw_deks, dict):
                raise ValueError("Invalid snapshot DEKs")
            if not isinstance(had_corefs_keys, bool):
                raise ValueError("Invalid snapshot CoreFS marker")
            deks = {
                str(domain): _decode_key(encoded_key)
                for domain, encoded_key in raw_deks.items()
            }
            if expires_at <= now or had_corefs_keys:
                discarded_sessions = True
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
        return sessions, sqlcipher_key, discarded_sessions

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
                    "hadCorefsKeys": (
                        session.corefs_keys is not None
                        or session.corefs_session is not None
                    ),
                }
                for token, session in sessions.items()
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


async def get_active_dek_async(
    user_id: int,
    domain: str = DEFAULT_DOMAIN,
) -> bytes | None:
    return await unlock_session_store.get_active_dek_async(user_id, domain)


def get_active_deks(user_id: int) -> dict[str, bytes] | None:
    return unlock_session_store.get_active_deks(user_id)


def set_sqlcipher_key(key: bytes) -> None:
    unlock_session_store.set_sqlcipher_key(key)


def get_sqlcipher_key() -> bytes | None:
    return unlock_session_store.get_sqlcipher_key()


def clear_sqlcipher_key() -> None:
    unlock_session_store.clear_sqlcipher_key()
