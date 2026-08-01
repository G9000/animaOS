from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable
from threading import Lock, Thread, current_thread
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.models.corefs_runtime import (
    CoreFSBlindToken,
    CoreFSIndexCheckpoint,
    CoreFSIndexEntry,
)
from anima_server.services.agent.embedding_resolution import (
    configured_embedding_fingerprint,
)
from anima_server.services.corefs import logical
from anima_server.services.corefs.indexer import CoreFSProgressiveIndex, ReadinessState
from anima_server.services.sessions import UnlockSession

_INDEX_READ_CHUNK_BYTES = 64 * 1024
_MAX_INDEXABLE_OBJECT_BYTES = 16 * 1024 * 1024
_INDEX_VERSION = 1
_BLIND_CHECKPOINT_FAMILY = "__blind__"
logger = logging.getLogger(__name__)

_rebuild_workers_lock = Lock()
_rebuild_workers: WeakKeyDictionary[CoreFSProgressiveIndex, Thread] = WeakKeyDictionary()
_rebuild_pending: WeakKeyDictionary[CoreFSProgressiveIndex, bool] = WeakKeyDictionary()


class _NonIndexableCoreFSObject(ValueError):
    """An authenticated object that is valid but cannot participate in text search."""


class _ConfiguredEmbeddingQuery:
    def __init__(self, fingerprint: str) -> None:
        self.corefs_embedding_fingerprint = fingerprint

    def __call__(self, text: str) -> tuple[float, ...]:
        return embed_configured_query(text)


def reconcile_authenticated_catalog(
    session: UnlockSession,
) -> logical.CoreFsValidationSnapshot:
    """Publish navigation readiness from an authenticated native catalog."""
    if (
        session.runtime_index is None
        or session.corefs_session is None
        or session.corefs_keys is None
    ):
        raise ValueError("CoreFS reconciliation requires an unlocked session")
    selected = logical.select_validation_snapshot(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    snapshot = session.runtime_index.snapshot()
    if snapshot.catalog_generation != selected.generation or snapshot.state in {
        ReadinessState.OPENING_CORE,
        ReadinessState.VALIDATING_CORE,
    }:
        session.runtime_index.begin_catalog()
        session.runtime_index.publish_catalog(
            catalog_generation=selected.generation,
            families={},
        )
    return selected


def rebuild_unlocked_search(
    session: UnlockSession,
    *,
    embedder: Callable[[str], tuple[float, ...]] | None = None,
    runtime_db: Session | None = None,
) -> logical.CoreFsValidationSnapshot:
    """Rebuild unlock-scoped search state from one authenticated catalog snapshot."""
    if (
        session.runtime_index is None
        or session.corefs_session is None
        or session.corefs_keys is None
    ):
        raise ValueError("CoreFS reconciliation requires an unlocked session")

    selected = logical.select_validation_snapshot(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    index = session.runtime_index
    user_id = getattr(session, "user_id", None)
    runtime_embedding_total = 0
    runtime_embedding_failures: tuple[tuple[str, int], ...] = ()
    if runtime_db is not None and embedder is not None and isinstance(user_id, int):
        from anima_server.services.corefs.sealed_runtime import (
            rebuild_runtime_embeddings,
        )

        rebuild_runtime_embeddings(
            runtime_db,
            index=index,
            user_id=user_id,
            embedder=embedder,
        )
        runtime_embedding_total, runtime_embedding_failures = (
            index.runtime_embedding_rebuild_status()
        )
    prior = index.snapshot()
    if prior.catalog_generation != selected.generation:
        index.begin_catalog()
        index.publish_catalog(
            catalog_generation=selected.generation,
            families={},
        )
        prior = index.snapshot()
    reusable_blind_generation = prior.blind_index_generation == selected.generation
    if runtime_db is not None and not reusable_blind_generation:
        reusable_blind_generation = _restore_blind_generation(
            runtime_db,
            index=index,
            generation=selected.generation,
        )
    entries, walk_failures = _walk_authenticated_files(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
        selected=selected,
    )
    family_counts = Counter(entry["family"] for entry in entries)
    degraded: dict[str, set[str]] = {}
    for failure in walk_failures:
        family = failure["family"]
        object_id = failure["object_id"]
        degraded.setdefault(family, set()).add(object_id)
    for family, object_ids in degraded.items():
        family_counts[family] += len(object_ids)
    if runtime_embedding_total:
        family_counts["runtime_embeddings"] = runtime_embedding_total
        if runtime_embedding_failures:
            degraded["runtime_embeddings"] = {
                f"{source_type}:{source_id}"
                for source_type, source_id in runtime_embedding_failures
            }

    prior = index.snapshot()
    # Same-unlock retries can retain already decrypted text in memory and
    # resume from the durable opaque rows below. A new process deliberately
    # rehydrates every plaintext document because PCF-003 forbids persisting
    # decrypted search text or semantic vectors to Runtime storage.
    resuming = (
        runtime_db is not None
        and prior.catalog_generation == selected.generation
        and prior.state
        in {
            ReadinessState.TEXT_INDEXING,
            ReadinessState.SEMANTIC_INDEXING,
        }
    )
    completed_entries: set[tuple[str, str, str]] = set()
    if runtime_db is not None:
        completed_entries = _prepare_durable_index_state(
            runtime_db,
            index=index,
            generation=selected.generation,
            entries=entries,
            reset=not resuming,
        )

    if not resuming:
        index.begin_catalog(
            preserve_blind_generation=reusable_blind_generation,
        )
        index.publish_catalog(
            catalog_generation=selected.generation,
            families=dict(family_counts),
            degraded={family: tuple(sorted(object_ids)) for family, object_ids in degraded.items()},
        )
        if runtime_embedding_total:
            index.publish_runtime_embedding_readiness()
        if not reusable_blind_generation:
            index.begin_blind_generation(
                generation=selected.generation,
                expected_count=len(entries),
            )
            for entry in entries:
                index.add_blind_token(
                    generation=selected.generation,
                    value=entry["path"],
                    object_id=entry["stable_id"],
                )
            if runtime_db is not None:
                _persist_blind_generation(
                    runtime_db,
                    index=index,
                    generation=selected.generation,
                    entries=entries,
                )
            index.commit_blind_generation(selected.generation)
        index.begin_text_indexing()

    indexed_by_revision = {
        (object_id, revision): (object_id, text)
        for object_id, revision, _family, text in index.indexed_texts()
    }
    indexed: list[tuple[str, str]] = list(indexed_by_revision.values())
    text_failed = False
    for entry in entries:
        durable_key = _durable_entry_key(entry)
        in_memory = indexed_by_revision.get((entry["stable_id"], entry["revision"]))
        if in_memory is not None:
            if runtime_db is not None and durable_key not in completed_entries:
                _record_text_progress(
                    runtime_db,
                    index=index,
                    generation=selected.generation,
                    entry=entry,
                    total=family_counts[entry["family"]],
                    status="text_indexed",
                )
                completed_entries.add(durable_key)
            continue
        if durable_key in completed_entries:
            continue
        try:
            text = _read_authenticated_text(
                corefs_session=session.corefs_session,
                keys=session.corefs_keys,
                selected=selected,
                path=entry["path"],
            )
        except _NonIndexableCoreFSObject as exc:
            index.skip_text(
                family=entry["family"],
                object_id=entry["stable_id"],
                revision=entry["revision"],
            )
            index.mark_family_failure(
                family=entry["family"],
                object_id=entry["stable_id"],
            )
            if runtime_db is not None:
                _record_text_progress(
                    runtime_db,
                    index=index,
                    generation=selected.generation,
                    entry=entry,
                    total=family_counts[entry["family"]],
                    status="text_skipped",
                    error=exc,
                )
                completed_entries.add(durable_key)
            continue
        except (UnicodeDecodeError, ValueError) as exc:
            text_failed = True
            index.mark_family_failure(
                family=entry["family"],
                object_id=entry["stable_id"],
            )
            if runtime_db is not None:
                _record_text_progress(
                    runtime_db,
                    index=index,
                    generation=selected.generation,
                    entry=entry,
                    total=family_counts[entry["family"]],
                    status="text_failed",
                    error=exc,
                )
            continue
        index.index_text(
            family=entry["family"],
            object_id=entry["stable_id"],
            revision=entry["revision"],
            text=text,
        )
        index.clear_family_failure(
            family=entry["family"],
            object_id=entry["stable_id"],
        )
        indexed.append((entry["stable_id"], text))
        indexed_by_revision[(entry["stable_id"], entry["revision"])] = (
            entry["stable_id"],
            text,
        )
        if runtime_db is not None:
            _record_text_progress(
                runtime_db,
                index=index,
                generation=selected.generation,
                entry=entry,
                total=family_counts[entry["family"]],
                status="text_indexed",
            )
            completed_entries.add(durable_key)

    semantic_failed = False
    if embedder is not None and not text_failed:
        fingerprint_value = getattr(
            embedder,
            "corefs_embedding_fingerprint",
            None,
        )
        embedding_fingerprint = (
            fingerprint_value if isinstance(fingerprint_value, str) and fingerprint_value else None
        )
        if (
            index.snapshot().state is not ReadinessState.SEMANTIC_INDEXING
            or embedding_fingerprint is not None
        ):
            index.begin_semantic_indexing(
                embedding_fingerprint=embedding_fingerprint,
            )
        for object_id, text in indexed:
            if index.has_vector(object_id):
                continue
            try:
                vector = embedder(text)
                index.index_vector(
                    object_id=object_id,
                    vector=vector,
                    embedding_fingerprint=embedding_fingerprint,
                )
                family = next(
                    entry["family"] for entry in entries if entry["stable_id"] == object_id
                )
                index.clear_family_failure(
                    family=family,
                    object_id=object_id,
                )
            except (TypeError, ValueError):
                semantic_failed = True
                family = next(
                    entry["family"] for entry in entries if entry["stable_id"] == object_id
                )
                index.mark_family_failure(family=family, object_id=object_id)
    runtime_embedding_failed = bool(runtime_embedding_failures)
    if not text_failed and not semantic_failed and not runtime_embedding_failed:
        index.finish()
    if (
        runtime_db is not None
        and not text_failed
        and not semantic_failed
        and not runtime_embedding_failed
    ):
        _finish_durable_index_state(
            runtime_db,
            index=index,
            generation=selected.generation,
        )
    return selected


def schedule_unlocked_rebuild(
    session: UnlockSession,
    *,
    rerun_if_active: bool = False,
) -> bool:
    """Start at most one background rebuild for an unlocked Runtime index."""
    index = session.runtime_index
    if index is None:
        raise ValueError("CoreFS rebuild requires an unlocked Runtime index")
    with _rebuild_workers_lock:
        current = _rebuild_workers.get(index)
        if current is not None and current.is_alive():
            if rerun_if_active:
                _rebuild_pending[index] = True
            return False
        _rebuild_pending.pop(index, None)
        worker = Thread(
            target=_run_scheduled_rebuild,
            args=(session, index),
            name=f"corefs-rebuild-{index.core_id}",
            daemon=True,
        )
        _rebuild_workers[index] = worker
        worker.start()
    return True


def initialize_catalog_if_idle(
    index: CoreFSProgressiveIndex,
    generation: int,
) -> bool:
    """Publish an empty catalog only when no rebuild can overwrite it."""

    def initialize() -> None:
        if index.snapshot().catalog_generation is not None:
            return
        index.begin_catalog()
        index.publish_catalog(catalog_generation=generation, families={})

    return _run_when_rebuild_idle(index, initialize)


def reconcile_catalog_if_idle(session: UnlockSession) -> bool:
    """Reconcile only when no rebuild can publish an older catalog afterward."""
    index = session.runtime_index
    if index is None:
        raise ValueError("CoreFS reconciliation requires an unlocked Runtime index")

    return _run_when_rebuild_idle(
        index,
        lambda: reconcile_authenticated_catalog(session),
    )


def _run_when_rebuild_idle(
    index: CoreFSProgressiveIndex,
    action: Callable[[], object],
) -> bool:
    with _rebuild_workers_lock:
        current = _rebuild_workers.get(index)
        if current is not None and current.is_alive():
            _rebuild_pending[index] = True
            return False
        action()
    return True


def _run_scheduled_rebuild(
    session: UnlockSession,
    index: CoreFSProgressiveIndex,
) -> None:
    try:
        from anima_server.db.runtime import get_runtime_session_factory

        configured_embedder = _ConfiguredEmbeddingQuery(configured_embedding_fingerprint())
        try:
            runtime_db_factory = get_runtime_session_factory()
        except RuntimeError:
            rebuild_unlocked_search(
                session,
                embedder=configured_embedder,
            )
        else:
            with runtime_db_factory() as runtime_db:
                user_id = getattr(session, "user_id", None)
                has_native_session = getattr(session, "corefs_session", None) is not None
                has_native_keys = getattr(session, "corefs_keys", None) is not None
                if isinstance(user_id, int) and not (has_native_session and has_native_keys):
                    from anima_server.services.corefs.sealed_runtime import (
                        rebuild_runtime_embeddings,
                    )

                    rebuild_runtime_embeddings(
                        runtime_db,
                        index=index,
                        user_id=user_id,
                        embedder=configured_embedder,
                    )
                else:
                    rebuild_unlocked_search(
                        session,
                        embedder=configured_embedder,
                        runtime_db=runtime_db,
                    )
    except Exception:
        logger.exception("CoreFS background rebuild failed")
    finally:
        rerun = False
        with _rebuild_workers_lock:
            if _rebuild_workers.get(index) is current_thread():
                _rebuild_workers.pop(index, None)
                rerun = bool(_rebuild_pending.pop(index, False))
        if rerun:
            schedule_unlocked_rebuild(session)


def refresh_unlocked_semantic_search(session: UnlockSession) -> bool:
    """Invalidate and rebuild semantic vectors after embedding settings change."""
    index = session.runtime_index
    if index is None:
        return False
    fingerprint = configured_embedding_fingerprint()
    index.request_runtime_embedding_refresh(
        embedding_fingerprint=fingerprint,
    )
    if (
        session.corefs_session is not None
        and session.corefs_keys is not None
        and index.snapshot().catalog_generation is not None
    ):
        index.request_semantic_refresh(
            embedding_fingerprint=fingerprint,
        )
    return schedule_unlocked_rebuild(
        session,
        rerun_if_active=True,
    )


def embed_configured_query(text: str) -> tuple[float, ...]:
    from anima_server.services.agent.embeddings import generate_embedding

    vector = asyncio.run(generate_embedding(text))
    if not vector:
        raise ValueError("configured embedding provider returned no vector")
    return tuple(float(value) for value in vector)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _persist_blind_generation(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    generation: int,
    entries: list[dict[str, str]],
) -> None:
    """Atomically replace the committed opaque exact-search generation."""
    runtime_db.execute(
        delete(CoreFSBlindToken).where(
            CoreFSBlindToken.core_id == index.core_id,
            CoreFSBlindToken.local_instance_id == index.local_instance_id,
        )
    )
    runtime_db.execute(
        delete(CoreFSIndexCheckpoint).where(
            CoreFSIndexCheckpoint.core_id == index.core_id,
            CoreFSIndexCheckpoint.local_instance_id == index.local_instance_id,
            CoreFSIndexCheckpoint.family == _BLIND_CHECKPOINT_FAMILY,
        )
    )
    unique_entries: dict[tuple[str, bytes, str], CoreFSBlindToken] = {}
    for entry in entries:
        token = index.blind_token(entry["path"])
        key = (entry["family"], token, entry["stable_id"])
        unique_entries[key] = CoreFSBlindToken(
            core_id=index.core_id,
            local_instance_id=index.local_instance_id,
            family=entry["family"],
            generation=generation,
            token=token,
            object_id=entry["stable_id"],
            object_id_hash=_digest(entry["stable_id"]),
            revision_hash=_digest(entry["revision"]),
        )
    runtime_db.add_all(unique_entries.values())
    runtime_db.add(
        CoreFSIndexCheckpoint(
            core_id=index.core_id,
            local_instance_id=index.local_instance_id,
            family=_BLIND_CHECKPOINT_FAMILY,
            catalog_generation=generation,
            index_version=_INDEX_VERSION,
            cursor_hash=None,
            completed_count=len(unique_entries),
            total_count=len(unique_entries),
            status="ready",
        )
    )
    runtime_db.commit()


def _restore_blind_generation(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    generation: int,
) -> bool:
    """Restore one atomically committed exact-search generation after unlock."""
    checkpoint = runtime_db.scalar(
        select(CoreFSIndexCheckpoint).where(
            CoreFSIndexCheckpoint.core_id == index.core_id,
            CoreFSIndexCheckpoint.local_instance_id == index.local_instance_id,
            CoreFSIndexCheckpoint.family == _BLIND_CHECKPOINT_FAMILY,
            CoreFSIndexCheckpoint.catalog_generation == generation,
            CoreFSIndexCheckpoint.index_version == _INDEX_VERSION,
            CoreFSIndexCheckpoint.status == "ready",
        )
    )
    if checkpoint is None:
        return False
    stored = runtime_db.scalars(
        select(CoreFSBlindToken).where(
            CoreFSBlindToken.core_id == index.core_id,
            CoreFSBlindToken.local_instance_id == index.local_instance_id,
            CoreFSBlindToken.generation == generation,
        )
    ).all()
    expected = checkpoint.total_count
    if expected is None or len(stored) != expected:
        return False
    index.load_blind_generation(
        generation=generation,
        entries=tuple((bytes(row.token), row.object_id) for row in stored),
    )
    return True


def _durable_entry_key(entry: dict[str, str]) -> tuple[str, str, str]:
    return (
        entry["family"],
        _digest(entry["stable_id"]),
        _digest(entry["revision"]),
    )


def _durable_entry_checksum(entry: dict[str, str]) -> str:
    family, object_hash, revision_hash = _durable_entry_key(entry)
    return _digest(f"{family}:{object_hash}:{revision_hash}")


def _prepare_durable_index_state(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    generation: int,
    entries: list[dict[str, str]],
    reset: bool,
) -> set[tuple[str, str, str]]:
    completed: set[tuple[str, str, str]] = set()
    totals = Counter(entry["family"] for entry in entries)
    for entry in entries:
        family, object_hash, revision_hash = _durable_entry_key(entry)
        stored = runtime_db.scalar(
            select(CoreFSIndexEntry).where(
                CoreFSIndexEntry.core_id == index.core_id,
                CoreFSIndexEntry.local_instance_id == index.local_instance_id,
                CoreFSIndexEntry.family == family,
                CoreFSIndexEntry.object_id_hash == object_hash,
                CoreFSIndexEntry.revision_hash == revision_hash,
            )
        )
        checksum = _durable_entry_checksum(entry)
        if stored is None:
            stored = CoreFSIndexEntry(
                core_id=index.core_id,
                local_instance_id=index.local_instance_id,
                family=family,
                object_id_hash=object_hash,
                revision_hash=revision_hash,
                catalog_generation=generation,
                index_version=_INDEX_VERSION,
                status="pending",
                checksum=checksum,
            )
            runtime_db.add(stored)
        else:
            stored.catalog_generation = generation
            stored.index_version = _INDEX_VERSION
            stored.checksum = checksum
            if reset:
                stored.status = "pending"
            elif stored.status in {"text_indexed", "text_skipped"}:
                completed.add((family, object_hash, revision_hash))

    for family, total in totals.items():
        checkpoint = runtime_db.scalar(
            select(CoreFSIndexCheckpoint).where(
                CoreFSIndexCheckpoint.core_id == index.core_id,
                CoreFSIndexCheckpoint.local_instance_id == index.local_instance_id,
                CoreFSIndexCheckpoint.family == family,
                CoreFSIndexCheckpoint.catalog_generation == generation,
                CoreFSIndexCheckpoint.index_version == _INDEX_VERSION,
            )
        )
        completed_count = sum(1 for key in completed if key[0] == family)
        if checkpoint is None:
            runtime_db.add(
                CoreFSIndexCheckpoint(
                    core_id=index.core_id,
                    local_instance_id=index.local_instance_id,
                    family=family,
                    catalog_generation=generation,
                    index_version=_INDEX_VERSION,
                    cursor_hash=None,
                    completed_count=completed_count,
                    total_count=total,
                    status="text_indexing",
                )
            )
        else:
            checkpoint.completed_count = 0 if reset else completed_count
            checkpoint.total_count = total
            checkpoint.status = "text_indexing"
            if reset:
                checkpoint.cursor_hash = None
                checkpoint.error_code = None
                checkpoint.error_digest = None
    runtime_db.commit()
    return completed


def _record_text_progress(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    generation: int,
    entry: dict[str, str],
    total: int,
    status: str,
    error: Exception | None = None,
) -> None:
    family, object_hash, revision_hash = _durable_entry_key(entry)
    stored = runtime_db.scalar(
        select(CoreFSIndexEntry).where(
            CoreFSIndexEntry.core_id == index.core_id,
            CoreFSIndexEntry.local_instance_id == index.local_instance_id,
            CoreFSIndexEntry.family == family,
            CoreFSIndexEntry.object_id_hash == object_hash,
            CoreFSIndexEntry.revision_hash == revision_hash,
        )
    )
    if stored is None:
        raise ValueError("durable CoreFS index entry is missing")
    completed_statuses = {"text_indexed", "text_skipped"}
    was_completed = stored.status in completed_statuses
    stored.status = status
    checkpoint = runtime_db.scalar(
        select(CoreFSIndexCheckpoint).where(
            CoreFSIndexCheckpoint.core_id == index.core_id,
            CoreFSIndexCheckpoint.local_instance_id == index.local_instance_id,
            CoreFSIndexCheckpoint.family == family,
            CoreFSIndexCheckpoint.catalog_generation == generation,
            CoreFSIndexCheckpoint.index_version == _INDEX_VERSION,
        )
    )
    if checkpoint is None:
        raise ValueError("durable CoreFS index checkpoint is missing")
    if status in completed_statuses and not was_completed:
        checkpoint.completed_count = min(checkpoint.completed_count + 1, total)
    checkpoint.total_count = total
    checkpoint.cursor_hash = object_hash
    checkpoint.status = "text_indexing" if error is None else "ready_degraded"
    if error is not None:
        terminal_failure_exists = status == "text_skipped" or (
            runtime_db.scalar(
                select(CoreFSIndexEntry.object_id_hash)
                .where(
                    CoreFSIndexEntry.core_id == index.core_id,
                    CoreFSIndexEntry.local_instance_id == index.local_instance_id,
                    CoreFSIndexEntry.family == family,
                    CoreFSIndexEntry.catalog_generation == generation,
                    CoreFSIndexEntry.index_version == _INDEX_VERSION,
                    CoreFSIndexEntry.status == "text_skipped",
                )
                .limit(1)
            )
            is not None
        )
        if status == "text_skipped" or not terminal_failure_exists:
            checkpoint.error_code = type(error).__name__
            checkpoint.error_digest = _digest(str(error))
    elif not index.snapshot().families[family].degraded:
        checkpoint.error_code = None
        checkpoint.error_digest = None
    runtime_db.commit()


def _finish_durable_index_state(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    generation: int,
) -> None:
    degraded = {family for family, value in index.snapshot().families.items() if value.degraded}
    checkpoints = runtime_db.scalars(
        select(CoreFSIndexCheckpoint).where(
            CoreFSIndexCheckpoint.core_id == index.core_id,
            CoreFSIndexCheckpoint.local_instance_id == index.local_instance_id,
            CoreFSIndexCheckpoint.catalog_generation == generation,
            CoreFSIndexCheckpoint.index_version == _INDEX_VERSION,
        )
    ).all()
    for checkpoint in checkpoints:
        checkpoint.status = "ready_degraded" if checkpoint.family in degraded else "ready"
    runtime_db.commit()


def _walk_authenticated_files(
    *,
    corefs_session: Any,
    keys: object,
    selected: logical.CoreFsValidationSnapshot,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    entries: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    cursor: str | None = None
    while True:
        raw = logical.walk_v1(
            corefs_session=corefs_session,
            keys=keys,
            selected=selected,
            root="",
            cursor_after=cursor,
            page_size=100,
            include_directories=False,
        )
        result = _wire_result(raw, selected.generation)
        page_entries = result.get("entries")
        page_errors = result.get("errors")
        if not isinstance(page_entries, list) or not isinstance(page_errors, list):
            raise ValueError("invalid CoreFS walk response")
        for value in page_entries:
            if not isinstance(value, dict) or value.get("kind") != "file":
                continue
            path = value.get("path")
            stable_id = value.get("stableId")
            revision = value.get("revision")
            family = value.get("objectKind")
            if (
                not isinstance(path, str)
                or not path
                or not isinstance(stable_id, str)
                or not stable_id
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision <= 0
                or not isinstance(family, str)
                or not family
            ):
                raise ValueError("invalid CoreFS walk entry")
            entries.append(
                {
                    "path": path,
                    "stable_id": stable_id,
                    "revision": str(revision),
                    "family": family,
                }
            )
        for value in page_errors:
            if not isinstance(value, dict):
                raise ValueError("invalid CoreFS walk error")
            path = value.get("path")
            if isinstance(path, str) and path:
                failures.append(
                    {
                        "family": "unknown",
                        "object_id": path,
                    }
                )
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, dict):
            raise ValueError("invalid CoreFS walk cursor")
        after = next_cursor.get("after")
        if not isinstance(after, str) or not after or after == cursor:
            raise ValueError("invalid CoreFS walk cursor")
        cursor = after
    return entries, failures


def _read_authenticated_text(
    *,
    corefs_session: Any,
    keys: object,
    selected: logical.CoreFsValidationSnapshot,
    path: str,
) -> str:
    chunks: list[bytes] = []
    offset = 0
    while offset <= _MAX_INDEXABLE_OBJECT_BYTES:
        raw = logical.read_chunk_v1(
            corefs_session=corefs_session,
            keys=keys,
            selected=selected,
            path=path,
            offset=offset,
            max_bytes=_INDEX_READ_CHUNK_BYTES,
        )
        if raw is None:
            break
        result = _wire_result(raw, selected.generation)
        encoded = result.get("bytesBase64")
        response_offset = result.get("offset")
        if (
            not isinstance(encoded, str)
            or isinstance(response_offset, bool)
            or response_offset != offset
        ):
            raise ValueError("invalid CoreFS read response")
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid CoreFS read response") from exc
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    if offset > _MAX_INDEXABLE_OBJECT_BYTES:
        raise _NonIndexableCoreFSObject(
            "CoreFS object exceeds the in-memory indexing limit"
        )
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _NonIndexableCoreFSObject(
            "CoreFS object is not UTF-8 text"
        ) from exc


def _wire_result(raw: bytes, generation: int) -> dict[str, object]:
    try:
        payload = json.loads(raw)
        result = payload["result"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid CoreFS logical response") from exc
    if payload.get("version") != "corefs-logical-v1" or not isinstance(result, dict):
        raise ValueError("invalid CoreFS logical response")
    result_generation = result.get("generation")
    if result_generation is not None and result_generation != generation:
        raise ValueError("CoreFS logical response generation changed")
    return result
