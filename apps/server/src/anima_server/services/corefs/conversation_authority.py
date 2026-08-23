"""Fail-closed CoreFS conversation authority gate and canonical readers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from anima_server.services.corefs import logical
from anima_server.services.corefs.content_authority import (
    CoreFsAuthorityUnavailable,
    authenticated_content_authority,
)
from anima_server.services.corefs.messages import (
    ConversationFormatError,
    EncodedMessageSegment,
    ReducedMessage,
    ThreadDocument,
    decode_message_segment,
    decode_thread_document,
    reduce_message_events_resilient,
)

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_MAX_CONVERSATION_ENTRIES = 100_000
_MAX_THREAD_BODY_BYTES = 1024 * 1024
_MAX_SEGMENT_BODY_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ConversationAuthoritySelection:
    generation: int
    catalog_hash: str

    @property
    def snapshot(self) -> logical.CoreFsValidationSnapshot:
        return logical.CoreFsValidationSnapshot(self.generation, self.catalog_hash)


class ConversationAuthorityError(PermissionError, CoreFsAuthorityUnavailable):
    """Raised when canonical conversation authority cannot serve a session."""


@dataclass(frozen=True, slots=True)
class CanonicalThreadView:
    document: ThreadDocument
    messages: tuple[ReducedMessage, ...]
    degraded_ranges: tuple[tuple[int, int], ...] = ()
    path: str | None = None
    revision: int | None = None
    segments: tuple[CanonicalSegmentRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class CanonicalSegmentRecord:
    segment: EncodedMessageSegment
    path: str
    revision: int


@dataclass(frozen=True, slots=True)
class CanonicalConversationCatalog:
    selection: ConversationAuthoritySelection
    conversation_root_path: str
    conversation_root_stable_id: str
    trash_root_stable_id: str
    threads: tuple[CanonicalThreadView, ...]


def conversation_authority_selection(
    session: object,
) -> ConversationAuthoritySelection | None:
    """Accept only a PCF-008-authenticated, family-scoped authority marker."""
    marker = getattr(session, "content_authority", None)
    if not isinstance(marker, dict):
        return None
    families = marker.get("families")
    generation = marker.get("generation")
    catalog_hash = marker.get("catalogHash")
    if (
        marker.get("version") != 1
        or marker.get("state") != "authoritative"
        or not isinstance(families, list)
        or "conversations" not in families
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or not isinstance(catalog_hash, str)
        or _SHA256_HEX.fullmatch(catalog_hash) is None
        or getattr(session, "corefs_session", None) is None
        or getattr(session, "corefs_keys", None) is None
    ):
        return None
    return ConversationAuthoritySelection(generation, catalog_hash)


def conversation_corefs_authority_active(session: object) -> bool:
    """Return whether canonical CoreFS is the required conversation authority.

    True either when this session can reach canonical conversations or when
    the Core manifest is authoritative — an FS-locked session on an activated
    Core must take the canonical branch (and fail closed there) instead of
    writing legacy state the canonical catalog never sees.
    """
    from anima_server.services.corefs.content_authority import core_content_authority_active

    return conversation_authority_selection(session) is not None or core_content_authority_active()


def active_conversation_authority_session(user_id: int) -> object | None:
    from anima_server.services.sessions import active_unlock_sessions

    return next(
        (
            session
            for session in reversed(active_unlock_sessions(user_id))
            if conversation_authority_selection(session) is not None
        ),
        None,
    )


def any_conversation_corefs_authority_active() -> bool:
    """Return true when canonical conversation authority governs this Core."""
    from anima_server.services.corefs.content_authority import core_content_authority_active
    from anima_server.services.sessions import all_active_unlock_sessions

    # Cheap in-memory scan first; only fall through to the manifest read when
    # no live session already proves canonical authority (matches the sibling
    # predicates and keeps this off the disk on a recurring background tick).
    return any(
        conversation_authority_selection(session) is not None
        for session in all_active_unlock_sessions()
    ) or core_content_authority_active()


def list_canonical_threads(*, session: Any) -> tuple[CanonicalThreadView, ...]:
    return read_canonical_conversation_catalog(session=session).threads


def get_active_canonical_thread(*, session: Any) -> CanonicalThreadView | None:
    return next(
        (
            view
            for view in list_canonical_threads(session=session)
            if view.document.status == "active"
        ),
        None,
    )


def read_canonical_conversation_catalog(*, session: Any) -> CanonicalConversationCatalog:
    selection = _require_selection(session)
    role = session.corefs_session.resolve_validation_role_v1(
        session.corefs_keys,
        "core.conversations",
    )
    if (
        not isinstance(role, dict)
        or role.get("generation") != selection.generation
        or role.get("catalogHash") != selection.catalog_hash
    ):
        raise ConversationFormatError("core.conversations role is unavailable")
    stable_id = role.get("stableId")
    if not isinstance(stable_id, str):
        raise ConversationFormatError("core.conversations role identity is invalid")
    root_entries = _walk_all(
        session=session,
        selected=selection.snapshot,
        root="",
        include_directories=True,
    )
    root = next(
        (
            entry.get("path")
            for entry in root_entries
            if entry.get("kind") == "directory" and entry.get("stableId") == stable_id
        ),
        None,
    )
    if not isinstance(root, str) or not root:
        raise ConversationFormatError("core.conversations role path is invalid")
    trash_ids = [
        entry.get("stableId")
        for entry in root_entries
        if entry.get("kind") == "directory" and entry.get("role") == "core.trash"
    ]
    if len(trash_ids) != 1 or not isinstance(trash_ids[0], str):
        raise ConversationFormatError("core.trash role is unavailable")
    entries = _walk_all(session=session, selected=selection.snapshot, root=root)
    threads = [
        entry
        for entry in entries
        if entry.get("kind") == "file" and entry.get("objectKind") == "thread"
    ]
    views = tuple(
        sorted(
            (
                _read_thread_view(session=session, selection=selection, entry=entry)
                for entry in threads
            ),
            key=lambda item: (
                item.document.last_message_at or item.document.updated_at,
                item.document.thread_id,
            ),
            reverse=True,
        )
    )
    return CanonicalConversationCatalog(
        selection=selection,
        conversation_root_path=root,
        conversation_root_stable_id=stable_id,
        trash_root_stable_id=trash_ids[0],
        threads=views,
    )


def get_canonical_thread(
    *,
    session: Any,
    thread_id: int | str,
) -> CanonicalThreadView | None:
    for view in list_canonical_threads(session=session):
        if view.document.thread_id == str(thread_id) or view.document.legacy_thread_id == thread_id:
            return view
        if str(view.document.legacy_thread_id) == str(thread_id):
            return view
    return None


def canonical_messages_for_display(view: CanonicalThreadView) -> list[dict[str, object]]:
    return [
        {
            "id": canonical_message_api_id(message),
            "role": message.role,
            "content": message.content,
            "ts": message.created_at,
            "isArchivedHistory": view.document.status != "active",
            "retrieval": None,
            "attachments": [{"corefsUri": uri} for uri in message.attachment_uris],
            "pills": [],
        }
        for message in view.messages
    ]


def canonical_message_api_id(message: ReducedMessage) -> int:
    value = message.legacy_message_id
    if isinstance(value, int) and value >= 0:
        return value
    return int.from_bytes(hashlib.sha256(message.message_id.encode()).digest()[:8], "big") & (
        (1 << 63) - 1
    )


def _read_thread_view(
    *,
    session: Any,
    selection: ConversationAuthoritySelection,
    entry: dict[str, object],
) -> CanonicalThreadView:
    path = entry.get("path")
    stable_id = entry.get("stableId")
    revision = entry.get("revision")
    if (
        not isinstance(path, str)
        or not isinstance(stable_id, str)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        raise ConversationFormatError("canonical thread path is invalid")
    document = decode_thread_document(
        _read_all(session=session, selected=selection.snapshot, path=path)
    )
    if document.thread_id != stable_id:
        raise ConversationFormatError("canonical thread identity is invalid")
    segment_entries = _entries_by_id(
        _walk_all(
            session=session,
            selected=selection.snapshot,
            root=path.rsplit("/", 1)[0],
        )
    )
    previous_id: str | None = None
    previous_hash: str | None = None
    events = []
    degraded: list[tuple[int, int]] = []
    segment_records: list[CanonicalSegmentRecord] = []
    for index, (segment_id, expected_hash) in enumerate(
        zip(document.segment_ids, document.segment_sha256, strict=True)
    ):
        segment_entry = segment_entries.get(segment_id)
        if segment_entry is None:
            degraded.append(_expected_gap(document, index))
            previous_id = segment_id
            previous_hash = expected_hash
            continue
        segment_path = segment_entry.get("path")
        segment_revision = segment_entry.get("revision")
        if (
            not isinstance(segment_path, str)
            or isinstance(segment_revision, bool)
            or not isinstance(segment_revision, int)
            or segment_revision < 1
        ):
            degraded.append(_expected_gap(document, index))
            previous_id = segment_id
            previous_hash = expected_hash
            continue
        try:
            segment = decode_message_segment(
                _read_all(
                    session=session,
                    selected=selection.snapshot,
                    path=segment_path,
                ),
                expected_previous_segment_id=previous_id,
                expected_previous_sha256=previous_hash,
            )
            if segment.segment_id != segment_id or segment.sha256 != expected_hash:
                raise ConversationFormatError("thread segment inventory hash is invalid")
        except ConversationFormatError:
            degraded.append(_expected_gap(document, index))
        else:
            events.extend(segment.events)
            segment_records.append(
                CanonicalSegmentRecord(
                    segment=segment,
                    path=segment_path,
                    revision=segment_revision,
                )
            )
        previous_id = segment_id
        previous_hash = expected_hash
    messages, event_degradation = reduce_message_events_resilient(events)
    return CanonicalThreadView(
        document=document,
        messages=messages,
        degraded_ranges=_merge_ranges([*degraded, *event_degradation]),
        path=path,
        revision=revision,
        segments=tuple(segment_records),
    )


def _walk_all(
    *,
    session: Any,
    selected: logical.CoreFsValidationSnapshot,
    root: str,
    include_directories: bool = False,
) -> list[dict[str, object]]:
    cursor: str | None = None
    entries: list[dict[str, object]] = []
    while True:
        payload = _wire(
            logical.walk_v1(
                corefs_session=session.corefs_session,
                keys=session.corefs_keys,
                selected=selected,
                root=root,
                cursor_after=cursor,
                page_size=100,
                include_directories=include_directories,
            ),
            selection=selected,
        )
        page = payload.get("entries")
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise ConversationFormatError("canonical conversation walk is invalid")
        entries.extend(page)
        if len(entries) > _MAX_CONVERSATION_ENTRIES:
            raise ConversationFormatError("canonical conversation inventory exceeds its bound")
        next_cursor = payload.get("nextCursor")
        if next_cursor is None:
            return entries
        if not isinstance(next_cursor, dict) or not isinstance(next_cursor.get("after"), str):
            raise ConversationFormatError("canonical conversation cursor is invalid")
        candidate = str(next_cursor["after"])
        if not candidate or candidate == cursor:
            raise ConversationFormatError("canonical conversation cursor did not advance")
        cursor = candidate


def _read_all(
    *,
    session: Any,
    selected: logical.CoreFsValidationSnapshot,
    path: str,
) -> bytes:
    limit = _MAX_THREAD_BODY_BYTES if path.endswith(".json") else _MAX_SEGMENT_BODY_BYTES
    chunks: list[bytes] = []
    offset = 0
    while offset <= limit:
        raw = logical.read_chunk_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=selected,
            path=path,
            offset=offset,
            max_bytes=min(64 * 1024, limit - offset + 1),
        )
        if raw is None:
            return b"".join(chunks)
        payload = _wire(raw, selection=selected)
        encoded = payload.get("bytesBase64")
        if not isinstance(encoded, str) or payload.get("offset") != offset:
            raise ConversationFormatError("canonical conversation read is invalid")
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ConversationFormatError(
                "canonical conversation body encoding is invalid"
            ) from exc
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        offset += len(chunk)
    raise ConversationFormatError("canonical conversation body exceeds its bound")


def _entries_by_id(entries: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for entry in entries:
        stable_id = entry.get("stableId", entry.get("objectId"))
        if isinstance(stable_id, str):
            result[stable_id] = entry
    return result


def _expected_gap(document: ThreadDocument, index: int) -> tuple[int, int]:
    return document.segment_ranges[index]


def _merge_ranges(ranges: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _wire(
    raw: bytes,
    *,
    selection: logical.CoreFsValidationSnapshot,
) -> dict[str, object]:
    try:
        payload = json.loads(raw)
        result = payload["result"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ConversationFormatError("CoreFS conversation response is invalid") from exc
    if (
        payload.get("version") != "corefs-logical-v1"
        or not isinstance(result, dict)
        or result.get("generation") not in {None, selection.generation}
    ):
        raise ConversationFormatError("CoreFS conversation response is invalid")
    return result


def _require_selection(session: object) -> ConversationAuthoritySelection:
    try:
        marker = authenticated_content_authority(session, family="conversations")
    except RuntimeError as exc:
        raise ConversationAuthorityError("CoreFS conversation authority could not be refreshed") from exc
    if marker is None:
        raise ConversationAuthorityError("CoreFS conversation authority is not active")
    selection = conversation_authority_selection(session)
    if selection is None:
        raise ConversationAuthorityError("CoreFS conversation authority is not active")
    return selection
