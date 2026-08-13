"""Post-cutover thread lifecycle mutations committed only through CoreFS."""

from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from anima_server.services.corefs import logical
from anima_server.services.corefs.content_authority import (
    invalidate_active_catalog_indexes,
    publish_content_authority_after_mutation,
)
from anima_server.services.corefs.conversation_authority import (
    CanonicalConversationCatalog,
    CanonicalThreadView,
    read_canonical_conversation_catalog,
)
from anima_server.services.corefs.diary_migration import migration_opaque_id
from anima_server.services.corefs.messages import (
    THREAD_CONTENT_TYPE,
    ThreadDocument,
    encode_thread_document,
)

_MAX_CREATE_ATTEMPTS = 4
_locks_guard = RLock()
_locks: dict[int, RLock] = {}


class ConversationMutationError(RuntimeError):
    pass


def create_canonical_thread(*, session: Any, force_new: bool = False) -> CanonicalThreadView:
    user_id = int(session.user_id)
    with _conversation_lock(user_id):
        for _attempt in range(_MAX_CREATE_ATTEMPTS):
            catalog = read_canonical_conversation_catalog(session=session)
            active = next(
                (view for view in catalog.threads if view.document.status == "active"),
                None,
            )
            if active is not None and not force_new and not active.messages:
                return active
            legacy_id = _new_legacy_id(catalog)
            stable_id = migration_opaque_id("conversation-thread", str(legacy_id))
            now = _timestamp()
            document = ThreadDocument(
                thread_id=stable_id,
                legacy_thread_id=legacy_id,
                title=None,
                status="active",
                created_at=now,
                updated_at=now,
                last_message_at=None,
                closed_at=None,
                is_archived=False,
                segment_ids=(),
                segment_sha256=(),
                segment_ranges=(),
                message_count=0,
            )
            path = f"{catalog.conversation_root_path}/thread-{stable_id}.json"
            try:
                if active is None:
                    _execute(
                        session=session,
                        catalog=catalog,
                        mutation={
                            "operation": "create_file",
                            "path": path,
                            "stableId": stable_id,
                            "kind": "thread",
                            "contentType": THREAD_CONTENT_TYPE,
                            "bodyEncoding": "utf-8",
                        },
                        body=encode_thread_document(document),
                        expected_ids={stable_id},
                    )
                else:
                    closed = _closed_document(active.document, now=now)
                    _execute(
                        session=session,
                        catalog=catalog,
                        mutation={
                            "operation": "apply_patch",
                            "patch": _update_and_add_patch(
                                update_path=_require_path(active),
                                current=encode_thread_document(active.document),
                                updated=encode_thread_document(closed),
                                add_path=path,
                                added=encode_thread_document(document),
                            ),
                            "expectedRevisions": {
                                _require_path(active): _require_revision(active),
                            },
                            "addFormats": {
                                path: {
                                    "stableId": stable_id,
                                    "kind": "thread",
                                    "contentType": THREAD_CONTENT_TYPE,
                                }
                            },
                            "trashFolder": {"stableId": catalog.trash_root_stable_id},
                        },
                        body=None,
                        expected_ids={active.document.thread_id, stable_id},
                    )
            except ValueError as exc:
                if str(exc) in {
                    "corefs_mutation_collision",
                    "corefs_mutation_optimistic_conflict",
                }:
                    continue
                raise
            created = _find_thread(
                read_canonical_conversation_catalog(session=session),
                legacy_id,
            )
            if created is None:
                raise ConversationMutationError("Canonical thread creation did not verify.")
            return created
    raise ConversationMutationError("Canonical thread identity allocation did not converge.")


def close_canonical_thread(
    *, session: Any, thread_id: int | str
) -> tuple[CanonicalThreadView | None, bool]:
    with _conversation_lock(int(session.user_id)):
        catalog = read_canonical_conversation_catalog(session=session)
        current = _find_thread(catalog, thread_id)
        if current is None:
            return None, False
        if current.document.status == "closed":
            return current, False
        document = _closed_document(current.document, now=_timestamp())
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "write_file",
                "target": {"stableId": current.document.thread_id},
                "expectedRevision": _require_revision(current),
                "contentType": THREAD_CONTENT_TYPE,
                "bodyEncoding": "utf-8",
            },
            body=encode_thread_document(document),
            expected_ids={current.document.thread_id},
        )
        return (
            _find_thread(read_canonical_conversation_catalog(session=session), thread_id),
            True,
        )


def delete_canonical_thread(*, session: Any, thread_id: int | str) -> bool:
    with _conversation_lock(int(session.user_id)):
        catalog = read_canonical_conversation_catalog(session=session)
        current = _find_thread(catalog, thread_id)
        if current is None:
            return False
        if current.degraded_ranges or len(current.segments) != len(current.document.segment_ids):
            raise ConversationMutationError("A degraded canonical thread cannot be deleted.")
        paths = [record.path for record in current.segments]
        paths.append(_require_path(current))
        expected = {record.path: record.revision for record in current.segments}
        expected[_require_path(current)] = _require_revision(current)
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "apply_patch",
                "patch": _delete_patch(paths),
                "expectedRevisions": expected,
                "addFormats": {},
                "trashFolder": {"stableId": catalog.trash_root_stable_id},
            },
            body=None,
            expected_ids={
                current.document.thread_id,
                *(record.segment.segment_id for record in current.segments),
            },
        )
        return True


def _closed_document(document: ThreadDocument, *, now: str) -> ThreadDocument:
    return replace(
        document,
        status="closed",
        updated_at=now,
        closed_at=now,
    )


def _find_thread(
    catalog: CanonicalConversationCatalog,
    thread_id: int | str,
) -> CanonicalThreadView | None:
    return next(
        (
            view
            for view in catalog.threads
            if view.document.thread_id == str(thread_id)
            or view.document.legacy_thread_id == thread_id
            or str(view.document.legacy_thread_id) == str(thread_id)
        ),
        None,
    )


def _new_legacy_id(catalog: CanonicalConversationCatalog) -> int:
    existing = set()
    for view in catalog.threads:
        value = view.document.legacy_thread_id
        if isinstance(value, int) and not isinstance(value, bool):
            existing.add(value)
    while True:
        candidate = secrets.randbelow((1 << 52) - 1) + 1
        if candidate not in existing:
            return candidate


def _execute(
    *,
    session: Any,
    catalog: CanonicalConversationCatalog,
    mutation: dict[str, object],
    body: bytes | None,
    expected_ids: set[str],
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
    if not isinstance(changes, list):
        raise ConversationMutationError("Native CoreFS conversation mutation result is invalid.")
    actual_ids = {
        change.get("stableId")
        for change in changes
        if isinstance(change, dict) and isinstance(change.get("stableId"), str)
    }
    if actual_ids != expected_ids or len(changes) != len(expected_ids):
        raise ConversationMutationError("Native CoreFS conversation mutation result is invalid.")
    publish_content_authority_after_mutation(
        session,
        generation=int(result["generation"]),
        catalog_hash=str(result["catalogHash"]),
    )


def _require_path(view: CanonicalThreadView) -> str:
    if view.path is None:
        raise ConversationMutationError("Canonical thread path is unavailable.")
    return view.path


def _require_revision(view: CanonicalThreadView) -> int:
    if view.revision is None:
        raise ConversationMutationError("Canonical thread revision is unavailable.")
    return view.revision


def _update_and_add_patch(
    *,
    update_path: str,
    current: bytes,
    updated: bytes,
    add_path: str,
    added: bytes,
) -> str:
    return (
        "*** Begin Patch\n"
        f"*** Update File: {update_path}\n"
        "@@\n"
        f"-{current.decode('utf-8')}\n"
        f"+{updated.decode('utf-8')}\n"
        f"*** Add File: {add_path}\n"
        f"+{added.decode('utf-8')}\n"
        "*** End of File\n"
        "*** End Patch"
    )


def _delete_patch(paths: list[str]) -> str:
    return "\n".join(
        ["*** Begin Patch", *(f"*** Delete File: {path}" for path in paths), "*** End Patch"]
    )


def _conversation_lock(user_id: int) -> RLock:
    with _locks_guard:
        return _locks.setdefault(user_id, RLock())


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
