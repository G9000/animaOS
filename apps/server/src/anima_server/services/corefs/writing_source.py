from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.services.corefs.formats import (
    ACCOUNT_PROFILE_CONTENT_TYPE,
    DIARY_CONTENT_TYPE,
    DRAFT_CONTENT_TYPE,
    NOTE_CONTENT_TYPE,
    PREFERENCES_CONTENT_TYPE,
    TASK_CONTENT_TYPE,
    decode_draft_document,
    decode_note_document,
    decode_preferences_document,
    encode_account_profile_document,
    encode_diary_document,
    encode_draft_document,
    encode_note_document,
    encode_preferences_document,
    encode_task_document,
)

_SOURCE_SCHEMA_VERSION = 1
_SOURCE_SCOPE = "pcf004-writing-v1"
_RECONCILIATION_ITEMS = 100
_RECONCILIATION_BYTES = 2 * 1024 * 1024
_MAX_SOURCE_RETRIES = 3
_OPAQUE_REFERENCE = re.compile(r"corefs://object/([0-7][0-9A-HJKMNP-TV-Z]{25})")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

BodySource = Literal[
    "sql_attachment",
    "sql_account",
    "sql_diary",
    "sql_inline",
    "sql_preferences",
    "sql_task",
    "prepared",
    "staged_draft",
    "staged_draft_inline",
    "staged_note",
    "supplemental",
    "supplemental_path",
]


@dataclass(frozen=True, slots=True)
class WritingSourceObjectDescriptor:
    stable_id: str
    parent_id: str
    name: str
    kind: str
    content_type: str
    body_encoding: str
    body_length: int
    content_sha256: str
    source_fingerprint_sha256: str
    created_at: str
    updated_at: str
    revision: int
    object_key_epoch: int = 1
    source_character_count: int | None = None
    references: tuple[str, ...] = ()
    policy: str = "inherit"
    stable_role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    converter_format_version: int = 1
    body_source: BodySource = "prepared"
    source_key: str = ""
    source_digest: str | None = None
    prepared_path: str | None = None

    def native_object(self) -> dict[str, object]:
        return {
            "objectId": self.stable_id,
            "revision": self.revision,
            "objectKeyEpoch": self.object_key_epoch,
            "kind": self.kind,
            "parentId": self.parent_id,
            "name": self.name,
            "contentType": self.content_type,
            "bodyEncoding": self.body_encoding,
            "bodyLength": self.body_length,
            "contentSha256": self.content_sha256,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "sourceCharacterCount": self.source_character_count,
            "references": list(self.references),
            "policy": self.policy,
            "stableRole": self.stable_role,
            "graphMetadata": self.metadata,
            "sourceFingerprintSha256": self.source_fingerprint_sha256,
            "converterFormatVersion": self.converter_format_version,
        }


def _validate_staged_draft_handoff(
    *,
    existing: Any | None,
    draft: Any,
    encoded_content_sha256: str,
) -> None:
    """Reject replay or mutation of an already-published browser revision."""
    if existing is None or existing.kind != "draft":
        return
    token = existing.metadata.get("handoffToken")
    if token is None:
        return
    if not isinstance(token, dict):
        raise ValueError("Prepared draft handoff token is invalid.")

    draft_id = token.get("draftId")
    revision = token.get("clientRevision")
    content_sha256 = token.get("contentSha256")
    if (
        not isinstance(draft_id, str)
        or not draft_id
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not isinstance(content_sha256, str)
        or _SHA256_HEX.fullmatch(content_sha256) is None
    ):
        raise ValueError("Prepared draft handoff token is invalid.")
    if draft.id != draft_id:
        raise ValueError("Draft handoff ID conflicts with the prepared revision.")
    if draft.client_revision is None or draft.content_sha256 is None:
        raise ValueError("Versioned draft handoff fields are required.")
    if draft.client_revision < revision:
        raise ValueError("Draft handoff revision is stale.")
    if draft.client_revision == revision:
        if draft.content_sha256 != content_sha256:
            raise ValueError("Draft handoff revision conflicts with different content.")
        if encoded_content_sha256 != existing.content_hash:
            raise ValueError("Draft handoff revision conflicts with different metadata.")


@dataclass(frozen=True, slots=True)
class WritingSourceInventory:
    source_generation: int
    source_digest: str
    expected_head: tuple[int, str] | None
    folders: tuple[Any, ...]
    objects: tuple[WritingSourceObjectDescriptor, ...]
    source_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class WritingSourceBody:
    descriptor: WritingSourceObjectDescriptor
    body: bytes | None


@dataclass(frozen=True, slots=True)
class _AttachmentMetadata:
    id: int
    entry_id: int
    kind: str
    mime_type: str
    size_bytes: int
    storage_path: str
    sha256: str
    filename: str | None
    caption: str | None
    created_at: str | None


@dataclass(frozen=True, slots=True)
class _PortableStateSource:
    stable_id: str
    kind: Literal["account-profile", "preferences", "task"]
    name: str
    content_type: str
    body: bytes
    created_at: str
    updated_at: str
    source_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


def read_writing_source_generation(db: Session, *, user_id: int) -> int:
    from anima_server.models import CoreFSWritingSourceState

    value = db.scalar(
        select(CoreFSWritingSourceState.generation).where(
            CoreFSWritingSourceState.user_id == user_id
        )
    )
    # Native preparation requires a positive fence. Before the first legacy
    # write there is no trigger-created row; generation one represents that
    # empty initial state and the inventory digest still detects the first row.
    return int(value) if value is not None else 1


@contextmanager
def begin_writing_source_fence(db: Session) -> Iterator[Session]:
    """Hold one dedicated SQLite/SQLCipher BEGIN IMMEDIATE to publication."""
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    if engine.dialect.name != "sqlite":
        raise RuntimeError("PCF-004 writing preparation requires a SQLite/SQLCipher source.")
    if db.in_transaction():
        db.rollback()
    connection = engine.connect()
    fenced = Session(bind=connection, autoflush=False, expire_on_commit=False)
    try:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        yield fenced
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        fenced.close()
        connection.close()


def build_writing_source_inventory(
    *,
    session: Any,
    db: Session,
    staged_drafts: Iterable[Any] = (),
    staged_notes: Iterable[Any] = (),
    supplemental_folders: Iterable[Any] = (),
    supplemental_objects: Iterable[WritingSourceBody] = (),
) -> WritingSourceInventory:
    from anima_server.models import DiaryAttachment, DiaryEntry, DiaryFolder
    from anima_server.services.corefs.diary_migration import (
        DiaryMigrationError,
        InactiveFolder,
        _native_folder_policy,
        _portable_catalog_names,
        migration_opaque_id,
        read_prepared_writing_body,
        read_prepared_writing_snapshot,
    )
    from anima_server.services.data_crypto import df

    staged_drafts = tuple(staged_drafts)
    staged_notes = tuple(staged_notes)
    supplemental_folders = tuple(supplemental_folders)
    supplemental_objects = tuple(supplemental_objects)
    if len(staged_drafts) > 1 or len(staged_notes) > 1:
        raise DiaryMigrationError("Writing preparation accepts at most one staged draft and note.")

    try:
        head_value = session.corefs_session.validation_snapshot(session.corefs_keys)
        expected_head = (int(head_value["generation"]), str(head_value["catalogHash"]))
    except ValueError as exc:
        if str(exc) != "CoreFS validation snapshot is missing":
            raise DiaryMigrationError("CoreFS validation head could not be opened.") from exc
        expected_head = None
        current_folders: tuple[Any, ...] = ()
        current_objects: tuple[Any, ...] = ()
    else:
        current = read_prepared_writing_snapshot(session=session)
        current_folders = current.folders
        current_objects = current.objects

    allowed_kinds = {
        "account-profile",
        "diary",
        "attachment",
        "draft",
        "gallery-asset",
        "knowledge-source",
        "note",
        "preferences",
        "task",
        "thread",
        "message-segment",
    }
    unknown = sorted({item.kind for item in current_objects if item.kind not in allowed_kinds})
    if unknown:
        raise DiaryMigrationError(
            "Writing preparation cannot replace unrelated object families: " + ", ".join(unknown)
        )

    current_revisions = {item.stable_id: item.revision for item in current_objects}
    current_objects_by_id = {item.stable_id: item for item in current_objects}
    current_attachments = {
        item.stable_id: item for item in current_objects if item.kind == "attachment"
    }
    preserved_roles = {item.role: item for item in current_folders if item.role is not None}
    root = next((item for item in current_folders if item.parent_id is None), None)
    journal = preserved_roles.get("core.journal")
    notes = preserved_roles.get("core.notes")
    supplemental_roles = {item.role: item for item in supplemental_folders if item.role is not None}
    if len(supplemental_roles) != sum(item.role is not None for item in supplemental_folders):
        raise DiaryMigrationError("Supplemental source contains duplicate stable roles.")
    if set(supplemental_roles) - {"core.conversations", "core.gallery"}:
        raise DiaryMigrationError("Supplemental source contains an unsupported stable role.")
    conversations = supplemental_roles.get("core.conversations") or preserved_roles.get(
        "core.conversations"
    )
    gallery = supplemental_roles.get("core.gallery") or preserved_roles.get("core.gallery")
    root_id = root.stable_id if root is not None else migration_opaque_id("core-folder", "root")
    journal_id = (
        journal.stable_id
        if journal is not None
        else migration_opaque_id("core-folder-role", "core.journal")
    )
    notes_id = (
        notes.stable_id
        if notes is not None
        else migration_opaque_id("core-folder-role", "core.notes")
    )
    conversations_id = (
        conversations.stable_id
        if conversations is not None
        else migration_opaque_id("core-folder-role", "core.conversations")
    )
    gallery_id = (
        gallery.stable_id
        if gallery is not None
        else migration_opaque_id("core-folder-role", "core.gallery")
    )
    folders: list[Any] = [
        InactiveFolder(
            stable_id=root_id,
            parent_id=None,
            name=root.name if root is not None else "Core",
            order=0,
            role=None,
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
        InactiveFolder(
            stable_id=journal_id,
            parent_id=journal.parent_id if journal is not None else root_id,
            name=journal.name if journal is not None else "Journal",
            order=0,
            role="core.journal",
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
        InactiveFolder(
            stable_id=notes_id,
            parent_id=notes.parent_id if notes is not None else root_id,
            name=notes.name if notes is not None else "Notes",
            order=1,
            role="core.notes",
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
    ]
    if conversations is not None:
        folders.append(
            InactiveFolder(
                stable_id=conversations_id,
                parent_id=root_id,
                name=conversations.name,
                order=2,
                role="core.conversations",
                owner="shared",
                agent_access="manage",
                policy="shared-manage",
                metadata=getattr(conversations, "metadata", {}),
            )
        )
    if gallery is not None:
        folders.append(
            InactiveFolder(
                stable_id=gallery_id,
                parent_id=gallery.parent_id or root_id,
                name=gallery.name,
                order=3,
                role="core.gallery",
                owner="user",
                agent_access="write",
                policy="user-write",
                metadata=getattr(gallery, "metadata", {}),
            )
        )

    folder_rows = db.scalars(
        select(DiaryFolder)
        .where(DiaryFolder.user_id == session.user_id)
        .order_by(DiaryFolder.created_at, DiaryFolder.id)
    ).all()
    for order, row in enumerate(folder_rows):
        display_name = (
            df(session.user_id, row.name, table="diary_folders", field="name") or row.name
        )
        native_policy = _native_folder_policy("inherit")
        folders.append(
            InactiveFolder(
                stable_id=migration_opaque_id("diary-folder", str(row.id)),
                parent_id=journal_id,
                name=display_name,
                order=order,
                role=None,
                owner="user",
                agent_access="write" if native_policy == "inherit" else "none",
                policy=native_policy,
                metadata={
                    "legacyId": row.id,
                    "displayName": display_name,
                    "originalName": display_name,
                    "order": order,
                    "policy": "inherit",
                    "createdAt": _timestamp(row.created_at),
                },
            )
        )

    attachment_rows = db.execute(
        select(
            DiaryAttachment.id,
            DiaryAttachment.entry_id,
            DiaryAttachment.kind,
            DiaryAttachment.mime_type,
            DiaryAttachment.size_bytes,
            DiaryAttachment.storage_path,
            DiaryAttachment.sha256,
            DiaryAttachment.original_filename,
            DiaryAttachment.caption,
            DiaryAttachment.created_at,
        )
        .where(DiaryAttachment.user_id == session.user_id)
        .order_by(DiaryAttachment.created_at, DiaryAttachment.id)
    ).all()
    attachments: dict[int, _AttachmentMetadata] = {}
    attachments_by_entry: dict[int, list[_AttachmentMetadata]] = {}
    for row in attachment_rows:
        item = _AttachmentMetadata(
            id=int(row.id),
            entry_id=int(row.entry_id),
            kind=str(row.kind),
            mime_type=str(row.mime_type),
            size_bytes=int(row.size_bytes),
            storage_path=str(row.storage_path),
            sha256=str(row.sha256),
            filename=df(
                session.user_id,
                row.original_filename,
                table="diary_attachments",
                field="original_filename",
            )
            or None,
            caption=df(
                session.user_id,
                row.caption,
                table="diary_attachments",
                field="caption",
            )
            or None,
            created_at=_timestamp(row.created_at),
        )
        attachments[item.id] = item
        attachments_by_entry.setdefault(item.entry_id, []).append(item)

    descriptors: dict[str, WritingSourceObjectDescriptor] = {}

    def add(item: WritingSourceObjectDescriptor, *, replace_existing: bool = False) -> None:
        existing = descriptors.get(item.stable_id)
        if existing is None or replace_existing:
            descriptors[item.stable_id] = item
            return
        if (
            existing.content_sha256 != item.content_sha256
            or existing.kind != item.kind
            or existing.content_type != item.content_type
        ):
            raise DiaryMigrationError("Writing source contains a conflicting stable object ID.")

    # Preserve CoreFS-native drafts and notes body-at-a-time. Their referenced
    # attachments are admitted only after their authenticated body is decoded.
    referenced_current_attachments: set[str] = set()
    conversation_references: set[str] = set()
    for item in sorted(current_objects, key=lambda value: value.stable_id):
        if item.kind not in {"draft", "note"}:
            continue
        body = read_prepared_writing_body(session=session, item=item)
        if item.kind == "draft":
            decoded = decode_draft_document(body)
            references = tuple(
                dict.fromkeys(
                    (
                        *((decoded.target_id,) if decoded.target_id is not None else ()),
                        *_OPAQUE_REFERENCE.findall(decoded.body),
                    )
                )
            )
            referenced_current_attachments.update(_OPAQUE_REFERENCE.findall(decoded.body))
            source_count = _source_character_count(item.metadata, decoded.body)
        else:
            decoded_note = decode_note_document(body)
            references = ()
            source_count = _source_character_count(item.metadata, decoded_note.body)
        add(
            _prepared_descriptor(
                item,
                revision=current_revisions[item.stable_id] + 1,
                references=references,
                source_character_count=source_count,
            )
        )
        if item.kind == "draft":
            del decoded
        else:
            del decoded_note
        del body

    supplemental_ids = {item.descriptor.stable_id for item in supplemental_objects}
    for item in sorted(current_objects, key=lambda value: value.stable_id):
        is_gallery_object = item.kind in {"gallery-asset", "knowledge-source"} or (
            item.kind == "attachment" and item.parent_id == gallery_id
        )
        if not is_gallery_object or item.stable_id in supplemental_ids:
            continue
        body = read_prepared_writing_body(session=session, item=item)
        add(_prepared_descriptor(item, revision=current_revisions[item.stable_id] + 1))
        del body

    for item in sorted(current_objects, key=lambda value: value.stable_id):
        if item.kind not in {"thread", "message-segment"}:
            continue
        if item.stable_id in supplemental_ids:
            continue
        body = read_prepared_writing_body(session=session, item=item)
        if item.kind == "thread":
            from anima_server.services.corefs.messages import decode_thread_document

            references = decode_thread_document(body).segment_ids
        else:
            from anima_server.services.corefs.messages import (
                decode_message_segment,
                message_segment_references,
            )

            previous = item.metadata.get("previousSegmentSha256")
            previous_id = item.metadata.get("previousSegmentId")
            if previous is not None and not isinstance(previous, str):
                raise DiaryMigrationError("Prepared message segment chain metadata is invalid.")
            if previous_id is not None and not isinstance(previous_id, str):
                raise DiaryMigrationError("Prepared message segment chain metadata is invalid.")
            decoded_segment = decode_message_segment(
                body,
                expected_previous_segment_id=previous_id,
                expected_previous_sha256=previous,
            )
            references = message_segment_references(decoded_segment)
            conversation_references.update(references)
        add(
            _prepared_descriptor(
                item,
                revision=current_revisions[item.stable_id] + 1,
                references=references,
            )
        )
        del body

    referenced_current_attachments.update(
        reference for reference in conversation_references if reference in current_attachments
    )
    for attachment_id in sorted(referenced_current_attachments):
        item = current_attachments.get(attachment_id)
        if item is None:
            raise DiaryMigrationError("Prepared draft contains a dangling attachment reference.")
        add(_prepared_descriptor(item, revision=item.revision + 1))

    portable_state_sources = _account_settings_sources(
        session=session,
        db=db,
        current_objects=current_objects,
    )
    for source in portable_state_sources:
        body_digest = hashlib.sha256(source.body).hexdigest()
        add(
            WritingSourceObjectDescriptor(
                stable_id=source.stable_id,
                parent_id=root_id,
                name=source.name,
                kind=source.kind,
                content_type=source.content_type,
                body_encoding="utf-8",
                body_length=len(source.body),
                content_sha256=body_digest,
                source_fingerprint_sha256=body_digest,
                created_at=source.created_at,
                updated_at=source.updated_at,
                revision=current_revisions.get(source.stable_id, 0) + 1,
                metadata=source.metadata,
                body_source={
                    "account-profile": "sql_account",
                    "preferences": "sql_preferences",
                    "task": "sql_task",
                }[source.kind],
                source_key=source.source_key,
            ),
            replace_existing=True,
        )

    entry_ids = tuple(
        int(value)
        for value in db.scalars(
            select(DiaryEntry.id)
            .where(DiaryEntry.user_id == session.user_id)
            .order_by(DiaryEntry.id)
        ).all()
    )
    for entry_id in entry_ids:
        row = db.scalar(
            select(DiaryEntry).where(
                DiaryEntry.id == entry_id,
                DiaryEntry.user_id == session.user_id,
            )
        )
        if row is None:
            raise DiaryMigrationError("Diary source changed while inventory was read.")
        entry_attachments = tuple(
            sorted(attachments_by_entry.get(entry_id, ()), key=lambda x: x.id)
        )
        attachment_ids = {item.id for item in entry_attachments}
        if row.cover_attachment_id is not None and row.cover_attachment_id not in attachment_ids:
            raise DiaryMigrationError("Legacy diary cover must belong to the same entry.")
        parent_id = (
            migration_opaque_id("diary-folder", str(row.folder_id))
            if row.folder_id is not None
            else journal_id
        )
        for attachment in entry_attachments:
            stable_id = migration_opaque_id("diary-attachment", str(attachment.id))
            created_at = (
                attachment.created_at or _timestamp(row.created_at) or _entry_time(row.entry_date)
            )
            updated_at = (
                attachment.created_at or _timestamp(row.updated_at) or _entry_time(row.entry_date)
            )
            add(
                WritingSourceObjectDescriptor(
                    stable_id=stable_id,
                    parent_id=parent_id,
                    name=attachment.filename or stable_id,
                    kind="attachment",
                    content_type=attachment.mime_type,
                    body_encoding="binary",
                    body_length=attachment.size_bytes,
                    content_sha256=attachment.sha256,
                    source_fingerprint_sha256=_json_hash(
                        {
                            "storagePath": attachment.storage_path,
                            "size": attachment.size_bytes,
                            "sha256": attachment.sha256,
                        }
                    ),
                    created_at=created_at,
                    updated_at=updated_at,
                    revision=current_revisions.get(stable_id, 0) + 1,
                    metadata={
                        "legacyId": attachment.id,
                        "legacyEntryId": attachment.entry_id,
                        "displayName": attachment.filename,
                        "originalName": attachment.filename,
                        "kind": attachment.kind,
                        "caption": attachment.caption,
                        "filename": attachment.filename,
                        "sha256": attachment.sha256,
                        "createdAt": attachment.created_at,
                    },
                    body_source="sql_attachment",
                    source_key=str(attachment.id),
                ),
                replace_existing=True,
            )

        def add_inline(
            mime_type: str,
            data: bytes,
            digest: str,
            parent: str = parent_id,
            created: str = _timestamp(row.created_at) or _entry_time(row.entry_date),
            updated: str = _timestamp(row.updated_at) or _entry_time(row.entry_date),
            entry_key: str = str(entry_id),
        ) -> str:
            stable_id = migration_opaque_id("diary-inline-media", digest)
            add(
                WritingSourceObjectDescriptor(
                    stable_id=stable_id,
                    parent_id=parent,
                    name=f"inline-{digest[:12]}",
                    kind="attachment",
                    content_type=mime_type,
                    body_encoding="binary",
                    body_length=len(data),
                    content_sha256=digest,
                    source_fingerprint_sha256=digest,
                    created_at=created,
                    updated_at=updated,
                    revision=current_revisions.get(stable_id, 0) + 1,
                    body_source="sql_inline",
                    source_key=entry_key,
                    source_digest=digest,
                )
            )
            return f"corefs://object/{stable_id}"

        body, source_body, references = _encode_entry(
            user_id=session.user_id,
            row=row,
            attachments=entry_attachments,
            media_reference_factory=add_inline,
        )
        stable_id = migration_opaque_id("diary-entry", str(entry_id))
        add(
            WritingSourceObjectDescriptor(
                stable_id=stable_id,
                parent_id=parent_id,
                name=f"{row.entry_date}-{entry_id}.diary.json",
                kind="diary",
                content_type=DIARY_CONTENT_TYPE,
                body_encoding="utf-8",
                body_length=len(body),
                content_sha256=hashlib.sha256(body).hexdigest(),
                source_fingerprint_sha256=hashlib.sha256(source_body.encode()).hexdigest(),
                created_at=_timestamp(row.created_at) or _entry_time(row.entry_date),
                updated_at=_timestamp(row.updated_at) or _entry_time(row.entry_date),
                revision=current_revisions.get(stable_id, 0) + 1,
                source_character_count=len(source_body),
                references=references,
                metadata={
                    "legacyId": entry_id,
                    "legacyFolderId": row.folder_id,
                    "source": row.source,
                    "createdAt": _timestamp(row.created_at),
                    "updatedAt": _timestamp(row.updated_at),
                    "sourceCharacterCount": len(source_body),
                },
                body_source="sql_diary",
                source_key=str(entry_id),
            ),
            replace_existing=True,
        )
        del body
        del source_body
        del references
        db.expunge(row)

    for draft in staged_drafts:
        stable_id = draft.stable_id or migration_opaque_id("diary-draft", draft.id)
        if draft.content_sha256 is not None:
            actual = hashlib.sha256(draft.body.encode()).hexdigest()
            if actual != draft.content_sha256:
                raise DiaryMigrationError("Draft handoff content hash changed before preparation.")
        target_id = draft.target_stable_id or (
            migration_opaque_id("diary-entry", str(draft.target_entry_id))
            if draft.target_entry_id is not None
            else None
        )
        existing_references = tuple(dict.fromkeys(_OPAQUE_REFERENCE.findall(draft.body)))
        for reference_id in existing_references:
            attachment = current_attachments.get(reference_id)
            if attachment is None:
                raise DiaryMigrationError("Draft contains a dangling CoreFS attachment reference.")
            add(_prepared_descriptor(attachment, revision=attachment.revision + 1))
        inline_ids: list[str] = []

        def add_draft_inline(
            mime_type: str,
            data: bytes,
            digest: str,
            created: str = draft.created_at or draft.updated_at,
            updated: str = draft.updated_at,
            draft_key: str = draft.id,
            references: list[str] = inline_ids,
        ) -> str:
            media_id = migration_opaque_id("diary-inline-media", digest)
            add(
                WritingSourceObjectDescriptor(
                    stable_id=media_id,
                    parent_id=journal_id,
                    name=f"inline-{digest[:12]}",
                    kind="attachment",
                    content_type=mime_type,
                    body_encoding="binary",
                    body_length=len(data),
                    content_sha256=digest,
                    source_fingerprint_sha256=digest,
                    created_at=created,
                    updated_at=updated,
                    revision=current_revisions.get(media_id, 0) + 1,
                    metadata={"origin": "legacy-local-storage-draft"},
                    body_source="staged_draft_inline",
                    source_key=draft_key,
                    source_digest=digest,
                )
            )
            references.append(media_id)
            return f"corefs://object/{media_id}"

        draft_body = encode_draft_document(
            stable_id=stable_id,
            target_id=target_id,
            content_type=draft.content_type,
            body=draft.body,
            metadata=draft.metadata,
            media_reference_factory=add_draft_inline,
        )
        draft_body_sha256 = hashlib.sha256(draft_body).hexdigest()
        _validate_staged_draft_handoff(
            existing=current_objects_by_id.get(stable_id),
            draft=draft,
            encoded_content_sha256=draft_body_sha256,
        )
        graph_metadata = dict(draft.native_metadata or {})
        if draft.client_revision is not None and draft.content_sha256 is not None:
            graph_metadata["handoffToken"] = {
                "draftId": draft.id,
                "clientRevision": draft.client_revision,
                "contentSha256": draft.content_sha256,
            }
        source_count = (
            draft.source_character_count
            if draft.source_character_count is not None
            else len(draft.body)
        )
        if draft.native_metadata is None or "sourceCharacterCount" in draft.native_metadata:
            graph_metadata["sourceCharacterCount"] = source_count
        add(
            WritingSourceObjectDescriptor(
                stable_id=stable_id,
                parent_id=journal_id,
                name=f"{stable_id}.draft.json",
                kind="draft",
                content_type=DRAFT_CONTENT_TYPE,
                body_encoding="utf-8",
                body_length=len(draft_body),
                content_sha256=draft_body_sha256,
                source_fingerprint_sha256=hashlib.sha256(draft.body.encode()).hexdigest(),
                created_at=draft.created_at or draft.updated_at,
                updated_at=draft.updated_at,
                revision=current_revisions.get(stable_id, 0) + 1,
                source_character_count=source_count,
                references=tuple(
                    dict.fromkeys(
                        (
                            *((target_id,) if target_id is not None else ()),
                            *existing_references,
                            *inline_ids,
                        )
                    )
                ),
                metadata=graph_metadata,
                body_source="staged_draft",
                source_key=draft.id,
            ),
            replace_existing=True,
        )
        del draft_body

    for note in staged_notes:
        stable_id = note.stable_id or migration_opaque_id("note", note.id)
        note_body = encode_note_document(
            stable_id=stable_id,
            title=note.title,
            content_type=note.content_type,
            body=note.body,
        )
        source_count = (
            note.source_character_count
            if note.source_character_count is not None
            else len(note.body)
        )
        metadata = dict(note.native_metadata or {})
        if note.native_metadata is None or "sourceCharacterCount" in note.native_metadata:
            metadata["sourceCharacterCount"] = source_count
        add(
            WritingSourceObjectDescriptor(
                stable_id=stable_id,
                parent_id=notes_id,
                name=f"{stable_id}.note.json",
                kind="note",
                content_type=NOTE_CONTENT_TYPE,
                body_encoding="utf-8",
                body_length=len(note_body),
                content_sha256=hashlib.sha256(note_body).hexdigest(),
                source_fingerprint_sha256=hashlib.sha256(note.body.encode()).hexdigest(),
                created_at=note.created_at or note.updated_at,
                updated_at=note.updated_at,
                revision=current_revisions.get(stable_id, 0) + 1,
                source_character_count=source_count,
                metadata=metadata,
                body_source="staged_note",
                source_key=note.id,
            ),
            replace_existing=True,
        )
        del note_body

    for supplemental in supplemental_objects:
        descriptor = supplemental.descriptor
        body = supplemental.body
        if descriptor.kind not in {
            "thread",
            "message-segment",
            "attachment",
            "gallery-asset",
            "knowledge-source",
        }:
            raise DiaryMigrationError("Supplemental source contains an unsupported object kind.")
        allowed_parents = (
            {conversations_id} if descriptor.kind in {"thread", "message-segment"} else {gallery_id}
        )
        if descriptor.kind == "attachment":
            allowed_parents.add(conversations_id)
        if descriptor.parent_id not in allowed_parents:
            raise DiaryMigrationError("Supplemental object has the wrong stable parent.")
        if descriptor.body_source not in {"supplemental", "supplemental_path"}:
            descriptor = replace(descriptor, body_source="supplemental")
        descriptor = replace(
            descriptor,
            revision=current_revisions.get(descriptor.stable_id, 0) + 1,
        )
        if descriptor.body_source == "supplemental":
            if (
                body is None
                or len(body) != descriptor.body_length
                or hashlib.sha256(body).hexdigest() != descriptor.content_sha256
            ):
                raise DiaryMigrationError("Supplemental body identity is invalid.")
        elif body is not None or not descriptor.source_key:
            raise DiaryMigrationError("Supplemental path source identity is invalid.")
        add(descriptor, replace_existing=True)

    folders, normalized_objects = _portable_catalog_names(
        folders,
        list(descriptors.values()),
    )
    objects = tuple(sorted(normalized_objects, key=lambda item: item.stable_id))
    folders_tuple = tuple(folders)
    source_generation = read_writing_source_generation(db, user_id=session.user_id)
    counts = {
        "folders": len(folder_rows),
        "entries": len(entry_ids),
        "attachments": sum(item.kind == "attachment" for item in objects),
        "drafts": sum(item.kind == "draft" for item in objects),
        "notes": sum(item.kind == "note" for item in objects),
        "threads": sum(item.kind == "thread" for item in objects),
        "messageSegments": sum(item.kind == "message-segment" for item in objects),
        "galleryAssets": sum(item.kind == "gallery-asset" for item in objects),
        "knowledgeSources": sum(item.kind == "knowledge-source" for item in objects),
        "accountProfiles": sum(item.kind == "account-profile" for item in objects),
        "preferences": sum(item.kind == "preferences" for item in objects),
        "tasks": sum(item.kind == "task" for item in objects),
        "conversationRoot": int(conversations is not None),
        "galleryRoot": int(gallery is not None),
    }
    source_digest = _source_inventory_hash(
        user_id=session.user_id,
        source_generation=source_generation,
        folders=folders_tuple,
        objects=objects,
        counts=counts,
    )
    return WritingSourceInventory(
        source_generation=source_generation,
        source_digest=source_digest,
        expected_head=expected_head,
        folders=folders_tuple,
        objects=objects,
        source_counts=counts,
    )


def iter_writing_source_objects(
    *,
    session: Any,
    db: Session,
    inventory: WritingSourceInventory,
    staged_drafts: Iterable[Any] = (),
    staged_notes: Iterable[Any] = (),
    supplemental_objects: Iterable[WritingSourceBody] = (),
) -> Iterator[WritingSourceBody]:
    from anima_server.models import DiaryAttachment, DiaryEntry
    from anima_server.services.corefs.diary_migration import (
        DiaryMigrationError,
        read_prepared_writing_body,
        read_prepared_writing_snapshot,
    )
    from anima_server.services.diary import read_attachment_blob

    drafts = {item.id: item for item in staged_drafts}
    notes = {item.id: item for item in staged_notes}
    supplemental = {item.descriptor.stable_id: item for item in supplemental_objects}
    prepared = None
    portable_state_bodies: dict[str, bytes] | None = None
    attachments_by_entry = _attachment_metadata_by_entry(db, user_id=session.user_id)

    for descriptor in inventory.objects:
        body: bytes
        if descriptor.body_source == "sql_attachment":
            row = db.scalar(
                select(DiaryAttachment).where(
                    DiaryAttachment.id == int(descriptor.source_key),
                    DiaryAttachment.user_id == session.user_id,
                )
            )
            if row is None:
                raise DiaryMigrationError("Diary attachment changed during preparation.")
            decrypted = read_attachment_blob(user_id=session.user_id, attachment=row)
            body = decrypted.data
            db.expunge(row)
        elif descriptor.body_source in {"sql_account", "sql_preferences", "sql_task"}:
            if portable_state_bodies is None:
                try:
                    current_objects = read_prepared_writing_snapshot(session=session).objects
                except ValueError as exc:
                    if str(exc) != "CoreFS validation snapshot is missing":
                        raise
                    current_objects = ()
                portable_state_bodies = {
                    source.stable_id: source.body
                    for source in _account_settings_sources(
                        session=session,
                        db=db,
                        current_objects=current_objects,
                    )
                }
            body = portable_state_bodies.get(descriptor.stable_id, b"")
        elif descriptor.body_source in {"sql_diary", "sql_inline"}:
            row = db.scalar(
                select(DiaryEntry).where(
                    DiaryEntry.id == int(descriptor.source_key),
                    DiaryEntry.user_id == session.user_id,
                )
            )
            if row is None:
                raise DiaryMigrationError("Diary entry changed during preparation.")
            captured: bytes | None = None

            def capture(
                _mime_type: str,
                data: bytes,
                digest: str,
                expected_digest: str | None = descriptor.source_digest,
            ) -> str:
                nonlocal captured
                if digest == expected_digest:
                    captured = data
                from anima_server.services.corefs.diary_migration import migration_opaque_id

                return f"corefs://object/{migration_opaque_id('diary-inline-media', digest)}"

            encoded, _source, _references = _encode_entry(
                user_id=session.user_id,
                row=row,
                attachments=tuple(attachments_by_entry.get(int(descriptor.source_key), ())),
                media_reference_factory=capture,
            )
            body = encoded if descriptor.body_source == "sql_diary" else (captured or b"")
            db.expunge(row)
        elif descriptor.body_source == "prepared":
            if prepared is None:
                prepared = {
                    item.stable_id: item
                    for item in read_prepared_writing_snapshot(session=session).objects
                }
            item = prepared.get(descriptor.stable_id)
            if item is None or item.path != descriptor.prepared_path:
                raise DiaryMigrationError("Prepared writing object changed during preparation.")
            body = read_prepared_writing_body(session=session, item=item)
        elif descriptor.body_source in {"staged_draft", "staged_draft_inline"}:
            draft = drafts.get(descriptor.source_key)
            if draft is None:
                raise DiaryMigrationError("Staged draft body is unavailable.")
            captured = None

            def capture_draft(
                _mime_type: str,
                data: bytes,
                digest: str,
                expected_digest: str | None = descriptor.source_digest,
            ) -> str:
                nonlocal captured
                if digest == expected_digest:
                    captured = data
                from anima_server.services.corefs.diary_migration import migration_opaque_id

                return f"corefs://object/{migration_opaque_id('diary-inline-media', digest)}"

            stable_id = draft.stable_id
            if stable_id is None:
                from anima_server.services.corefs.diary_migration import migration_opaque_id

                stable_id = migration_opaque_id("diary-draft", draft.id)
            target_id = draft.target_stable_id
            if target_id is None and draft.target_entry_id is not None:
                from anima_server.services.corefs.diary_migration import migration_opaque_id

                target_id = migration_opaque_id("diary-entry", str(draft.target_entry_id))
            encoded = encode_draft_document(
                stable_id=stable_id,
                target_id=target_id,
                content_type=draft.content_type,
                body=draft.body,
                metadata=draft.metadata,
                media_reference_factory=capture_draft,
            )
            body = encoded if descriptor.body_source == "staged_draft" else (captured or b"")
        elif descriptor.body_source == "staged_note":
            note = notes.get(descriptor.source_key)
            if note is None:
                raise DiaryMigrationError("Staged note body is unavailable.")
            stable_id = note.stable_id
            if stable_id is None:
                from anima_server.services.corefs.diary_migration import migration_opaque_id

                stable_id = migration_opaque_id("note", note.id)
            body = encode_note_document(
                stable_id=stable_id,
                title=note.title,
                content_type=note.content_type,
                body=note.body,
            )
        elif descriptor.body_source == "supplemental":
            source = supplemental.get(descriptor.stable_id)
            if source is None or source.body is None:
                raise DiaryMigrationError("Supplemental conversation body is unavailable.")
            body = source.body
        elif descriptor.body_source == "supplemental_path":
            from pathlib import Path

            source = supplemental.get(descriptor.stable_id)
            if source is None or source.body is not None:
                raise DiaryMigrationError("Supplemental attachment source is unavailable.")
            try:
                body = Path(source.descriptor.source_key).read_bytes()
            except OSError as exc:
                raise DiaryMigrationError("Supplemental attachment source is unreadable.") from exc
        else:  # pragma: no cover - closed Literal plus defensive corruption guard
            raise DiaryMigrationError("Unknown writing body source.")

        if len(body) != descriptor.body_length:
            raise DiaryMigrationError("Writing source body length changed during preparation.")
        if hashlib.sha256(body).hexdigest() != descriptor.content_sha256:
            raise DiaryMigrationError("Writing source body hash changed during preparation.")
        yield WritingSourceBody(descriptor=descriptor, body=body)
        del body
        if descriptor.body_source == "sql_attachment":
            del decrypted
        elif descriptor.body_source in {"sql_diary", "sql_inline"}:
            del encoded
            del _source
            del _references
            del captured
        elif descriptor.body_source in {"staged_draft", "staged_draft_inline"}:
            del encoded
            del captured


def _account_settings_sources(
    *,
    session: Any,
    db: Session,
    current_objects: Iterable[Any],
) -> tuple[_PortableStateSource, ...]:
    """Project legacy-authoritative account/settings rows into canonical bodies.

    Until PCF-008 flips global authority, SQLCipher remains the write source.
    Existing portable preference fields imported by the desktop are preserved,
    while the legacy presence subsection is deterministically refreshed.
    """
    from anima_server.models import AgentProfile, Task, User
    from anima_server.services.core import get_owner_id
    from anima_server.services.corefs.diary_migration import (
        DiaryMigrationError,
        migration_opaque_id,
        read_prepared_writing_body,
    )
    from anima_server.services.presence_config import get_presence_config_values

    user = db.get(User, session.user_id)
    if user is None:
        return ()
    owner_id = get_owner_id()
    if not owner_id:
        raise DiaryMigrationError("Account migration requires an opaque Core owner ID.")

    account_id = migration_opaque_id("account-profile", owner_id)
    preferences_id = migration_opaque_id("preferences", owner_id)
    current_by_id = {item.stable_id: item for item in current_objects}
    for item in current_objects:
        if item.kind == "account-profile" and item.stable_id != account_id:
            raise DiaryMigrationError("Core contains a conflicting account-profile object.")
        if item.kind == "preferences" and item.stable_id != preferences_id:
            raise DiaryMigrationError("Core contains a conflicting preferences object.")

    profile = db.scalar(select(AgentProfile).where(AgentProfile.user_id == session.user_id))
    created_at = _timestamp(user.created_at)
    updated_at = _timestamp(user.updated_at)
    if created_at is None or updated_at is None:
        raise DiaryMigrationError("Legacy account timestamps are incomplete.")
    account_body = encode_account_profile_document(
        stable_id=account_id,
        owner_id=owner_id,
        legacy_user_id=int(user.id),
        username=str(user.username),
        display_name=str(user.display_name),
        gender=user.gender,
        age=user.age,
        birthday=user.birthday,
        setup_complete=bool(profile.setup_complete) if profile is not None else False,
        created_at=created_at,
        updated_at=updated_at,
    )

    existing_values: dict[str, Any] = {}
    existing_preferences = current_by_id.get(preferences_id)
    if existing_preferences is not None:
        body = read_prepared_writing_body(session=session, item=existing_preferences)
        decoded = decode_preferences_document(body)
        if decoded.owner_id != owner_id:
            raise DiaryMigrationError("Prepared preferences owner does not match this Core.")
        existing_values.update(decoded.values)
        del body

    presence = get_presence_config_values(db, session.user_id)
    existing_values["presence"] = {
        "enabled": presence.enabled,
        "mainChatEnabled": presence.main_chat_enabled,
        "homeGreetingContextEnabled": presence.home_greeting_context_enabled,
        "taskNudgesEnabled": presence.task_nudges_enabled,
        "memoryNudgesEnabled": presence.memory_nudges_enabled,
        "checkInNudgesEnabled": presence.checkin_nudges_enabled,
        "customInstruction": presence.custom_instruction,
        "initiativeEnabled": presence.initiative_enabled,
        "quietHoursStart": presence.quiet_hours_start,
        "quietHoursEnd": presence.quiet_hours_end,
        "dreamSharing": presence.dream_sharing,
    }
    preferences_updated_at = updated_at
    if existing_preferences is not None and existing_preferences.updated_at > updated_at:
        preferences_updated_at = existing_preferences.updated_at
    preferences_body = encode_preferences_document(
        stable_id=preferences_id,
        owner_id=owner_id,
        values=existing_values,
        updated_at=preferences_updated_at,
    )

    sources = [
        _PortableStateSource(
            stable_id=account_id,
            kind="account-profile",
            name="account-profile.json",
            content_type=ACCOUNT_PROFILE_CONTENT_TYPE,
            body=account_body,
            created_at=created_at,
            updated_at=updated_at,
            source_key=str(user.id),
            metadata={"ownerId": owner_id},
        ),
        _PortableStateSource(
            stable_id=preferences_id,
            kind="preferences",
            name="preferences.json",
            content_type=PREFERENCES_CONTENT_TYPE,
            body=preferences_body,
            created_at=created_at,
            updated_at=preferences_updated_at,
            source_key=str(user.id),
            metadata={"ownerId": owner_id},
        ),
    ]
    tasks = db.scalars(select(Task).where(Task.user_id == session.user_id).order_by(Task.id)).all()
    for task in tasks:
        task_created_at = _timestamp(task.created_at)
        task_updated_at = _timestamp(task.updated_at)
        if task_created_at is None or task_updated_at is None:
            raise DiaryMigrationError("Legacy task timestamps are incomplete.")
        stable_id = migration_opaque_id("task", str(task.id))
        sources.append(
            _PortableStateSource(
                stable_id=stable_id,
                kind="task",
                name=f"task-{task.id}.json",
                content_type=TASK_CONTENT_TYPE,
                body=encode_task_document(
                    stable_id=stable_id,
                    legacy_id=int(task.id),
                    text=str(task.text),
                    done=bool(task.done),
                    priority=int(task.priority),
                    due_date=task.due_date,
                    completed_at=_timestamp(task.completed_at),
                    created_at=task_created_at,
                    updated_at=task_updated_at,
                ),
                created_at=task_created_at,
                updated_at=task_updated_at,
                source_key=str(task.id),
                metadata={"legacyId": int(task.id)},
            )
        )
    return tuple(sources)


def prepare_writing_source_catalog(
    *,
    session: Any,
    db: Session,
    staged_drafts: Iterable[Any] = (),
    staged_notes: Iterable[Any] = (),
    supplemental_folders: Iterable[Any] = (),
    supplemental_objects: Iterable[WritingSourceBody] = (),
) -> Any:
    from anima_server.services.corefs.diary_migration import (
        DiaryMigrationError,
        DiaryMigrationResult,
        _write_checkpoint,
        migration_opaque_id,
        read_prepared_writing_snapshot,
    )

    staged_drafts = tuple(staged_drafts)
    staged_notes = tuple(staged_notes)
    supplemental_folders = tuple(supplemental_folders)
    supplemental_objects = tuple(supplemental_objects)
    native = session.corefs_session
    keys = session.corefs_keys
    if native is None or keys is None:
        raise DiaryMigrationError("Diary migration requires an unlocked CoreFS session.")

    for _attempt in range(_MAX_SOURCE_RETRIES):
        inventory = build_writing_source_inventory(
            session=session,
            db=db,
            staged_drafts=staged_drafts,
            staged_notes=staged_notes,
            supplemental_folders=supplemental_folders,
            supplemental_objects=supplemental_objects,
        )
        active = _preparation_status_or_none(native, keys)
        if active is None and _inventory_matches_current(session=session, inventory=inventory):
            generation, catalog_hash = _required_head(inventory)
            staged_id, staged_revision = _staged_result(
                staged_drafts,
                read_prepared_writing_snapshot(session=session).objects,
            )
            _write_checkpoint(
                user_id=session.user_id,
                generation=generation,
                catalog_hash=catalog_hash,
                source_counts=inventory.source_counts,
                source_hash=inventory.source_digest,
                source_mutation_generation=inventory.source_generation,
                completion_token=_completion_token(staged_drafts),
            )
            return DiaryMigrationResult(
                generation=generation,
                catalog_hash=catalog_hash,
                published=False,
                source_counts=inventory.source_counts,
                source_hash=inventory.source_digest,
                stable_id=staged_id,
                revision=staged_revision,
            )

        if active is not None and active.get("state") == "ready":
            if (
                int(active.get("sourceMutationGeneration", -1)) != inventory.source_generation
                or active.get("sourceInventorySha256") != inventory.source_digest
            ):
                if _reconcile_ready_source_drift(native, keys, active) == "unpublished":
                    _abandon(native, keys, active)
                continue
            receipt = _finalize_under_fence(
                session=session,
                db=db,
                inventory=inventory,
                status=active,
                staged_drafts=staged_drafts,
                staged_notes=staged_notes,
                supplemental_folders=supplemental_folders,
                supplemental_objects=supplemental_objects,
                recovery=True,
            )
            return _result_from_receipt(
                session=session,
                inventory=inventory,
                receipt=receipt,
                staged_drafts=staged_drafts,
                published=True,
            )

        begin_request = {
            "scope": _SOURCE_SCOPE,
            "expectedValidationGeneration": (
                inventory.expected_head[0] if inventory.expected_head is not None else None
            ),
            "expectedValidationCatalogSha256": (
                inventory.expected_head[1] if inventory.expected_head is not None else None
            ),
            "sourceOwnerId": migration_opaque_id("pcf004-source-owner", str(session.user_id)),
            "sourceSchemaVersion": _SOURCE_SCHEMA_VERSION,
            "sourceMutationGeneration": inventory.source_generation,
            "sourceInventorySha256": inventory.source_digest,
        }
        try:
            status = dict(
                native.preparation_begin_or_resume_v1(keys, _canonical_json(begin_request))
            )
        except Exception as exc:
            if _is_native_conflict(exc):
                active = _preparation_status_or_none(native, keys)
                if active is not None:
                    _abandon(native, keys, active)
                    continue
            raise

        prepared = _reconcile(native, keys)
        desired = {item.stable_id: item for item in inventory.objects}
        if any(
            object_id not in desired or not _prepared_metadata_matches(desired[object_id], metadata)
            for object_id, metadata in prepared.items()
        ):
            _abandon(native, keys, status)
            continue

        identities: dict[str, dict[str, object]] = {
            object_id: _identity(metadata) for object_id, metadata in prepared.items()
        }
        # Reconciliation has already authenticated the durable descriptors.
        # Filter them out before entering the body iterator so restart does not
        # decrypt and re-encode a potentially multi-gigabyte completed prefix.
        pending_inventory = replace(
            inventory,
            objects=tuple(
                descriptor
                for descriptor in inventory.objects
                if descriptor.stable_id not in identities
            ),
        )
        source_objects = iter_writing_source_objects(
            session=session,
            db=db,
            inventory=pending_inventory,
            staged_drafts=staged_drafts,
            staged_notes=staged_notes,
            supplemental_objects=supplemental_objects,
        )
        while True:
            try:
                produced = next(source_objects)
            except StopIteration:
                break
            outcome = dict(
                native.preparation_prepare_object_v1(
                    keys,
                    _canonical_json(
                        {
                            "expected": _cas(status),
                            "object": produced.descriptor.native_object(),
                        }
                    ),
                    produced.body,
                )
            )
            status = dict(outcome["status"])
            summary = dict(outcome["prepared"])
            identities[produced.descriptor.stable_id] = _identity(summary)
            del produced

        refreshed = build_writing_source_inventory(
            session=session,
            db=db,
            staged_drafts=staged_drafts,
            staged_notes=staged_notes,
            supplemental_folders=supplemental_folders,
            supplemental_objects=supplemental_objects,
        )
        if not _same_source(inventory, refreshed):
            _abandon(native, keys, status)
            continue
        inventory = refreshed
        if status.get("state") != "ready":
            status = dict(
                native.preparation_seal_v1(
                    keys,
                    _canonical_json(
                        {
                            "expected": _cas(status),
                            "sourceMutationGeneration": inventory.source_generation,
                            "sourceInventorySha256": inventory.source_digest,
                            "folders": [_folder_wire(item) for item in inventory.folders],
                            "objects": [identities[item.stable_id] for item in inventory.objects],
                        }
                    ),
                )
            )

        receipt = _finalize_under_fence(
            session=session,
            db=db,
            inventory=inventory,
            status=status,
            staged_drafts=staged_drafts,
            staged_notes=staged_notes,
            supplemental_folders=supplemental_folders,
            supplemental_objects=supplemental_objects,
            recovery=False,
        )
        return _result_from_receipt(
            session=session,
            inventory=inventory,
            receipt=receipt,
            staged_drafts=staged_drafts,
            published=True,
        )

    raise DiaryMigrationError("Writing source kept changing during bounded preparation.")


def _finalize_under_fence(
    *,
    session: Any,
    db: Session,
    inventory: WritingSourceInventory,
    status: dict[str, object],
    staged_drafts: tuple[Any, ...],
    staged_notes: tuple[Any, ...],
    supplemental_folders: tuple[Any, ...],
    supplemental_objects: tuple[WritingSourceBody, ...],
    recovery: bool,
) -> dict[str, object]:
    from anima_server.services.corefs.diary_migration import DiaryMigrationError

    with begin_writing_source_fence(db) as fenced:
        fenced_inventory = build_writing_source_inventory(
            session=session,
            db=fenced,
            staged_drafts=staged_drafts,
            staged_notes=staged_notes,
            supplemental_folders=supplemental_folders,
            supplemental_objects=supplemental_objects,
        )
        if not _same_source(inventory, fenced_inventory):
            raise DiaryMigrationError("Writing source changed before final publication.")
        receipt = dict(
            session.corefs_session.preparation_finalize_v1(
                session.corefs_keys,
                _canonical_json(
                    {
                        "preparationId": status["preparationId"],
                        "expected": _cas(status),
                        "sourceMutationGeneration": inventory.source_generation,
                        "sourceInventorySha256": inventory.source_digest,
                    }
                ),
            )
        )
        generation = receipt.get("validationGeneration")
        catalog_hash = receipt.get("validationCatalogSha256")
        if not isinstance(generation, int) or not isinstance(catalog_hash, str):
            raise DiaryMigrationError("Native preparation completion receipt is incomplete.")
        head = session.corefs_session.validation_snapshot(session.corefs_keys)
        if head != {"generation": generation, "catalogHash": catalog_hash}:
            raise DiaryMigrationError("Published validation head does not match its receipt.")
        if not recovery:
            _verify_published_inventory(session=session, inventory=inventory)
    return receipt


def _result_from_receipt(
    *,
    session: Any,
    inventory: WritingSourceInventory,
    receipt: dict[str, object],
    staged_drafts: tuple[Any, ...],
    published: bool,
) -> Any:
    from anima_server.services.corefs.diary_migration import (
        DiaryMigrationError,
        DiaryMigrationResult,
        _write_checkpoint,
        read_prepared_writing_snapshot,
    )

    generation = receipt.get("validationGeneration")
    catalog_hash = receipt.get("validationCatalogSha256")
    if not isinstance(generation, int) or not isinstance(catalog_hash, str):
        raise DiaryMigrationError("Native preparation completion receipt is incomplete.")
    prepared = read_prepared_writing_snapshot(session=session).objects
    staged_id, staged_revision = _staged_result(staged_drafts, prepared)
    _write_checkpoint(
        user_id=session.user_id,
        generation=generation,
        catalog_hash=catalog_hash,
        source_counts=inventory.source_counts,
        source_hash=inventory.source_digest,
        source_mutation_generation=inventory.source_generation,
        completion_token=_completion_token(staged_drafts),
    )
    return DiaryMigrationResult(
        generation=generation,
        catalog_hash=catalog_hash,
        published=published,
        source_counts=inventory.source_counts,
        source_hash=inventory.source_digest,
        stable_id=staged_id,
        revision=staged_revision,
    )


def _attachment_metadata_by_entry(
    db: Session, *, user_id: int
) -> dict[int, list[_AttachmentMetadata]]:
    from anima_server.models import DiaryAttachment
    from anima_server.services.data_crypto import df

    rows = db.execute(
        select(
            DiaryAttachment.id,
            DiaryAttachment.entry_id,
            DiaryAttachment.kind,
            DiaryAttachment.mime_type,
            DiaryAttachment.size_bytes,
            DiaryAttachment.storage_path,
            DiaryAttachment.sha256,
            DiaryAttachment.original_filename,
            DiaryAttachment.caption,
            DiaryAttachment.created_at,
        )
        .where(DiaryAttachment.user_id == user_id)
        .order_by(DiaryAttachment.created_at, DiaryAttachment.id)
    ).all()
    result: dict[int, list[_AttachmentMetadata]] = {}
    for row in rows:
        item = _AttachmentMetadata(
            id=int(row.id),
            entry_id=int(row.entry_id),
            kind=str(row.kind),
            mime_type=str(row.mime_type),
            size_bytes=int(row.size_bytes),
            storage_path=str(row.storage_path),
            sha256=str(row.sha256),
            filename=df(
                user_id,
                row.original_filename,
                table="diary_attachments",
                field="original_filename",
            )
            or None,
            caption=df(
                user_id,
                row.caption,
                table="diary_attachments",
                field="caption",
            )
            or None,
            created_at=_timestamp(row.created_at),
        )
        result.setdefault(item.entry_id, []).append(item)
    for attachments in result.values():
        # Preserve the inventory pass's canonical ordering even if a database
        # driver or test double does not honor the statement ordering.
        attachments.sort(key=lambda item: (item.created_at or "", item.id))
    return result


def _encode_entry(
    *,
    user_id: int,
    row: Any,
    attachments: tuple[_AttachmentMetadata, ...],
    media_reference_factory: Callable[[str, bytes, str], str],
) -> tuple[bytes, str, tuple[str, ...]]:
    from anima_server.services.corefs.diary_migration import migration_opaque_id
    from anima_server.services.data_crypto import df

    source_body = df(user_id, row.body, table="diary_entries", field="body")
    attachment_uris = tuple(
        f"corefs://object/{migration_opaque_id('diary-attachment', str(item.id))}"
        for item in attachments
    )
    inline_ids: list[str] = []

    def reference_inline(mime_type: str, data: bytes, digest: str) -> str:
        uri = media_reference_factory(mime_type, data, digest)
        inline_ids.append(uri.rsplit("/", 1)[-1])
        return uri

    cover_uri = (
        f"corefs://object/{migration_opaque_id('diary-attachment', str(row.cover_attachment_id))}"
        if row.cover_attachment_id is not None
        else None
    )
    body = encode_diary_document(
        stable_id=migration_opaque_id("diary-entry", str(row.id)),
        entry_date=row.entry_date,
        title=df(user_id, row.title, table="diary_entries", field="title") or None,
        mood=df(user_id, row.mood, table="diary_entries", field="mood") or None,
        folder_id=(
            migration_opaque_id("diary-folder", str(row.folder_id))
            if row.folder_id is not None
            else None
        ),
        html=source_body,
        cover_uri=cover_uri,
        attachment_uris=attachment_uris,
        media_reference_factory=reference_inline,
        legacy_plain_text=not bool(re.match(r"\s*<", source_body)),
        legacy_id=row.id,
        legacy_folder_id=row.folder_id,
        source=row.source,
        created_at=_timestamp(row.created_at),
        updated_at=_timestamp(row.updated_at),
        attachment_metadata=tuple(
            {
                "legacyId": item.id,
                "stableId": migration_opaque_id("diary-attachment", str(item.id)),
                "kind": item.kind,
                "mimeType": item.mime_type,
                "filename": item.filename,
                "caption": item.caption,
                "sha256": item.sha256,
                "createdAt": item.created_at,
            }
            for item in attachments
        ),
    )
    references = tuple(
        dict.fromkeys((*(uri.rsplit("/", 1)[-1] for uri in attachment_uris), *inline_ids))
    )
    return body, source_body, references


def _prepared_descriptor(
    item: Any,
    *,
    revision: int,
    references: tuple[str, ...] = (),
    source_character_count: int | None = None,
) -> WritingSourceObjectDescriptor:
    return WritingSourceObjectDescriptor(
        stable_id=item.stable_id,
        parent_id=item.parent_id,
        name=item.name,
        kind=item.kind,
        content_type=item.content_type,
        body_encoding=item.body_encoding,
        body_length=item.body_length,
        content_sha256=item.content_hash,
        source_fingerprint_sha256=item.content_hash,
        created_at=item.created_at,
        updated_at=item.updated_at,
        revision=revision,
        source_character_count=source_character_count,
        references=references,
        metadata=dict(item.metadata),
        body_source="prepared",
        source_key=item.stable_id,
        prepared_path=item.path,
    )


def _source_inventory_hash(
    *,
    user_id: int,
    source_generation: int,
    folders: tuple[Any, ...],
    objects: tuple[WritingSourceObjectDescriptor, ...],
    counts: dict[str, int],
) -> str:
    return _json_hash(
        {
            "schemaVersion": _SOURCE_SCHEMA_VERSION,
            "userId": user_id,
            "sourceMutationGeneration": source_generation,
            "counts": counts,
            "folders": [_folder_wire(item) for item in folders],
            "objects": [
                {
                    key: value
                    for key, value in item.native_object().items()
                    if key not in {"revision", "objectKeyEpoch"}
                }
                for item in objects
            ],
        }
    )


def _folder_wire(item: Any) -> dict[str, object]:
    return {
        "stableId": item.stable_id,
        "parentId": item.parent_id,
        "name": item.name,
        "role": item.role,
        "policy": item.policy,
        "metadata": item.metadata,
    }


def _preparation_status_or_none(native: Any, keys: Any) -> dict[str, object] | None:
    try:
        return dict(native.preparation_status_v1(keys))
    except Exception as exc:
        message = str(exc).lower()
        if message.strip() == "no active preparation exists":
            return None
        raise


def _reconcile(native: Any, keys: Any) -> dict[str, dict[str, object]]:
    cursor: int | None = None
    items: dict[str, dict[str, object]] = {}
    while True:
        status = dict(
            native.preparation_status_v1(
                keys,
                _canonical_json(
                    {
                        "cursorPosition": cursor,
                        "maxItems": _RECONCILIATION_ITEMS,
                        "maxBytes": _RECONCILIATION_BYTES,
                        "expected": [],
                    }
                ),
            )
        )
        page = dict(status["reconciliation"])
        for raw in page.get("items", []):
            metadata = dict(raw)
            items[str(metadata["objectId"])] = metadata
        next_cursor = page.get("nextCursorPosition")
        if next_cursor is None:
            return items
        cursor = int(next_cursor)


def _prepared_metadata_matches(
    descriptor: WritingSourceObjectDescriptor, metadata: dict[str, object]
) -> bool:
    expected = descriptor.native_object()
    pairs = {
        "objectId": "objectId",
        "revision": "revision",
        "objectKeyEpoch": "objectKeyEpoch",
        "kind": "kind",
        "parentId": "parentId",
        "name": "name",
        "contentType": "contentType",
        "bodyEncoding": "bodyEncoding",
        "bodyLength": "bodyLength",
        "contentSha256": "contentSha256",
        "createdAt": "createdAt",
        "updatedAt": "updatedAt",
        "sourceCharacterCount": "sourceCharacterCount",
        "references": "references",
        "policy": "policy",
        "stableRole": "stableRole",
        "graphMetadata": "graphMetadata",
        "sourceFingerprintSha256": "sourceFingerprintSha256",
        "converterFormatVersion": "converterFormatVersion",
    }
    return all(
        metadata.get(actual) == expected[expected_key] for actual, expected_key in pairs.items()
    )


def _identity(value: dict[str, object]) -> dict[str, object]:
    return {
        "objectId": value["objectId"],
        "revision": value["revision"],
        "contentSha256": value["contentSha256"],
        "preparationOrdinal": value["preparationOrdinal"],
    }


def _cas(status: dict[str, object]) -> dict[str, object]:
    return {
        "pointerSha256": status["pointerSha256"],
        "snapshotSequence": status["snapshotSequence"],
    }


def _abandon(native: Any, keys: Any, status: dict[str, object]) -> None:
    native.preparation_abandon_v1(
        keys,
        _canonical_json(
            {
                "preparationId": status["preparationId"],
                "expected": _cas(status),
            }
        ),
    )


def _ready_validation_head(status: dict[str, object], *, intended: bool) -> tuple[int, str] | None:
    from anima_server.services.corefs.diary_migration import DiaryMigrationError

    prefix = "intended" if intended else "expected"
    generation = status.get(f"{prefix}ValidationGeneration")
    catalog_hash = status.get(f"{prefix}ValidationCatalogSha256")
    if generation is None and catalog_hash is None:
        return None
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or not isinstance(catalog_hash, str)
        or _SHA256_HEX.fullmatch(catalog_hash) is None
    ):
        raise DiaryMigrationError("Ready preparation validation-head metadata is invalid.")
    return generation, catalog_hash


def _reconcile_ready_source_drift(
    native: Any, keys: Any, status: dict[str, object]
) -> Literal["recovered", "unpublished"]:
    """Resolve source drift without abandoning a catalog that already published."""
    from anima_server.services.corefs.diary_migration import DiaryMigrationError

    intended = _ready_validation_head(status, intended=True)
    if intended is None:
        raise DiaryMigrationError("Ready preparation has no intended validation head.")
    expected = _ready_validation_head(status, intended=False)
    try:
        head = native.validation_snapshot(keys)
    except ValueError as exc:
        if str(exc) != "CoreFS validation snapshot is missing":
            raise DiaryMigrationError("CoreFS validation head could not be opened.") from exc
        current: tuple[int, str] | None = None
    else:
        try:
            current = (int(head["generation"]), str(head["catalogHash"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise DiaryMigrationError("CoreFS validation head is incomplete.") from exc

    if current == expected:
        return "unpublished"
    if current != intended:
        raise DiaryMigrationError("Current validation head conflicts with the ready preparation.")

    receipt = dict(
        native.preparation_finalize_v1(
            keys,
            _canonical_json(
                {
                    "preparationId": status["preparationId"],
                    "expected": _cas(status),
                    "sourceMutationGeneration": status["sourceMutationGeneration"],
                    "sourceInventorySha256": status["sourceInventorySha256"],
                }
            ),
        )
    )
    if (
        receipt.get("validationGeneration") != intended[0]
        or receipt.get("validationCatalogSha256") != intended[1]
    ):
        raise DiaryMigrationError("Recovered preparation receipt does not match publication.")
    return "recovered"


def _verify_published_inventory(*, session: Any, inventory: WritingSourceInventory) -> None:
    from anima_server.services.corefs.diary_migration import (
        DiaryMigrationError,
        read_prepared_writing_snapshot,
    )

    snapshot = read_prepared_writing_snapshot(session=session)
    expected = {
        item.stable_id: (
            item.revision,
            item.content_sha256,
            item.parent_id,
            item.name,
            item.kind,
            item.content_type,
            item.body_encoding,
            item.body_length,
        )
        for item in inventory.objects
    }
    actual = {
        item.stable_id: (
            item.revision,
            item.content_hash,
            item.parent_id,
            item.name,
            item.kind,
            item.content_type,
            item.body_encoding,
            item.body_length,
        )
        for item in snapshot.objects
    }
    if actual != expected:
        raise DiaryMigrationError("Published writing inventory did not verify.")


def _inventory_matches_current(*, session: Any, inventory: WritingSourceInventory) -> bool:
    """Compare every durable PCF-004 field available after publication.

    Preparation-only values such as source fingerprints and source character
    counts are validation inputs, not fields retained by the final catalog.
    PCF-004 references and object policies are deterministic from the body and
    fixed migration policy respectively. The authenticated envelope and
    catalog fields below therefore form the complete durable no-op identity.
    """
    from anima_server.services.corefs.diary_migration import (
        read_prepared_writing_body,
        read_prepared_writing_snapshot,
    )

    if inventory.expected_head is None:
        return False
    snapshot = read_prepared_writing_snapshot(session=session)
    desired_objects = {
        item.stable_id: (
            item.content_sha256,
            item.parent_id,
            item.name,
            item.kind,
            item.content_type,
            item.body_encoding,
            item.body_length,
            item.created_at,
            item.updated_at,
            item.metadata,
        )
        for item in inventory.objects
    }
    current_objects = {
        item.stable_id: (
            item.content_hash,
            item.parent_id,
            item.name,
            item.kind,
            item.content_type,
            item.body_encoding,
            item.body_length,
            item.created_at,
            item.updated_at,
            item.metadata,
        )
        for item in snapshot.objects
    }
    desired_folders = {
        item.stable_id: (item.parent_id, item.name, item.role) for item in inventory.folders
    }
    current_folders = {
        item.stable_id: (item.parent_id, item.name, item.role) for item in snapshot.folders
    }
    if desired_objects != current_objects or desired_folders != current_folders:
        return False

    current_by_id = {item.stable_id: item for item in snapshot.objects}
    for descriptor in inventory.objects:
        # Exact no-op acceptance includes authenticated, bounded envelope/body
        # verification; catalog metadata alone cannot prove object-file integrity.
        body = read_prepared_writing_body(session=session, item=current_by_id[descriptor.stable_id])
        del body
    return True


def _required_head(inventory: WritingSourceInventory) -> tuple[int, str]:
    if inventory.expected_head is None:
        raise RuntimeError("Writing inventory does not have a validation head.")
    return inventory.expected_head


def _staged_result(
    staged_drafts: tuple[Any, ...], prepared: Iterable[Any]
) -> tuple[str | None, int | None]:
    if not staged_drafts:
        return None, None
    from anima_server.services.corefs.diary_migration import migration_opaque_id

    staged = staged_drafts[-1]
    stable_id = staged.stable_id or migration_opaque_id("diary-draft", staged.id)
    revision = next((item.revision for item in prepared if item.stable_id == stable_id), None)
    return stable_id, revision


def _completion_token(staged_drafts: tuple[Any, ...]) -> dict[str, object] | None:
    if not staged_drafts:
        return None
    staged = staged_drafts[-1]
    if staged.client_revision is None or staged.content_sha256 is None:
        return None
    return {
        "draftId": staged.id,
        "clientRevision": staged.client_revision,
        "contentSha256": staged.content_sha256,
    }


def _same_source(first: WritingSourceInventory, second: WritingSourceInventory) -> bool:
    return (
        first.source_generation == second.source_generation
        and first.source_digest == second.source_digest
        and first.expected_head == second.expected_head
    )


def _is_native_conflict(exc: Exception) -> bool:
    return type(exc).__name__ in {
        "CorefsPreparationConflictError",
        "CorefsPreparationSourceFenceError",
    }


def _source_character_count(metadata: dict[str, Any], body: str) -> int:
    value = metadata.get("sourceCharacterCount")
    if value is None:
        return len(body)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Prepared writing source character count is invalid.")
    return value


def _json_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _entry_time(entry_date: str) -> str:
    return f"{entry_date}T00:00:00Z"


def _timestamp(value: Any | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
