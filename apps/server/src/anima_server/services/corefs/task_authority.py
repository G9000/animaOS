"""CoreFS task authority gate and authenticated canonical readers."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any

from anima_server.services.corefs import logical
from anima_server.services.corefs.content_authority import authenticated_content_authority
from anima_server.services.corefs.diary_migration import migration_opaque_id
from anima_server.services.corefs.formats import TaskDocument, decode_task_document

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_MAX_TASKS = 10_000
_MAX_TASK_BODY_BYTES = 1024 * 1024


class TaskAuthorityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TaskAuthoritySelection:
    generation: int
    catalog_hash: str

    @property
    def snapshot(self) -> logical.CoreFsValidationSnapshot:
        return logical.CoreFsValidationSnapshot(self.generation, self.catalog_hash)


@dataclass(frozen=True, slots=True)
class CanonicalTaskRecord:
    document: TaskDocument
    path: str
    revision: int


@dataclass(frozen=True, slots=True)
class CanonicalTaskCatalog:
    selection: TaskAuthoritySelection
    tasks: tuple[CanonicalTaskRecord, ...]
    trash_folder_stable_id: str


def task_authority_selection(session: object) -> TaskAuthoritySelection | None:
    """Accept only the authenticated global authority marker owned by PCF-008."""
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
        or "tasks" not in families
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or not isinstance(catalog_hash, str)
        or _SHA256_HEX.fullmatch(catalog_hash) is None
        or getattr(session, "corefs_session", None) is None
        or getattr(session, "corefs_keys", None) is None
    ):
        return None
    return TaskAuthoritySelection(generation, catalog_hash)


def task_corefs_authority_active(session: object) -> bool:
    return task_authority_selection(session) is not None


def list_canonical_tasks(*, session: Any) -> tuple[TaskDocument, ...]:
    return tuple(record.document for record in read_canonical_task_catalog(session=session).tasks)


def read_canonical_task_catalog(*, session: Any) -> CanonicalTaskCatalog:
    try:
        marker = authenticated_content_authority(session, family="tasks")
    except RuntimeError as exc:
        raise TaskAuthorityError("CoreFS task authority could not be refreshed.") from exc
    selection = task_authority_selection(session) if marker is not None else None
    if selection is None:
        raise TaskAuthorityError("CoreFS task authority is not active.")
    entries = _walk_all(session=session, selection=selection, include_directories=True)
    trash_ids = [
        entry.get("stableId")
        for entry in entries
        if entry.get("kind") == "directory" and entry.get("role") == "core.trash"
    ]
    if len(trash_ids) != 1 or not isinstance(trash_ids[0], str):
        raise TaskAuthorityError("Canonical CoreFS trash authority is unavailable.")
    task_entries = [
        entry
        for entry in entries
        if entry.get("kind") == "file" and entry.get("objectKind") == "task"
    ]
    if len(task_entries) > _MAX_TASKS:
        raise TaskAuthorityError("Canonical task inventory exceeds its bound.")

    tasks: list[CanonicalTaskRecord] = []
    for entry in task_entries:
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
            raise TaskAuthorityError("Canonical task identity is invalid.")
        document = decode_task_document(_read_all(session=session, selection=selection, path=path))
        if document.stable_id != stable_id or document.stable_id != migration_opaque_id(
            "task", str(document.legacy_id)
        ):
            raise TaskAuthorityError("Canonical task body does not match its catalog identity.")
        tasks.append(CanonicalTaskRecord(document=document, path=path, revision=revision))

    tasks.sort(key=lambda task: task.document.stable_id)
    tasks.sort(key=lambda task: task.document.created_at or "", reverse=True)
    tasks.sort(key=lambda task: task.document.priority, reverse=True)
    tasks.sort(key=lambda task: task.document.done)
    return CanonicalTaskCatalog(
        selection=selection,
        tasks=tuple(tasks),
        trash_folder_stable_id=trash_ids[0],
    )


def _walk_all(
    *,
    session: Any,
    selection: TaskAuthoritySelection,
    include_directories: bool = False,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    cursor: str | None = None
    seen: set[str] = set()
    while True:
        result = _wire(
            logical.walk_v1(
                corefs_session=session.corefs_session,
                keys=session.corefs_keys,
                selected=selection.snapshot,
                root="",
                cursor_after=cursor,
                page_size=100,
                include_directories=include_directories,
            ),
            selection=selection,
        )
        page = result.get("entries")
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise TaskAuthorityError("Canonical task inventory is invalid.")
        entries.extend(page)
        if len(entries) > 50_000:
            raise TaskAuthorityError("Canonical CoreFS inventory exceeds its bound.")
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            return entries
        if not isinstance(next_cursor, dict) or not isinstance(next_cursor.get("after"), str):
            raise TaskAuthorityError("Canonical task cursor is invalid.")
        cursor = next_cursor["after"]
        if not cursor or cursor in seen:
            raise TaskAuthorityError("Canonical task cursor did not advance.")
        seen.add(cursor)


def _read_all(
    *,
    session: Any,
    selection: TaskAuthoritySelection,
    path: str,
) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while offset <= _MAX_TASK_BODY_BYTES:
        raw = logical.read_chunk_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=selection.snapshot,
            path=path,
            offset=offset,
            max_bytes=min(64 * 1024, _MAX_TASK_BODY_BYTES - offset + 1),
        )
        if raw is None:
            return b"".join(chunks)
        result = _wire(raw, selection=selection)
        encoded = result.get("bytesBase64")
        if not isinstance(encoded, str) or result.get("offset") != offset:
            raise TaskAuthorityError("Canonical task read is invalid.")
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TaskAuthorityError("Canonical task body encoding is invalid.") from exc
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        offset += len(chunk)
    raise TaskAuthorityError("Canonical task body exceeds its bound.")


def _wire(
    raw: bytes,
    *,
    selection: TaskAuthoritySelection,
) -> dict[str, object]:
    try:
        payload = json.loads(raw)
        result = payload["result"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise TaskAuthorityError("Canonical task response is invalid.") from exc
    if (
        payload.get("version") != "corefs-logical-v1"
        or not isinstance(result, dict)
        or result.get("generation") not in {None, selection.generation}
    ):
        raise TaskAuthorityError("Canonical task response changed generation.")
    return result
