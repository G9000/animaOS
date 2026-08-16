"""Authenticated post-cutover authority for journal, draft, and note content."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from anima_server.services.corefs import logical
from anima_server.services.corefs.content_authority import authenticated_content_authority
from anima_server.services.corefs.diary_migration import (
    PreparedWritingFolder,
    PreparedWritingObject,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.formats import (
    DIARY_CONTENT_TYPE,
    DRAFT_CONTENT_TYPE,
    NOTE_CONTENT_TYPE,
    DiaryDocument,
    DraftDocument,
    NoteDocument,
    decode_diary_document,
    decode_draft_document,
    decode_note_document,
)

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_NEW_FOLDER_PREFIX = re.compile(r"^(\d{1,16})--")
_MAX_WRITING_OBJECTS = 50_000


class WritingAuthorityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WritingAuthoritySelection:
    generation: int
    catalog_hash: str

    @property
    def snapshot(self) -> logical.CoreFsValidationSnapshot:
        return logical.CoreFsValidationSnapshot(self.generation, self.catalog_hash)


@dataclass(frozen=True, slots=True)
class CanonicalDiaryFolder:
    stable_id: str
    legacy_id: int
    path: str
    name: str
    created_at: str | None


@dataclass(frozen=True, slots=True)
class CanonicalDiaryEntry:
    document: DiaryDocument
    path: str
    revision: int


@dataclass(frozen=True, slots=True)
class CanonicalDraft:
    document: DraftDocument
    path: str
    revision: int
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CanonicalNote:
    document: NoteDocument
    path: str
    revision: int


@dataclass(frozen=True, slots=True)
class CanonicalWritingCatalog:
    selection: WritingAuthoritySelection
    journal_folder: PreparedWritingFolder
    notes_folder: PreparedWritingFolder
    trash_folder: PreparedWritingFolder
    folders: tuple[CanonicalDiaryFolder, ...]
    entries: tuple[CanonicalDiaryEntry, ...]
    drafts: tuple[CanonicalDraft, ...]
    notes: tuple[CanonicalNote, ...]
    objects_by_stable_id: dict[str, PreparedWritingObject]


def writing_authority_selection(session: object) -> WritingAuthoritySelection | None:
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
        or not {"diary", "notes"}.issubset(families)
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or not isinstance(catalog_hash, str)
        or _SHA256_HEX.fullmatch(catalog_hash) is None
        or getattr(session, "corefs_session", None) is None
        or getattr(session, "corefs_keys", None) is None
    ):
        return None
    return WritingAuthoritySelection(generation, catalog_hash)


def writing_corefs_authority_active(session: object) -> bool:
    return writing_authority_selection(session) is not None


def active_writing_authority_session(user_id: int) -> object | None:
    from anima_server.services.sessions import active_unlock_sessions

    return next(
        (
            session
            for session in reversed(active_unlock_sessions(user_id))
            if writing_corefs_authority_active(session)
        ),
        None,
    )


def read_canonical_writing_catalog(*, session: Any) -> CanonicalWritingCatalog:
    try:
        marker = authenticated_content_authority(session, family="diary")
    except RuntimeError as exc:
        raise WritingAuthorityError("CoreFS writing authority could not be refreshed.") from exc
    selection = writing_authority_selection(session) if marker is not None else None
    if selection is None:
        raise WritingAuthorityError("CoreFS writing authority is not active.")
    snapshot = read_prepared_writing_snapshot(
        session=session,
        selected=selection.snapshot,
    )
    if len(snapshot.objects) > _MAX_WRITING_OBJECTS:
        raise WritingAuthorityError("Canonical writing inventory exceeds its bound.")
    journal = _one_role(snapshot.folders, "core.journal")
    notes_folder = _one_role(snapshot.folders, "core.notes")
    trash = _one_role(snapshot.folders, "core.trash")
    objects_by_stable_id = {item.stable_id: item for item in snapshot.objects}
    if len(objects_by_stable_id) != len(snapshot.objects):
        raise WritingAuthorityError("Canonical writing identities are not unique.")

    folder_records = tuple(
        _folder_record(folder)
        for folder in snapshot.folders
        if folder.role is None
        and folder.path
        and _under(folder.path, journal.path)
        and not _under(folder.path, trash.path)
    )
    folder_ids = {folder.stable_id for folder in folder_records}
    if len(folder_ids) != len(folder_records):
        raise WritingAuthorityError("Canonical diary folder identities are not unique.")

    entries: list[CanonicalDiaryEntry] = []
    drafts: list[CanonicalDraft] = []
    notes: list[CanonicalNote] = []
    for item in snapshot.objects:
        if _under(item.path, trash.path):
            continue
        if item.kind == "diary" and _under(item.path, journal.path):
            if item.content_type != DIARY_CONTENT_TYPE:
                raise WritingAuthorityError("Canonical diary content type is invalid.")
            document = decode_diary_document(
                read_prepared_writing_body(
                    session=session,
                    item=item,
                    selected=selection.snapshot,
                )
            )
            if document.stable_id != item.stable_id:
                raise WritingAuthorityError("Canonical diary body changed identity.")
            entries.append(
                CanonicalDiaryEntry(document=document, path=item.path, revision=item.revision)
            )
        elif item.kind == "draft" and _under(item.path, journal.path):
            if item.content_type != DRAFT_CONTENT_TYPE:
                raise WritingAuthorityError("Canonical draft content type is invalid.")
            document = decode_draft_document(
                read_prepared_writing_body(
                    session=session,
                    item=item,
                    selected=selection.snapshot,
                )
            )
            if document.stable_id != item.stable_id:
                raise WritingAuthorityError("Canonical draft body changed identity.")
            drafts.append(
                CanonicalDraft(
                    document=document,
                    path=item.path,
                    revision=item.revision,
                    metadata=dict(item.metadata),
                )
            )
        elif item.kind == "note" and _under(item.path, notes_folder.path):
            if item.content_type != NOTE_CONTENT_TYPE:
                raise WritingAuthorityError("Canonical note content type is invalid.")
            document = decode_note_document(
                read_prepared_writing_body(
                    session=session,
                    item=item,
                    selected=selection.snapshot,
                )
            )
            if document.stable_id != item.stable_id:
                raise WritingAuthorityError("Canonical note body changed identity.")
            notes.append(CanonicalNote(document=document, path=item.path, revision=item.revision))

    entries.sort(
        key=lambda item: (
            item.document.entry_date,
            item.document.created_at or "",
            item.document.stable_id,
        ),
        reverse=True,
    )
    folder_records = tuple(
        sorted(folder_records, key=lambda item: (item.created_at or "", item.name))
    )
    return CanonicalWritingCatalog(
        selection=selection,
        journal_folder=journal,
        notes_folder=notes_folder,
        trash_folder=trash,
        folders=folder_records,
        entries=tuple(entries),
        drafts=tuple(drafts),
        notes=tuple(notes),
        objects_by_stable_id=objects_by_stable_id,
    )


def find_canonical_entry(
    catalog: CanonicalWritingCatalog,
    entry_id: int | str,
) -> CanonicalDiaryEntry | None:
    return next(
        (
            record
            for record in catalog.entries
            if record.document.stable_id == str(entry_id)
            or record.document.legacy_id == entry_id
            or str(record.document.legacy_id) == str(entry_id)
        ),
        None,
    )


def find_canonical_folder(
    catalog: CanonicalWritingCatalog,
    folder_id: int | str,
) -> CanonicalDiaryFolder | None:
    return next(
        (
            folder
            for folder in catalog.folders
            if folder.stable_id == str(folder_id)
            or folder.legacy_id == folder_id
            or str(folder.legacy_id) == str(folder_id)
        ),
        None,
    )


def diary_api_id(document: DiaryDocument) -> int:
    if isinstance(document.legacy_id, int) and document.legacy_id >= 0:
        return document.legacy_id
    return _stable_api_id(document.stable_id)


def folder_api_id(folder: PreparedWritingFolder) -> int:
    raw = folder.metadata.get("legacyId")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    match = _NEW_FOLDER_PREFIX.match(folder.path.rsplit("/", 1)[-1])
    if match is not None:
        return int(match.group(1))
    return _stable_api_id(folder.stable_id)


def _folder_record(folder: PreparedWritingFolder) -> CanonicalDiaryFolder:
    name = folder.metadata.get("displayName", folder.metadata.get("originalName"))
    if not isinstance(name, str) or not name:
        component = folder.path.rsplit("/", 1)[-1]
        name = _NEW_FOLDER_PREFIX.sub("", component, count=1)
    created_at = folder.metadata.get("createdAt")
    return CanonicalDiaryFolder(
        stable_id=folder.stable_id,
        legacy_id=folder_api_id(folder),
        path=folder.path,
        name=name,
        created_at=created_at if isinstance(created_at, str) else None,
    )


def _one_role(
    folders: tuple[PreparedWritingFolder, ...],
    role: str,
) -> PreparedWritingFolder:
    matches = [folder for folder in folders if folder.role == role]
    if len(matches) != 1:
        raise WritingAuthorityError(f"Canonical {role} role is unavailable.")
    return matches[0]


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


def _stable_api_id(stable_id: str) -> int:
    return int.from_bytes(hashlib.sha256(stable_id.encode()).digest()[:8], "big") & ((1 << 52) - 1)
