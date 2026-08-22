"""Post-cutover task mutations committed only through native CoreFS."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from anima_server.services.corefs import logical
from anima_server.services.corefs.content_authority import (
    invalidate_active_catalog_indexes,
    publish_content_authority_after_mutation,
)
from anima_server.services.corefs.diary_migration import migration_opaque_id
from anima_server.services.corefs.formats import (
    TASK_CONTENT_TYPE,
    TaskDocument,
    encode_task_document,
)
from anima_server.services.corefs.task_authority import (
    CanonicalTaskCatalog,
    CanonicalTaskRecord,
    read_canonical_task_catalog,
)

_MAX_CREATE_ATTEMPTS = 4
_locks_guard = RLock()
_locks: dict[int, RLock] = {}


class TaskMutationError(RuntimeError):
    pass


@contextmanager
def _task_mutation_lock(user_id: int) -> Iterator[None]:
    with _locks_guard:
        lock = _locks.setdefault(user_id, RLock())
    with lock:
        yield


def create_canonical_task(
    *,
    session: Any,
    text: str,
    priority: int,
    due_date: str | None,
) -> TaskDocument:
    user_id = int(session.user_id)
    with _task_mutation_lock(user_id):
        for _attempt in range(_MAX_CREATE_ATTEMPTS):
            catalog = read_canonical_task_catalog(session=session)
            existing_ids = {record.document.legacy_id for record in catalog.tasks}
            legacy_id = _new_legacy_id(existing_ids)
            stable_id = migration_opaque_id("task", str(legacy_id))
            now = _timestamp()
            document = TaskDocument(
                format_version=1,
                stable_id=stable_id,
                legacy_id=legacy_id,
                text=text,
                done=False,
                priority=priority,
                due_date=due_date,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
            try:
                _execute(
                    session=session,
                    catalog=catalog,
                    mutation={
                        "operation": "create_file",
                        "path": f"task-{legacy_id}.json",
                        "stableId": stable_id,
                        "kind": "task",
                        "contentType": TASK_CONTENT_TYPE,
                        "bodyEncoding": "utf-8",
                    },
                    body=_encode(document),
                    expected_stable_id=stable_id,
                    expected_revision=1,
                )
            except ValueError as exc:
                if str(exc) in {
                    "corefs_mutation_collision",
                    "corefs_mutation_optimistic_conflict",
                }:
                    continue
                raise
            return document
    raise TaskMutationError("Canonical task identity allocation did not converge.")


def update_canonical_task(
    *,
    session: Any,
    legacy_id: int,
    text: str | None,
    done: bool | None,
    priority: int | None,
    due_date: str | None,
    due_date_present: bool,
) -> TaskDocument | None:
    with _task_mutation_lock(int(session.user_id)):
        catalog = read_canonical_task_catalog(session=session)
        record = _find_task(catalog, legacy_id)
        if record is None:
            return None
        current = record.document
        now = _timestamp()
        next_done = current.done if done is None else done
        completed_at = current.completed_at
        if done is not None:
            completed_at = now if done else None
        document = TaskDocument(
            format_version=current.format_version,
            stable_id=current.stable_id,
            legacy_id=current.legacy_id,
            text=current.text if text is None else text,
            done=next_done,
            priority=current.priority if priority is None else priority,
            due_date=(due_date if due_date_present else current.due_date),
            completed_at=completed_at,
            created_at=current.created_at,
            updated_at=now,
        )
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "write_file",
                "target": {"stableId": current.stable_id},
                "expectedRevision": record.revision,
                "contentType": TASK_CONTENT_TYPE,
                "bodyEncoding": "utf-8",
            },
            body=_encode(document),
            expected_stable_id=current.stable_id,
            expected_revision=record.revision + 1,
        )
        return document


def delete_canonical_task(*, session: Any, legacy_id: int) -> bool:
    with _task_mutation_lock(int(session.user_id)):
        catalog = read_canonical_task_catalog(session=session)
        record = _find_task(catalog, legacy_id)
        if record is None:
            return False
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "trash",
                "target": {"stableId": record.document.stable_id},
                "trashFolder": {"stableId": catalog.trash_folder_stable_id},
                "expectedRevision": record.revision,
            },
            body=None,
            expected_stable_id=record.document.stable_id,
            expected_revision=record.revision,
        )
        return True


def _find_task(catalog: CanonicalTaskCatalog, legacy_id: int) -> CanonicalTaskRecord | None:
    return next(
        (record for record in catalog.tasks if record.document.legacy_id == legacy_id),
        None,
    )


def _new_legacy_id(existing: set[int]) -> int:
    while True:
        candidate = secrets.randbelow((1 << 52) - 1) + 1
        if candidate not in existing:
            return candidate


def _encode(document: TaskDocument) -> bytes:
    return encode_task_document(
        stable_id=document.stable_id,
        legacy_id=document.legacy_id,
        text=document.text,
        done=document.done,
        priority=document.priority,
        due_date=document.due_date,
        completed_at=document.completed_at,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _execute(
    *,
    session: Any,
    catalog: CanonicalTaskCatalog,
    mutation: dict[str, object],
    body: bytes | None,
    expected_stable_id: str,
    expected_revision: int,
) -> None:
    result = logical.execute_mutation_v1(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
        selected=catalog.selection.snapshot,
        principal="user",
        mutation=mutation,
        body=body,
        invalidate=lambda _generation, _catalog_hash: invalidate_active_catalog_indexes(
            int(session.user_id)
        ),
    )
    changes = result.get("changes")
    if (
        not isinstance(changes, list)
        or len(changes) != 1
        or not isinstance(changes[0], dict)
        or changes[0].get("stableId") != expected_stable_id
        or changes[0].get("revision") != expected_revision
    ):
        raise TaskMutationError("Native CoreFS task mutation result is invalid.")
    publish_content_authority_after_mutation(
        session,
        generation=int(result["generation"]),
        catalog_hash=str(result["catalogHash"]),
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
