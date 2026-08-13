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
    CanonicalSegmentRecord,
    CanonicalThreadView,
    read_canonical_conversation_catalog,
)
from anima_server.services.corefs.diary_migration import migration_opaque_id
from anima_server.services.corefs.messages import (
    MESSAGE_SEGMENT_CONTENT_TYPE,
    THREAD_CONTENT_TYPE,
    ConversationAppendPlan,
    ConversationTailConflict,
    ConversationTailSnapshot,
    MessageEvent,
    ReducedMessage,
    ThreadDocument,
    append_message_event_with_tail_retry,
    encode_thread_document,
)

_MAX_CREATE_ATTEMPTS = 4
_locks_guard = RLock()
_locks: dict[int, RLock] = {}


class ConversationMutationError(RuntimeError):
    pass


def append_canonical_message(
    *,
    session: Any,
    thread_id: int | str,
    role: str,
    content: str,
    attachment_uris: tuple[str, ...] = (),
    legacy_message_id: int | str | None = None,
    created_at: str | None = None,
) -> ReducedMessage:
    user_id = int(session.user_id)
    timestamp = created_at or _timestamp()
    allocated_legacy_id = legacy_message_id
    with _conversation_lock(user_id):

        def load_tail() -> ConversationTailSnapshot:
            catalog = read_canonical_conversation_catalog(session=session)
            current = _find_thread(catalog, thread_id)
            if current is None:
                raise ConversationMutationError("Canonical thread was not found.")
            if current.document.status != "active":
                raise ConversationMutationError("Canonical thread is not active.")
            _require_complete_thread(current)
            return ConversationTailSnapshot(
                thread_id=current.document.thread_id,
                events=tuple(
                    event
                    for segment in current.segments
                    for event in segment.segment.events
                ),
                tail=current.segments[-1].segment if current.segments else None,
            )

        def event_factory(snapshot: ConversationTailSnapshot) -> MessageEvent:
            nonlocal allocated_legacy_id
            existing_ids = {
                event.legacy_message_id
                for event in snapshot.events
                if event.legacy_message_id is not None
            }
            if allocated_legacy_id is None:
                allocated_legacy_id = _new_message_legacy_id(existing_ids)
            elif allocated_legacy_id in existing_ids:
                raise ConversationMutationError("Canonical message identity already exists.")
            next_sequence = max((event.sequence for event in snapshot.events), default=0) + 1
            message_id = migration_opaque_id(
                "conversation-message",
                f"{snapshot.thread_id}:{allocated_legacy_id}",
            )
            return MessageEvent(
                event_id=migration_opaque_id(
                    "conversation-event",
                    f"{message_id}:created:1",
                ),
                message_id=message_id,
                legacy_message_id=allocated_legacy_id,
                thread_id=snapshot.thread_id,
                sequence=next_sequence,
                kind="message.created",
                message_version=1,
                expected_prior_event_id=None,
                expected_prior_version=None,
                role=role,
                content=content,
                attachment_uris=attachment_uris,
                created_at=timestamp,
            )

        def commit(plan: ConversationAppendPlan) -> None:
            catalog = read_canonical_conversation_catalog(session=session)
            current = _find_thread(catalog, thread_id)
            if current is None:
                raise ConversationTailConflict("canonical thread disappeared")
            _require_complete_thread(current)
            tail_record = current.segments[-1] if current.segments else None
            actual_tail_id = tail_record.segment.segment_id if tail_record is not None else None
            actual_tail_hash = tail_record.segment.sha256 if tail_record is not None else None
            if (
                actual_tail_id != plan.expected_tail_id
                or actual_tail_hash != plan.expected_tail_sha256
            ):
                raise ConversationTailConflict("canonical thread tail changed")
            _commit_message_append(
                session=session,
                catalog=catalog,
                current=current,
                tail_record=tail_record,
                plan=plan,
            )

        plan = append_message_event_with_tail_retry(
            load_tail=load_tail,
            event_factory=event_factory,
            commit=commit,
            max_attempts=_MAX_CREATE_ATTEMPTS,
        )
        refreshed = _find_thread(
            read_canonical_conversation_catalog(session=session),
            thread_id,
        )
        if refreshed is None:
            raise ConversationMutationError("Canonical message append did not verify.")
        created = next(
            (message for message in refreshed.messages if message.message_id == plan.event.message_id),
            None,
        )
        if created is None:
            raise ConversationMutationError("Canonical message append did not verify.")
        return created


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


def reactivate_canonical_thread(
    *, session: Any, thread_id: int | str
) -> CanonicalThreadView | None:
    with _conversation_lock(int(session.user_id)):
        catalog = read_canonical_conversation_catalog(session=session)
        target = _find_thread(catalog, thread_id)
        if target is None:
            return None
        if target.document.status == "active":
            return target
        _require_complete_thread(target)
        now = _timestamp()
        active = next(
            (
                view
                for view in catalog.threads
                if view.document.status == "active"
                and view.document.thread_id != target.document.thread_id
            ),
            None,
        )
        activated = replace(
            target.document,
            status="active",
            updated_at=now,
            closed_at=None,
            is_archived=False,
        )
        updates = [
            (
                _require_path(target),
                encode_thread_document(target.document),
                encode_thread_document(activated),
            )
        ]
        expected = {_require_path(target): _require_revision(target)}
        expected_ids = {target.document.thread_id}
        if active is not None:
            closed = _closed_document(active.document, now=now)
            updates.append(
                (
                    _require_path(active),
                    encode_thread_document(active.document),
                    encode_thread_document(closed),
                )
            )
            expected[_require_path(active)] = _require_revision(active)
            expected_ids.add(active.document.thread_id)
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "apply_patch",
                "patch": _build_patch(updates=updates, additions=[]),
                "expectedRevisions": expected,
                "addFormats": {},
                "trashFolder": {"stableId": catalog.trash_root_stable_id},
            },
            body=None,
            expected_ids=expected_ids,
        )
        return _find_thread(
            read_canonical_conversation_catalog(session=session),
            thread_id,
        )


def delete_canonical_thread(*, session: Any, thread_id: int | str) -> bool:
    with _conversation_lock(int(session.user_id)):
        catalog = read_canonical_conversation_catalog(session=session)
        current = _find_thread(catalog, thread_id)
        if current is None:
            return False
        _require_complete_thread(current)
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


def _commit_message_append(
    *,
    session: Any,
    catalog: CanonicalConversationCatalog,
    current: CanonicalThreadView,
    tail_record: CanonicalSegmentRecord | None,
    plan: ConversationAppendPlan,
) -> None:
    if plan.replaces_tail:
        if tail_record is None or tail_record.segment.segment_id != plan.segment.segment_id:
            raise ConversationTailConflict("canonical replacement tail changed")
        segment_ids = current.document.segment_ids
        segment_hashes = (*current.document.segment_sha256[:-1], plan.segment.sha256)
        segment_ranges = (
            *current.document.segment_ranges[:-1],
            _segment_range(plan.segment.events),
        )
        segment_path = tail_record.path
        segment_revision = tail_record.revision
        add_format = None
        expected_ids = {current.document.thread_id, plan.segment.segment_id}
    else:
        segment_ids = (*current.document.segment_ids, plan.segment.segment_id)
        segment_hashes = (*current.document.segment_sha256, plan.segment.sha256)
        segment_ranges = (*current.document.segment_ranges, _segment_range(plan.segment.events))
        segment_path = (
            f"{catalog.conversation_root_path}/segment-"
            f"{current.document.thread_id}-{plan.segment.index:08d}.jsonl"
        )
        segment_revision = None
        add_format = {
            "stableId": plan.segment.segment_id,
            "kind": "message-segment",
            "contentType": MESSAGE_SEGMENT_CONTENT_TYPE,
        }
        expected_ids = {current.document.thread_id, plan.segment.segment_id}
    document = replace(
        current.document,
        updated_at=plan.event.created_at,
        last_message_at=plan.event.created_at,
        segment_ids=segment_ids,
        segment_sha256=segment_hashes,
        segment_ranges=segment_ranges,
        message_count=current.document.message_count + 1,
    )
    updates = [
        (
            _require_path(current),
            encode_thread_document(current.document),
            encode_thread_document(document),
        )
    ]
    expected_revisions = {_require_path(current): _require_revision(current)}
    additions: list[tuple[str, bytes]] = []
    add_formats: dict[str, dict[str, str]] = {}
    if plan.replaces_tail:
        assert tail_record is not None
        updates.append((segment_path, tail_record.segment.data, plan.segment.data))
        expected_revisions[segment_path] = segment_revision
    else:
        additions.append((segment_path, plan.segment.data))
        assert add_format is not None
        add_formats[segment_path] = add_format
    try:
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "apply_patch",
                "patch": _build_patch(updates=updates, additions=additions),
                "expectedRevisions": expected_revisions,
                "addFormats": add_formats,
                "trashFolder": {"stableId": catalog.trash_root_stable_id},
            },
            body=None,
            expected_ids=expected_ids,
        )
    except ValueError as exc:
        if str(exc) in {
            "corefs_mutation_collision",
            "corefs_mutation_optimistic_conflict",
        }:
            raise ConversationTailConflict("canonical thread tail changed") from exc
        raise


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


def _new_message_legacy_id(existing: set[int | str]) -> int:
    while True:
        candidate = secrets.randbelow((1 << 52) - 1) + 1
        if candidate not in existing:
            return candidate


def _require_complete_thread(view: CanonicalThreadView) -> None:
    if (
        view.document.status == "degraded"
        or view.degraded_ranges
        or len(view.segments) != len(view.document.segment_ids)
    ):
        raise ConversationMutationError("A degraded canonical thread cannot be mutated.")


def _segment_range(events: tuple[MessageEvent, ...]) -> tuple[int, int]:
    return (
        min(event.sequence for event in events),
        max(event.sequence for event in events),
    )


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
    return _build_patch(
        updates=[(update_path, current, updated)],
        additions=[(add_path, added)],
    )


def _build_patch(
    *,
    updates: list[tuple[str, bytes, bytes]],
    additions: list[tuple[str, bytes]],
) -> str:
    lines = ["*** Begin Patch"]
    for path, current, updated in updates:
        lines.extend((f"*** Update File: {path}", "@@"))
        lines.extend(f"-{line}" for line in current.decode("utf-8").splitlines())
        lines.extend(f"+{line}" for line in updated.decode("utf-8").splitlines())
    for path, added in additions:
        lines.append(f"*** Add File: {path}")
        lines.extend(f"+{line}" for line in added.decode("utf-8").splitlines())
        if not added.endswith((b"\n", b"\r")):
            lines.append("*** End of File")
    lines.append("*** End Patch")
    return "\n".join(lines)


def _delete_patch(paths: list[str]) -> str:
    return "\n".join(
        ["*** Begin Patch", *(f"*** Delete File: {path}" for path in paths), "*** End Patch"]
    )


def _conversation_lock(user_id: int) -> RLock:
    with _locks_guard:
        return _locks.setdefault(user_id, RLock())


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
