"""Post-cutover diary and folder writes committed only through CoreFS."""

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
from anima_server.services.corefs.diary_migration import (
    migration_opaque_id,
    portable_catalog_component,
)
from anima_server.services.corefs.formats import (
    DIARY_CONTENT_TYPE,
    DRAFT_CONTENT_TYPE,
    DiaryDocument,
    encode_diary_document,
    encode_draft_document,
)
from anima_server.services.corefs.writing_authority import (
    CanonicalDiaryEntry,
    CanonicalDiaryFolder,
    CanonicalDraft,
    CanonicalWritingCatalog,
    find_canonical_entry,
    find_canonical_folder,
    read_canonical_writing_catalog,
)

_MAX_ATTEMPTS = 4
_locks_guard = RLock()
_locks: dict[int, RLock] = {}


class WritingMutationError(RuntimeError):
    pass


def create_canonical_diary_entry(
    *,
    session: Any,
    entry_date: str,
    title: str | None,
    body: str,
    mood: str | None,
    folder_id: int | None,
) -> CanonicalDiaryEntry:
    with _writing_lock(int(session.user_id)):
        for _attempt in range(_MAX_ATTEMPTS):
            catalog = read_canonical_writing_catalog(session=session)
            folder = _require_folder(catalog, folder_id)
            existing_ids = {
                record.document.legacy_id
                for record in catalog.entries
                if record.document.legacy_id is not None
            }
            legacy_id = _new_legacy_id(existing_ids)
            stable_id = migration_opaque_id("diary-entry", str(legacy_id))
            now = _timestamp()
            document = DiaryDocument(
                format_version=1,
                stable_id=stable_id,
                entry_date=entry_date,
                title=title,
                mood=mood,
                folder_id=folder.stable_id if folder is not None else None,
                html=body,
                cover_uri=None,
                attachment_uris=(),
                inline_media_uris=(),
                legacy_id=legacy_id,
                legacy_folder_id=folder.legacy_id if folder is not None else None,
                source="user",
                created_at=now,
                updated_at=now,
                attachment_metadata=(),
            )
            parent_path = folder.path if folder is not None else catalog.journal_folder.path
            path = f"{parent_path}/{entry_date}-{legacy_id}.diary.json"
            try:
                _execute(
                    session=session,
                    catalog=catalog,
                    mutation={
                        "operation": "create_file",
                        "path": path,
                        "stableId": stable_id,
                        "kind": "diary",
                        "contentType": DIARY_CONTENT_TYPE,
                        "bodyEncoding": "utf-8",
                    },
                    body=_encode_diary(document),
                )
            except ValueError as exc:
                if str(exc) in {
                    "corefs_mutation_collision",
                    "corefs_mutation_optimistic_conflict",
                }:
                    continue
                raise
            refreshed = find_canonical_entry(
                read_canonical_writing_catalog(session=session),
                legacy_id,
            )
            if refreshed is None:
                raise WritingMutationError("Canonical diary creation did not verify.")
            return refreshed
    raise WritingMutationError("Canonical diary identity allocation did not converge.")


def upsert_canonical_draft(
    *,
    session: Any,
    draft_id: str,
    client_revision: int,
    content_sha256: str,
    target_entry_id: int | None,
    body: str,
    metadata: dict[str, Any],
    updated_at: str,
) -> CanonicalDraft:
    with _writing_lock(int(session.user_id)):
        catalog = read_canonical_writing_catalog(session=session)
        stable_id = migration_opaque_id("diary-draft", draft_id)
        existing = next(
            (draft for draft in catalog.drafts if draft.document.stable_id == stable_id),
            None,
        )
        token = {
            "draftId": draft_id,
            "clientRevision": client_revision,
            "contentSha256": content_sha256,
        }
        if existing is not None:
            prior = existing.document.metadata.get("handoffToken")
            if isinstance(prior, dict):
                prior_revision = prior.get("clientRevision")
                prior_hash = prior.get("contentSha256")
                if prior_revision == client_revision and prior_hash == content_sha256:
                    return existing
                if (
                    isinstance(prior_revision, int)
                    and not isinstance(prior_revision, bool)
                    and prior_revision >= client_revision
                ):
                    raise WritingMutationError("Draft handoff revision is stale.")
        target = (
            find_canonical_entry(catalog, target_entry_id) if target_entry_id is not None else None
        )
        if target_entry_id is not None and target is None:
            raise WritingMutationError("Draft target entry is unavailable.")
        document_metadata = dict(metadata)
        document_metadata.setdefault("updatedAt", updated_at)
        document_metadata["handoffToken"] = token
        encoded = encode_draft_document(
            stable_id=stable_id,
            target_id=target.document.stable_id if target is not None else None,
            content_type="text/html",
            body=body,
            metadata=document_metadata,
        )
        if existing is None:
            mutation: dict[str, object] = {
                "operation": "create_file",
                "path": f"{catalog.journal_folder.path}/{stable_id}.draft.json",
                "stableId": stable_id,
                "kind": "draft",
                "contentType": DRAFT_CONTENT_TYPE,
                "bodyEncoding": "utf-8",
            }
        else:
            mutation = {
                "operation": "write_file",
                "target": {"stableId": stable_id},
                "expectedRevision": existing.revision,
                "contentType": DRAFT_CONTENT_TYPE,
                "bodyEncoding": "utf-8",
            }
        _execute(
            session=session,
            catalog=catalog,
            mutation=mutation,
            body=encoded,
        )
        verified = next(
            (
                draft
                for draft in read_canonical_writing_catalog(session=session).drafts
                if draft.document.stable_id == stable_id
            ),
            None,
        )
        if (
            verified is None
            or verified.document.body != body
            or verified.document.metadata.get("handoffToken") != token
        ):
            raise WritingMutationError("Canonical draft handoff did not verify.")
        return verified


def update_canonical_diary_entry(
    *,
    session: Any,
    entry_id: int,
    entry_date: str | None,
    title: str | None,
    body: str | None,
    mood: str | None,
    cover_attachment_id: int | None,
    folder_id: int | None,
    clear_title: bool,
    clear_mood: bool,
    clear_cover: bool,
    clear_folder: bool,
) -> CanonicalDiaryEntry | None:
    with _writing_lock(int(session.user_id)):
        catalog = read_canonical_writing_catalog(session=session)
        record = find_canonical_entry(catalog, entry_id)
        if record is None:
            return None
        current = record.document
        folder = (
            None
            if clear_folder
            else _require_folder(catalog, folder_id)
            if folder_id is not None
            else find_canonical_folder(catalog, current.folder_id)
            if current.folder_id is not None
            else None
        )
        cover_uri = current.cover_uri
        if clear_cover:
            cover_uri = None
        elif cover_attachment_id is not None:
            attachment = _attachment_metadata(current, cover_attachment_id)
            if attachment.get("kind") != "image":
                raise WritingMutationError("Cover attachment must be an image.")
            stable_id = attachment.get("stableId")
            if not isinstance(stable_id, str):
                raise WritingMutationError("Cover attachment identity is invalid.")
            cover_uri = f"corefs://object/{stable_id}"
        next_document = replace(
            current,
            entry_date=entry_date or current.entry_date,
            title=None if clear_title else title if title is not None else current.title,
            mood=None if clear_mood else mood if mood is not None else current.mood,
            folder_id=folder.stable_id if folder is not None else None,
            legacy_folder_id=folder.legacy_id if folder is not None else None,
            html=body if body is not None else current.html,
            cover_uri=cover_uri,
            updated_at=_timestamp(),
        )
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "write_file",
                "target": {"stableId": current.stable_id},
                "expectedRevision": record.revision,
                "contentType": DIARY_CONTENT_TYPE,
                "bodyEncoding": "utf-8",
            },
            body=_encode_diary(next_document),
        )
        destination_parent = folder.path if folder is not None else catalog.journal_folder.path
        current_parent = record.path.rsplit("/", 1)[0]
        if destination_parent != current_parent:
            refreshed_catalog = read_canonical_writing_catalog(session=session)
            refreshed = find_canonical_entry(refreshed_catalog, entry_id)
            if refreshed is None:
                raise WritingMutationError("Canonical diary move source disappeared.")
            _execute(
                session=session,
                catalog=refreshed_catalog,
                mutation={
                    "operation": "move",
                    "source": {"stableId": current.stable_id},
                    "destination": f"{destination_parent}/{record.path.rsplit('/', 1)[-1]}",
                    "expectedRevision": refreshed.revision,
                },
                body=None,
            )
        verified = find_canonical_entry(
            read_canonical_writing_catalog(session=session),
            entry_id,
        )
        if verified is None:
            raise WritingMutationError("Canonical diary update did not verify.")
        return verified


def attach_canonical_diary_asset(
    *,
    session: Any,
    entry_id: int,
    attachment_id: int,
    stable_id: str,
    kind: str,
    mime_type: str,
    filename: str | None,
    caption: str | None,
    size_bytes: int,
    sha256: str,
    created_at: str,
) -> CanonicalDiaryEntry | None:
    with _writing_lock(int(session.user_id)):
        catalog = read_canonical_writing_catalog(session=session)
        record = find_canonical_entry(catalog, entry_id)
        if record is None:
            return None
        item = catalog.objects_by_stable_id.get(stable_id)
        if (
            item is None
            or item.kind != "attachment"
            or item.body_length != size_bytes
            or item.content_hash != sha256
            or item.content_type != mime_type
        ):
            raise WritingMutationError("Canonical diary attachment body is unavailable.")
        existing_ids = {value.get("legacyId") for value in record.document.attachment_metadata}
        if attachment_id in existing_ids:
            raise WritingMutationError("Canonical diary attachment identity already exists.")
        metadata = {
            "legacyId": attachment_id,
            "stableId": stable_id,
            "kind": kind,
            "mimeType": mime_type,
            "filename": filename,
            "caption": caption,
            "sha256": sha256,
            "createdAt": created_at,
        }
        document = replace(
            record.document,
            attachment_uris=(
                *record.document.attachment_uris,
                f"corefs://object/{stable_id}",
            ),
            attachment_metadata=(*record.document.attachment_metadata, metadata),
            updated_at=created_at,
        )
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "write_file",
                "target": {"stableId": record.document.stable_id},
                "expectedRevision": record.revision,
                "contentType": DIARY_CONTENT_TYPE,
                "bodyEncoding": "utf-8",
            },
            body=_encode_diary(document),
        )
        verified = find_canonical_entry(
            read_canonical_writing_catalog(session=session),
            entry_id,
        )
        if verified is None or not any(
            value.get("stableId") == stable_id for value in verified.document.attachment_metadata
        ):
            raise WritingMutationError("Canonical diary attachment link did not verify.")
        return verified


def delete_canonical_diary_entry(*, session: Any, entry_id: int) -> bool:
    with _writing_lock(int(session.user_id)):
        catalog = read_canonical_writing_catalog(session=session)
        record = find_canonical_entry(catalog, entry_id)
        if record is None:
            return False
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "trash",
                "target": {"stableId": record.document.stable_id},
                "trashFolder": {"stableId": catalog.trash_folder.stable_id},
                "expectedRevision": record.revision,
            },
            body=None,
        )
        return (
            find_canonical_entry(
                read_canonical_writing_catalog(session=session),
                entry_id,
            )
            is None
        )


def create_canonical_diary_folder(*, session: Any, name: str) -> CanonicalDiaryFolder:
    with _writing_lock(int(session.user_id)):
        for _attempt in range(_MAX_ATTEMPTS):
            catalog = read_canonical_writing_catalog(session=session)
            legacy_id = _new_legacy_id({folder.legacy_id for folder in catalog.folders})
            seed = migration_opaque_id("diary-folder", str(legacy_id))
            component = portable_catalog_component(f"{legacy_id}--{name}", stable_id=seed)
            try:
                _execute(
                    session=session,
                    catalog=catalog,
                    mutation={
                        "operation": "mkdir",
                        "path": f"{catalog.journal_folder.path}/{component}",
                    },
                    body=None,
                )
            except ValueError as exc:
                if str(exc) in {
                    "corefs_mutation_collision",
                    "corefs_mutation_optimistic_conflict",
                }:
                    continue
                raise
            refreshed = find_canonical_folder(
                read_canonical_writing_catalog(session=session),
                legacy_id,
            )
            if refreshed is None:
                raise WritingMutationError("Canonical diary folder creation did not verify.")
            return refreshed
    raise WritingMutationError("Canonical diary folder identity allocation did not converge.")


def rename_canonical_diary_folder(
    *,
    session: Any,
    folder_id: int,
    name: str,
) -> CanonicalDiaryFolder | None:
    with _writing_lock(int(session.user_id)):
        catalog = read_canonical_writing_catalog(session=session)
        folder = find_canonical_folder(catalog, folder_id)
        if folder is None:
            return None
        component = portable_catalog_component(
            f"{folder.legacy_id}--{name}",
            stable_id=folder.stable_id,
        )
        parent = folder.path.rsplit("/", 1)[0]
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "move",
                "source": {"stableId": folder.stable_id},
                "destination": f"{parent}/{component}",
                "expectedRevision": None,
            },
            body=None,
        )
        renamed = find_canonical_folder(
            read_canonical_writing_catalog(session=session),
            folder_id,
        )
        if renamed is None:
            raise WritingMutationError("Canonical diary folder rename did not verify.")
        return replace(renamed, name=name)


def delete_canonical_diary_folder(*, session: Any, folder_id: int) -> bool:
    with _writing_lock(int(session.user_id)):
        catalog = read_canonical_writing_catalog(session=session)
        folder = find_canonical_folder(catalog, folder_id)
        if folder is None:
            return False
        for record in tuple(
            entry for entry in catalog.entries if entry.document.folder_id == folder.stable_id
        ):
            updated = update_canonical_diary_entry(
                session=session,
                entry_id=_entry_legacy_id(record),
                entry_date=None,
                title=None,
                body=None,
                mood=None,
                cover_attachment_id=None,
                folder_id=None,
                clear_title=False,
                clear_mood=False,
                clear_cover=False,
                clear_folder=True,
            )
            if updated is None:
                raise WritingMutationError("Canonical diary folder cleanup lost an entry.")
        catalog = read_canonical_writing_catalog(session=session)
        folder = find_canonical_folder(catalog, folder_id)
        if folder is None:
            return True
        remaining = [
            item
            for item in catalog.objects_by_stable_id.values()
            if item.path.startswith(f"{folder.path}/")
        ]
        for item in remaining:
            current_catalog = read_canonical_writing_catalog(session=session)
            current = current_catalog.objects_by_stable_id.get(item.stable_id)
            if current is None or not current.path.startswith(f"{folder.path}/"):
                continue
            _execute(
                session=session,
                catalog=current_catalog,
                mutation={
                    "operation": "move",
                    "source": {"stableId": current.stable_id},
                    "destination": (f"{current_catalog.journal_folder.path}/{current.stable_id}"),
                    "expectedRevision": current.revision,
                },
                body=None,
            )
        catalog = read_canonical_writing_catalog(session=session)
        folder = find_canonical_folder(catalog, folder_id)
        if folder is None:
            return True
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "trash",
                "target": {"stableId": folder.stable_id},
                "trashFolder": {"stableId": catalog.trash_folder.stable_id},
                "expectedRevision": None,
            },
            body=None,
        )
        return (
            find_canonical_folder(
                read_canonical_writing_catalog(session=session),
                folder_id,
            )
            is None
        )


def _encode_diary(document: DiaryDocument) -> bytes:
    return encode_diary_document(
        stable_id=document.stable_id,
        entry_date=document.entry_date,
        title=document.title,
        mood=document.mood,
        folder_id=document.folder_id,
        html=document.html,
        cover_uri=document.cover_uri,
        attachment_uris=document.attachment_uris,
        legacy_id=document.legacy_id,
        legacy_folder_id=document.legacy_folder_id,
        source=document.source,
        created_at=document.created_at,
        updated_at=document.updated_at,
        attachment_metadata=document.attachment_metadata,
    )


def _require_folder(
    catalog: CanonicalWritingCatalog,
    folder_id: int | None,
) -> CanonicalDiaryFolder | None:
    if folder_id is None:
        return None
    folder = find_canonical_folder(catalog, folder_id)
    if folder is None:
        raise WritingMutationError("Folder must belong to this user.")
    return folder


def _attachment_metadata(document: DiaryDocument, attachment_id: int) -> dict[str, Any]:
    attachment = next(
        (item for item in document.attachment_metadata if item.get("legacyId") == attachment_id),
        None,
    )
    if attachment is None:
        raise WritingMutationError("Cover attachment must belong to this entry.")
    return attachment


def _entry_legacy_id(record: CanonicalDiaryEntry) -> int:
    value = record.document.legacy_id
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise WritingMutationError("Canonical diary entry has no API identity.")


def _new_legacy_id(existing: set[int | None]) -> int:
    while True:
        candidate = secrets.randbelow((1 << 52) - 1) + 1
        if candidate not in existing:
            return candidate


def _execute(
    *,
    session: Any,
    catalog: CanonicalWritingCatalog,
    mutation: dict[str, object],
    body: bytes | None,
) -> dict[str, object]:
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
        or not changes
        or not all(isinstance(change, dict) for change in changes)
    ):
        raise WritingMutationError("Native CoreFS writing mutation result is invalid.")
    publish_content_authority_after_mutation(
        session,
        generation=int(result["generation"]),
        catalog_hash=str(result["catalogHash"]),
    )
    return result


def _writing_lock(user_id: int) -> RLock:
    with _locks_guard:
        return _locks.setdefault(user_id, RLock())


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
