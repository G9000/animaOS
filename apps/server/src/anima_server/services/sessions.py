from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ctypes
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Condition, Event, RLock
from typing import Any

import anima_core

from anima_server.services.agent.embedding_resolution import (
    configured_embedding_fingerprint,
)
from anima_server.services.core import get_core_dir, get_core_id
from anima_server.services.corefs.indexer import CoreFSProgressiveIndex, ReadinessState
from anima_server.services.dev_session_snapshot import DevSessionSnapshot

SESSION_TTL = timedelta(hours=24)

DEFAULT_DOMAIN = "memories"

logger = logging.getLogger(__name__)


def _create_native_corefs_session() -> object:
    return anima_core.CorefsSession(str(get_core_dir()), get_core_id())


def _create_runtime_index(
    _corefs_keys: object | None,
    sqlcipher_key: bytes | None,
) -> CoreFSProgressiveIndex | None:
    from anima_server.config import settings

    if not settings.runtime_instance_data_dir:
        return None
    if sqlcipher_key is None:
        passphrase = settings.core_passphrase.strip()
        if not passphrase:
            return None
        from anima_server.services.core import get_sqlcipher_kdf_salt
        from anima_server.services.crypto import derive_sqlcipher_key

        sqlcipher_key = derive_sqlcipher_key(
            passphrase,
            get_sqlcipher_kdf_salt(),
        )
    local_instance_id = Path(settings.runtime_instance_data_dir).name
    index = CoreFSProgressiveIndex(get_core_id())
    index.unlock(
        sqlcipher_key=sqlcipher_key,
        local_instance_id=local_instance_id,
    )
    # Claim the active embedding generation before this index can be
    # published through an unlock session. Resolve it through a dependency
    # leaf so process-global snapshot restoration does not import the CoreFS
    # rebuild stack while this sessions module is still initializing.
    index.begin_runtime_embedding_rebuild(
        embedding_fingerprint=configured_embedding_fingerprint(settings),
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
    # PCF-008 may populate this only after authenticating the global cutover
    # marker. Domain slices must fail closed while it is absent.
    content_authority: dict[str, object] | None = field(
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
            Callable[[object | None, bytes | None], CoreFSProgressiveIndex | None] | None
        ) = None,
        on_session_published: Callable[[UnlockSession], None] | None = None,
    ) -> None:
        self._lock = RLock()
        self._runtime_conversion_lock = RLock()
        self._construction_condition = Condition(self._lock)
        self._snapshot = snapshot
        self._corefs_session_factory = corefs_session_factory or _create_native_corefs_session
        self._runtime_index_factory = runtime_index_factory or _create_runtime_index
        self._on_session_published = on_session_published
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
                cleanup.extend(self._commit_locked(next_sessions, self._sqlcipher_key))
            self._run_cleanup(cleanup)
            self._notify_session_published(session)
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
        preserve_existing_tokens: bool = False,
        before_publish: Callable[[UnlockSession], None] | None = None,
    ) -> str:
        self._begin_construction()
        replacement: UnlockSession | None = None
        cleanup = _CleanupBatch()
        try:
            token = secrets.token_urlsafe(32)
            replacement = self._new_session(user_id, deks, corefs_keys)
            if before_publish is not None:
                before_publish(replacement)
            with self._lock:
                self._ensure_running_locked()
                cleanup.extend(self._purge_expired_locked())
                next_sessions = {
                    current_token: session
                    for current_token, session in self._sessions.items()
                    if session.user_id != user_id
                }
                if preserve_existing_tokens:
                    next_sessions.update(
                        {
                            current_token: replacement
                            for current_token, session in self._sessions.items()
                            if session.user_id == user_id
                        }
                    )
                next_sessions[token] = replacement
                cleanup.extend(self._commit_locked(next_sessions, self._sqlcipher_key))
            self._run_cleanup(cleanup)
            self._notify_session_published(replacement)
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
        if session is not None and session.runtime_index is None:
            return self._repair_runtime_index(token)
        return session

    def _repair_runtime_index(
        self,
        token: str,
    ) -> UnlockSession | None:
        """Attach a Runtime index after startup prerequisites become available."""
        with self._runtime_conversion_lock:
            with self._lock:
                session = self._sessions.get(token)
                if session is None or session.expires_at <= self._now():
                    return None
                if session.runtime_index is not None:
                    return session
                sqlcipher_key = self._sqlcipher_key

            runtime_index = self._runtime_index_factory(
                session.corefs_keys,
                sqlcipher_key,
            )
            if runtime_index is None:
                return session

            try:
                self._convert_runtime_index_rows(
                    runtime_index,
                    user_id=session.user_id,
                    memory_dek=session.deks.get(DEFAULT_DOMAIN),
                )
                repaired = replace(session, runtime_index=runtime_index)
                with self._lock:
                    current = self._sessions.get(token)
                    if current is not session:
                        runtime_index.clear_unlocked_state()
                        return current
                    self._sessions = {
                        current_token: repaired if current_session is session else current_session
                        for current_token, current_session in self._sessions.items()
                    }
                    self._rebuild_latest_deks_locked()
            except Exception:
                runtime_index.clear_unlocked_state()
                raise

        self._notify_session_published(repaired)
        return repaired

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

    def revoke_and_clear_sqlcipher_key_if_idle(self, token: str | None) -> bool:
        """Revoke one token and release the database key only after the last session."""
        cleanup = _CleanupBatch()
        with self._lock:
            now = self._now()
            next_sessions = {
                current_token: session
                for current_token, session in self._sessions.items()
                if current_token != token and session.expires_at > now
            }
            became_idle = not next_sessions
            next_key = None if became_idle else self._sqlcipher_key
            cleanup.extend(self._commit_locked(next_sessions, next_key))
        self._run_cleanup(cleanup)
        return became_idle

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
            if self._active_constructions or self._active_shutdowns or self._closing_sessions:
                raise RuntimeError("Unlock session store cannot start while teardown is active")
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

    def get_active_runtime_index(
        self,
        user_id: int,
    ) -> CoreFSProgressiveIndex | None:
        indexes = self.get_active_runtime_indexes(user_id)
        if not indexes:
            return None
        return next(
            (
                index
                for index in reversed(indexes)
                if index.snapshot().state is ReadinessState.READY
            ),
            indexes[-1],
        )

    def get_active_runtime_indexes(
        self,
        user_id: int,
    ) -> tuple[CoreFSProgressiveIndex, ...]:
        cleanup = _CleanupBatch()
        with self._lock:
            cleanup.extend(self._purge_expired_locked())
            indexes = tuple(
                session.runtime_index
                for session in self._sessions.values()
                if session.user_id == user_id and session.runtime_index is not None
            )
        self._run_cleanup(cleanup)
        return tuple(dict.fromkeys(indexes))

    def get_active_sessions(self, user_id: int) -> tuple[UnlockSession, ...]:
        cleanup = _CleanupBatch()
        with self._lock:
            cleanup.extend(self._purge_expired_locked())
            sessions = tuple(
                session for session in self._sessions.values() if session.user_id == user_id
            )
        self._run_cleanup(cleanup)
        return sessions

    def get_all_active_sessions(self) -> tuple[UnlockSession, ...]:
        cleanup = _CleanupBatch()
        with self._lock:
            cleanup.extend(self._purge_expired_locked())
            sessions = tuple(self._sessions.values())
        self._run_cleanup(cleanup)
        return sessions

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
                cleanup.extend(self._commit_locked(dict(self._sessions), copied_key))
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
            token: session for token, session in self._sessions.items() if session.expires_at > now
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
            self._snapshot.write(self._snapshot_payload(next_sessions, next_sqlcipher_key))
        return self._apply_state_locked(next_sessions, next_sqlcipher_key)

    def _apply_state_locked(
        self,
        next_sessions: dict[str, UnlockSession],
        next_sqlcipher_key: bytes | None,
    ) -> _CleanupBatch:
        retained_session_ids = {id(session) for session in next_sessions.values()}
        removed_sessions = {
            id(session): session
            for session in self._sessions.values()
            if id(session) not in retained_session_ids
        }.values()
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

        if previous_sqlcipher_key is not None and previous_sqlcipher_key is not next_sqlcipher_key:
            cleanup.sqlcipher_keys.append(previous_sqlcipher_key)
        return cleanup

    def _new_session(
        self,
        user_id: int,
        deks: dict[str, bytes],
        corefs_keys: object | None,
    ) -> UnlockSession:
        copied_deks = {domain: _copy_key(dek) for domain, dek in deks.items()}
        corefs_session: object | None = None
        runtime_index: CoreFSProgressiveIndex | None = None
        try:
            corefs_session = None if corefs_keys is None else self._corefs_session_factory()
            if corefs_session is not None and not callable(
                getattr(corefs_session, "begin_close", None)
            ):
                raise RuntimeError("CoreFS native session does not implement begin_close")
            with self._lock:
                sqlcipher_key = self._sqlcipher_key
            runtime_index = self._runtime_index_factory(
                corefs_keys,
                sqlcipher_key,
            )
            with self._runtime_conversion_lock:
                self._convert_runtime_index_rows(
                    runtime_index,
                    user_id=user_id,
                    memory_dek=copied_deks.get(DEFAULT_DOMAIN),
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

    @staticmethod
    def _convert_runtime_index_rows(
        runtime_index: CoreFSProgressiveIndex | None,
        *,
        user_id: int,
        memory_dek: bytes | None,
    ) -> bool:
        if runtime_index is None:
            return True
        from anima_server.db.runtime import get_runtime_session_factory
        from anima_server.services.corefs.sealed_runtime import (
            convert_legacy_runtime_rows,
        )

        try:
            factory = get_runtime_session_factory()
        except RuntimeError:
            # Restored development sessions are decoded before the lifespan
            # initializes Runtime. The startup handoff retries after Alembic.
            return False
        with factory() as runtime_db:
            convert_legacy_runtime_rows(
                runtime_db,
                index=runtime_index,
                user_id=user_id,
                memory_dek=memory_dek,
            )
            runtime_db.commit()
        return True

    def initialize_runtime_indexes(self) -> None:
        """Finish restored-session Runtime setup after the DB is migrated."""
        with self._lock:
            sessions = dict(self._sessions)
            sqlcipher_key = self._sqlcipher_key

        replacements: dict[str, UnlockSession] = {}
        created_indexes: list[CoreFSProgressiveIndex] = []
        try:
            with self._runtime_conversion_lock:
                for token, session in sessions.items():
                    runtime_index = session.runtime_index
                    if runtime_index is None:
                        runtime_index = self._runtime_index_factory(
                            None,
                            sqlcipher_key,
                        )
                        if runtime_index is not None:
                            created_indexes.append(runtime_index)
                    self._convert_runtime_index_rows(
                        runtime_index,
                        user_id=session.user_id,
                        memory_dek=session.deks.get(DEFAULT_DOMAIN),
                    )
                    replacements[token] = replace(
                        session,
                        runtime_index=runtime_index,
                    )
        except Exception:
            for runtime_index in created_indexes:
                runtime_index.clear_unlocked_state()
            raise

        with self._lock:
            self._sessions = replacements
            self._rebuild_latest_deks_locked()
        notified_indexes: set[int] = set()
        for session in replacements.values():
            index_identity = id(session.runtime_index)
            if index_identity in notified_indexes:
                continue
            notified_indexes.add(index_identity)
            self._notify_session_published(session)

    def _notify_session_published(self, session: UnlockSession) -> None:
        callback = self._on_session_published
        if callback is None:
            return
        try:
            callback(session)
        except Exception:
            logger.exception("Failed to schedule unlocked Runtime index rebuild")

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
                record.error = RuntimeError("CoreFS native session does not implement begin_close")
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
                cleanup_worker = asyncio.create_task(asyncio.to_thread(_cancel_result, result))
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
        raw_sqlcipher_key = payload["sqlcipherKey"]
        sqlcipher_key = None if raw_sqlcipher_key is None else _decode_key(raw_sqlcipher_key)
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
                str(domain): _decode_key(encoded_key) for domain, encoded_key in raw_deks.items()
            }
            if expires_at <= now or had_corefs_keys:
                discarded_sessions = True
                _zero_deks(deks)
                continue
            sessions[token] = UnlockSession(
                user_id=user_id,
                deks=deks,
                expires_at=expires_at,
                runtime_index=self._runtime_index_factory(
                    None,
                    sqlcipher_key,
                ),
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
                        session.corefs_keys is not None or session.corefs_session is not None
                    ),
                }
                for token, session in sessions.items()
            ],
            "sqlcipherKey": (
                None if sqlcipher_key is None else base64.b64encode(sqlcipher_key).decode("ascii")
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
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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
def _schedule_published_session_rebuild(session: UnlockSession) -> None:
    # PCF-004/005/006 prepare and verify one combined authored-content shadow
    # only after SQLCipher, Runtime, and CoreFS keys are live. Legacy routes
    # remain authoritative until the global PCF-008 cutover marker is accepted.
    if session.corefs_session is not None and session.corefs_keys is not None:
        from anima_server.config import settings
        from anima_server.db.runtime import get_runtime_session_factory
        from anima_server.db.session import get_user_session_factory
        from anima_server.services.corefs.asset_migration import (
            prepare_portable_content_validation_catalog,
            record_asset_migration_failure,
        )
        from anima_server.services.corefs.conversation_migration import (
            record_conversation_migration_failure,
        )
        from anima_server.services.corefs.diary_migration import (
            prepare_diary_validation_catalog,
            record_diary_migration_failure,
        )

        try:
            with get_user_session_factory(session.user_id)() as db:
                try:
                    runtime_factory = get_runtime_session_factory()
                except RuntimeError:
                    prepare_diary_validation_catalog(session=session, db=db)
                else:
                    with runtime_factory() as runtime_db:
                        prepare_portable_content_validation_catalog(
                            session=session,
                            soul_db=db,
                            runtime_db=runtime_db,
                            transcripts_dir=settings.data_dir / "transcripts",
                        )
        except Exception as exc:
            record_diary_migration_failure(user_id=session.user_id, error=exc)
            record_conversation_migration_failure(user_id=session.user_id, error=exc)
            record_asset_migration_failure(user_id=session.user_id, error=exc)
            logger.exception("PCF-004/005/006 inactive content preparation failed")
    from anima_server.services.corefs.migration import schedule_unlocked_rebuild

    schedule_unlocked_rebuild(session)


unlock_session_store = UnlockSessionStore(
    snapshot=DevSessionSnapshot.from_environment(),
    on_session_published=_schedule_published_session_rebuild,
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


def active_unlock_sessions(user_id: int) -> tuple[UnlockSession, ...]:
    return unlock_session_store.get_active_sessions(user_id)


def all_active_unlock_sessions() -> tuple[UnlockSession, ...]:
    return unlock_session_store.get_all_active_sessions()


def active_runtime_indexes(user_id: int) -> tuple[CoreFSProgressiveIndex, ...]:
    return unlock_session_store.get_active_runtime_indexes(user_id)


def set_sqlcipher_key(key: bytes) -> None:
    unlock_session_store.set_sqlcipher_key(key)


def get_sqlcipher_key() -> bytes | None:
    return unlock_session_store.get_sqlcipher_key()


def clear_sqlcipher_key() -> None:
    unlock_session_store.clear_sqlcipher_key()
