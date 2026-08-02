from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from anima_server.services.corefs.formats import (
    DIARY_CONTENT_TYPE,
    DRAFT_CONTENT_TYPE,
    NOTE_CONTENT_TYPE,
    encode_diary_document,
    encode_draft_document,
    encode_note_document,
)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
logger = logging.getLogger(__name__)


def _native_folder_policy(value: str) -> str:
    """Lower legacy access into the validation converter's safe policy set."""
    normalized = value.strip().lower()
    if normalized == "inherit":
        return "inherit"
    # Native PCF-004 validation has no read-only representation. Explicit
    # deny, lowered access, and unrecognized legacy values therefore fail
    # closed instead of silently inheriting write access.
    return "deny"


def migration_opaque_id(domain: str, source_key: str | bytes) -> str:
    """Return the native converter's deterministic domain-separated ID."""
    source = source_key.encode() if isinstance(source_key, str) else source_key
    if not domain or not source:
        raise ValueError("Migration ID domain and source key are required.")
    try:
        import anima_core  # type: ignore[import-not-found]

        native = getattr(anima_core, "corefs_migration_id_v1", None)
        if native is not None:
            return str(native(domain, source))
    except ImportError:
        pass
    digest = hashlib.sha256(
        b"anima-corefs-migration-opaque-id-v1\0"
        + len(domain).to_bytes(8, "big")
        + domain.encode()
        + len(source).to_bytes(8, "big")
        + source
    ).digest()[:16]
    bits = int.from_bytes(digest, "big")
    return "".join(_CROCKFORD[(bits >> (125 - index * 5)) & 31] for index in range(26))


@dataclass(frozen=True, slots=True)
class LegacyDiaryFolder:
    id: int
    name: str
    parent_id: int | None
    order: int
    created_at: str | None = None
    policy: str = "inherit"


@dataclass(frozen=True, slots=True)
class LegacyDiaryAttachment:
    id: int
    entry_id: int
    kind: str
    mime_type: str
    data: bytes
    sha256: str
    filename: str | None
    caption: str | None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyDiaryEntry:
    id: int
    entry_date: str
    title: str | None
    body: str
    body_is_html: bool
    mood: str | None
    folder_id: int | None
    cover_attachment_id: int | None
    attachments: tuple[LegacyDiaryAttachment, ...]
    source: str = "user"
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyDiaryDraft:
    id: str
    target_entry_id: int | None
    body: str
    content_type: str
    updated_at: str
    stable_id: str | None = None
    metadata: dict[str, Any] | None = None
    target_stable_id: str | None = None
    created_at: str | None = None
    native_metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LegacyNote:
    id: str
    title: str | None
    body: str
    content_type: str
    updated_at: str
    stable_id: str | None = None
    created_at: str | None = None
    native_metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class InactiveFolder:
    stable_id: str
    parent_id: str | None
    name: str
    order: int
    role: str | None
    owner: str
    agent_access: str
    policy: str
    children: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InactiveObject:
    stable_id: str
    parent_id: str
    name: str
    kind: str
    content_type: str
    content: bytes
    content_hash: str
    source_hash: str
    body_encoding: str
    created_at: str
    updated_at: str
    expected_revision: int | None = None
    references: tuple[str, ...] = ()
    policy: str = "inherit"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InactiveWritingCatalog:
    user_id: int
    folders: tuple[InactiveFolder, ...]
    objects: tuple[InactiveObject, ...]
    catalog_hash: str

    def folder(self, stable_id: str) -> InactiveFolder:
        return next(folder for folder in self.folders if folder.stable_id == stable_id)

    def folder_for_role(self, role: str) -> InactiveFolder:
        matches = [folder for folder in self.folders if folder.role == role]
        if len(matches) != 1:
            raise ValueError(f"Inactive catalog must bind {role} exactly once.")
        return matches[0]

    def object(self, stable_id: str) -> InactiveObject:
        return next(item for item in self.objects if item.stable_id == stable_id)

    def publish(self, publisher: Callable[[InactiveWritingCatalog], None]) -> None:
        """Hand one immutable snapshot to the native atomic publication boundary."""
        publisher(self)

    def publish_native(
        self,
        *,
        corefs_session: Any,
        keys: object,
        expected_head: tuple[int, str] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "initialize": expected_head is None,
            "folders": [
                {
                    "stableId": item.stable_id,
                    "parentId": item.parent_id,
                    "name": item.name,
                    "role": item.role,
                    "policy": item.policy,
                    "metadata": item.metadata,
                }
                for item in self.folders
            ],
            "objects": [
                {
                    "stableId": item.stable_id,
                    "parentId": item.parent_id,
                    "name": item.name,
                    "kind": item.kind,
                    "contentType": item.content_type,
                    "bodyEncoding": item.body_encoding,
                    "contentBase64": base64.b64encode(item.content).decode(),
                    "createdAt": item.created_at,
                    "updatedAt": item.updated_at,
                    "expectedRevision": item.expected_revision,
                    "references": list(item.references),
                    "policy": item.policy,
                    "metadata": item.metadata,
                }
                for item in self.objects
            ],
        }
        if expected_head is not None:
            payload["initialize"] = False
            payload["expectedGeneration"] = expected_head[0]
            payload["expectedCatalogHash"] = expected_head[1]
        result = corefs_session.validation_batch_v1(
            keys, json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
        return dict(result)

    def with_expected_revisions(
        self,
        revisions: dict[str, int],
    ) -> InactiveWritingCatalog:
        """Bind current native revisions before an exact-head rerun."""
        return replace(
            self,
            objects=tuple(
                replace(item, expected_revision=revisions.get(item.stable_id))
                for item in self.objects
            ),
        )


def build_inactive_diary_catalog(
    *,
    user_id: int,
    folders: Iterable[LegacyDiaryFolder],
    entries: Iterable[LegacyDiaryEntry],
    drafts: Iterable[LegacyDiaryDraft] = (),
    notes: Iterable[LegacyNote] = (),
    preserved_folders: Iterable[InactiveFolder] = (),
) -> InactiveWritingCatalog:
    """Build deterministic validation-catalog input without changing active authority."""
    legacy_folders = tuple(sorted(folders, key=lambda item: (item.order, item.id)))
    legacy_entries = tuple(sorted(entries, key=lambda item: item.id))
    legacy_drafts = tuple(sorted(drafts, key=lambda item: item.id))
    legacy_notes = tuple(sorted(notes, key=lambda item: item.id))
    folder_ids = {folder.id for folder in legacy_folders}
    if len(folder_ids) != len(legacy_folders):
        raise ValueError("Legacy diary folder IDs must be unique.")
    for folder in legacy_folders:
        if folder.parent_id is not None and folder.parent_id not in folder_ids:
            raise ValueError("Legacy diary folder parent is missing.")

    preserved = tuple(preserved_folders)
    preserved_roles = {item.role: item for item in preserved if item.role is not None}
    journal_preserved = preserved_roles.get("core.journal")
    notes_preserved = preserved_roles.get("core.notes")
    core_root_id = migration_opaque_id("core-folder", "root")
    journal_id = (
        journal_preserved.stable_id
        if journal_preserved is not None
        else migration_opaque_id("core-folder-role", "core.journal")
    )
    notes_id = (
        notes_preserved.stable_id
        if notes_preserved is not None
        else migration_opaque_id("core-folder-role", "core.notes")
    )
    preserved_root = next((item for item in preserved if item.parent_id is None), None)
    if preserved_root is not None:
        core_root_id = preserved_root.stable_id
    converted_folders: list[InactiveFolder] = [
        InactiveFolder(
            stable_id=core_root_id,
            parent_id=None,
            name=preserved_root.name if preserved_root is not None else "Core",
            order=0,
            role=None,
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
        InactiveFolder(
            stable_id=journal_id,
            parent_id=(
                journal_preserved.parent_id
                if journal_preserved is not None
                else core_root_id
            ),
            name=journal_preserved.name if journal_preserved is not None else "Journal",
            order=0,
            role="core.journal",
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
        InactiveFolder(
            stable_id=notes_id,
            parent_id=(notes_preserved.parent_id if notes_preserved is not None else core_root_id),
            name=notes_preserved.name if notes_preserved is not None else "Notes",
            order=1,
            role="core.notes",
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
    ]
    for folder in legacy_folders:
        native_policy = _native_folder_policy(folder.policy)
        converted_folders.append(
            InactiveFolder(
                stable_id=migration_opaque_id("diary-folder", str(folder.id)),
                parent_id=(
                    migration_opaque_id("diary-folder", str(folder.parent_id))
                    if folder.parent_id is not None
                    else journal_id
                ),
                name=folder.name,
                order=folder.order,
                role=None,
                owner="user",
                agent_access=("write" if native_policy == "inherit" else "none"),
                policy=native_policy,
                metadata={
                    "legacyId": folder.id,
                    "order": folder.order,
                    "policy": folder.policy,
                    "createdAt": folder.created_at,
                },
            )
        )

    objects: list[InactiveObject] = []
    object_ids: set[str] = set()

    def add_object(item: InactiveObject) -> None:
        if item.stable_id in object_ids:
            return
        object_ids.add(item.stable_id)
        objects.append(item)

    for entry in legacy_entries:
        entry_parent_id = (
            migration_opaque_id("diary-folder", str(entry.folder_id))
            if entry.folder_id is not None
            else journal_id
        )
        attachment_uris: list[str] = []
        inline_reference_ids: list[str] = []
        attachments = tuple(sorted(entry.attachments, key=lambda item: item.id))
        attachment_ids = {attachment.id for attachment in attachments}
        if (
            entry.cover_attachment_id is not None
            and entry.cover_attachment_id not in attachment_ids
        ):
            raise ValueError("Legacy diary cover must belong to the same entry.")
        for attachment in attachments:
            actual_hash = hashlib.sha256(attachment.data).hexdigest()
            if actual_hash != attachment.sha256:
                raise ValueError("Legacy diary attachment hash mismatch.")
            stable_id = migration_opaque_id("diary-attachment", str(attachment.id))
            add_object(
                _object(
                    stable_id=stable_id,
                    parent_id=entry_parent_id,
                    name=attachment.filename or stable_id,
                    kind="attachment",
                    content_type=attachment.mime_type,
                    content=attachment.data,
                    source_hash=attachment.sha256,
                    body_encoding="binary",
                    created_at=attachment.created_at
                    or entry.created_at
                    or f"{entry.entry_date}T00:00:00Z",
                    updated_at=attachment.created_at
                    or entry.updated_at
                    or f"{entry.entry_date}T00:00:00Z",
                    metadata={
                        "legacyId": attachment.id,
                        "legacyEntryId": attachment.entry_id,
                        "kind": attachment.kind,
                        "caption": attachment.caption,
                        "filename": attachment.filename,
                        "sha256": attachment.sha256,
                        "createdAt": attachment.created_at,
                    },
                )
            )
            attachment_uris.append(f"corefs://object/{stable_id}")

        def reference_inline(
            mime_type: str,
            data: bytes,
            digest: str,
            parent_id: str = entry_parent_id,
            entry_date: str = entry.entry_date,
            entry_created_at: str | None = entry.created_at,
            entry_updated_at: str | None = entry.updated_at,
            reference_ids: list[str] = inline_reference_ids,
        ) -> str:
            stable_id = migration_opaque_id("diary-inline-media", digest)
            add_object(
                _object(
                    stable_id=stable_id,
                    parent_id=parent_id,
                    name=f"inline-{digest[:12]}",
                    kind="attachment",
                    content_type=mime_type,
                    content=data,
                    source_hash=digest,
                    body_encoding="binary",
                    created_at=entry_created_at or f"{entry_date}T00:00:00Z",
                    updated_at=entry_updated_at or f"{entry_date}T00:00:00Z",
                )
            )
            reference_ids.append(stable_id)
            return f"corefs://object/{stable_id}"

        cover_uri = (
            f"corefs://object/{migration_opaque_id('diary-attachment', str(entry.cover_attachment_id))}"
            if entry.cover_attachment_id is not None
            else None
        )
        content = encode_diary_document(
            stable_id=migration_opaque_id("diary-entry", str(entry.id)),
            entry_date=entry.entry_date,
            title=entry.title,
            mood=entry.mood,
            folder_id=(
                migration_opaque_id("diary-folder", str(entry.folder_id))
                if entry.folder_id is not None
                else None
            ),
            html=entry.body,
            cover_uri=cover_uri,
            attachment_uris=tuple(attachment_uris),
            media_reference_factory=reference_inline,
            legacy_plain_text=not entry.body_is_html,
            legacy_id=entry.id,
            legacy_folder_id=entry.folder_id,
            source=entry.source,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            attachment_metadata=tuple(
                {
                    "legacyId": attachment.id,
                    "stableId": migration_opaque_id("diary-attachment", str(attachment.id)),
                    "kind": attachment.kind,
                    "mimeType": attachment.mime_type,
                    "filename": attachment.filename,
                    "caption": attachment.caption,
                    "sha256": attachment.sha256,
                    "createdAt": attachment.created_at,
                }
                for attachment in attachments
            ),
        )
        add_object(
            _object(
                stable_id=migration_opaque_id("diary-entry", str(entry.id)),
                parent_id=entry_parent_id,
                name=f"{entry.entry_date}-{entry.id}.diary.json",
                kind="diary",
                content_type=DIARY_CONTENT_TYPE,
                content=content,
                source_hash=hashlib.sha256(entry.body.encode()).hexdigest(),
                body_encoding="utf-8",
                created_at=entry.created_at or f"{entry.entry_date}T00:00:00Z",
                updated_at=entry.updated_at or f"{entry.entry_date}T00:00:00Z",
                references=(
                    *(uri.rsplit("/", 1)[-1] for uri in attachment_uris),
                    *inline_reference_ids,
                ),
                metadata={
                    "legacyId": entry.id,
                    "legacyFolderId": entry.folder_id,
                    "source": entry.source,
                    "createdAt": entry.created_at,
                    "updatedAt": entry.updated_at,
                },
            )
        )

    for draft in legacy_drafts:
        stable_id = draft.stable_id or migration_opaque_id("diary-draft", draft.id)
        target_id = (
            draft.target_stable_id
            or (
                migration_opaque_id("diary-entry", str(draft.target_entry_id))
                if draft.target_entry_id is not None
                else None
            )
        )
        add_object(
            _object(
                stable_id=stable_id,
                parent_id=journal_id,
                name=f"{stable_id}.draft.json",
                kind="draft",
                content_type=DRAFT_CONTENT_TYPE,
                content=encode_draft_document(
                    stable_id=stable_id,
                    target_id=target_id,
                    content_type=draft.content_type,
                    body=draft.body,
                    metadata=draft.metadata,
                ),
                source_hash=hashlib.sha256(draft.body.encode()).hexdigest(),
                body_encoding="utf-8",
                created_at=draft.created_at or draft.updated_at,
                updated_at=draft.updated_at,
                references=(target_id,) if target_id is not None else (),
                metadata=draft.native_metadata,
            )
        )

    for note in legacy_notes:
        stable_id = note.stable_id or migration_opaque_id("note", note.id)
        add_object(
            _object(
                stable_id=stable_id,
                parent_id=notes_id,
                name=f"{stable_id}.note.json",
                kind="note",
                content_type=NOTE_CONTENT_TYPE,
                content=encode_note_document(
                    stable_id=stable_id,
                    title=note.title,
                    content_type=note.content_type,
                    body=note.body,
                ),
                source_hash=hashlib.sha256(note.body.encode()).hexdigest(),
                body_encoding="utf-8",
                created_at=note.created_at or note.updated_at,
                updated_at=note.updated_at,
                metadata=note.native_metadata,
            )
        )

    all_child_pairs = [
        *((folder.parent_id, folder.stable_id) for folder in converted_folders),
        *((item.parent_id, item.stable_id) for item in objects),
    ]
    converted_folders = [
        replace(
            folder,
            children=tuple(
                child_id for parent_id, child_id in all_child_pairs if parent_id == folder.stable_id
            ),
        )
        for folder in converted_folders
    ]
    folders_tuple = tuple(converted_folders)
    objects_tuple = tuple(sorted(objects, key=lambda item: item.stable_id))
    catalog_hash = _catalog_hash(user_id, folders_tuple, objects_tuple)
    return InactiveWritingCatalog(
        user_id=user_id,
        folders=folders_tuple,
        objects=objects_tuple,
        catalog_hash=catalog_hash,
    )


def _object(
    *,
    stable_id: str,
    parent_id: str,
    name: str,
    kind: str,
    content_type: str,
    content: bytes,
    source_hash: str,
    body_encoding: str,
    created_at: str,
    updated_at: str,
    expected_revision: int | None = None,
    references: tuple[str, ...] = (),
    policy: str = "inherit",
    metadata: dict[str, Any] | None = None,
) -> InactiveObject:
    return InactiveObject(
        stable_id=stable_id,
        parent_id=parent_id,
        name=name,
        kind=kind,
        content_type=content_type,
        content=content,
        content_hash=hashlib.sha256(content).hexdigest(),
        source_hash=source_hash,
        body_encoding=body_encoding,
        created_at=created_at,
        updated_at=updated_at,
        expected_revision=expected_revision,
        references=references,
        policy=policy,
        metadata=metadata or {},
    )


def _catalog_hash(
    user_id: int,
    folders: tuple[InactiveFolder, ...],
    objects: tuple[InactiveObject, ...],
) -> str:
    payload = {
        "userId": user_id,
        "folders": [
            {
                "stableId": item.stable_id,
                "parentId": item.parent_id,
                "name": item.name,
                "order": item.order,
                "role": item.role,
                "owner": item.owner,
                "agentAccess": item.agent_access,
                "policy": item.policy,
                "children": list(item.children),
                "metadata": item.metadata,
            }
            for item in folders
        ],
        "objects": [
            {
                "stableId": item.stable_id,
                "parentId": item.parent_id,
                "name": item.name,
                "kind": item.kind,
                "contentType": item.content_type,
                "contentHash": item.content_hash,
                "sourceHash": item.source_hash,
                "bodyEncoding": item.body_encoding,
                "createdAt": item.created_at,
                "updatedAt": item.updated_at,
                "expectedRevision": item.expected_revision,
                "references": list(item.references),
                "policy": item.policy,
                "metadata": item.metadata,
            }
            for item in objects
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class DiaryMigrationError(RuntimeError):
    """Raised when the inactive writing catalog cannot be proven equivalent."""


@dataclass(frozen=True, slots=True)
class PreparedWritingObject:
    stable_id: str
    path: str
    kind: str
    revision: int
    content_hash: str
    content: bytes
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedWritingFolder:
    stable_id: str
    parent_id: str | None
    path: str
    name: str
    role: str | None


@dataclass(frozen=True, slots=True)
class PreparedWritingSnapshot:
    folders: tuple[PreparedWritingFolder, ...]
    objects: tuple[PreparedWritingObject, ...]


@dataclass(frozen=True, slots=True)
class DiaryMigrationResult:
    generation: int
    catalog_hash: str
    published: bool
    source_counts: dict[str, int]
    source_hash: str
    stable_id: str | None = None
    revision: int | None = None


def resolve_prepared_role(*, session: Any, role: str) -> dict[str, object]:
    """Resolve an inactive stable role only through an authorized unlock session."""
    if session.corefs_session is None or session.corefs_keys is None:
        raise DiaryMigrationError("CoreFS prepared access requires an unlocked session.")
    value = session.corefs_session.resolve_validation_role_v1(session.corefs_keys, role)
    if not isinstance(value, dict):
        raise DiaryMigrationError(f"Prepared role {role} is unavailable.")
    return dict(value)


def read_prepared_writing_objects(*, session: Any) -> tuple[PreparedWritingObject, ...]:
    """Read authenticated inactive writing objects without changing route authority."""
    return read_prepared_writing_snapshot(session=session).objects


def read_prepared_writing_snapshot(*, session: Any) -> PreparedWritingSnapshot:
    """Read authenticated inactive folders and writing objects at one generation."""
    from anima_server.services.corefs import logical

    if session.corefs_session is None or session.corefs_keys is None:
        raise DiaryMigrationError("CoreFS prepared access requires an unlocked session.")
    selected = logical.select_validation_snapshot(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    values: list[PreparedWritingObject] = []
    root = _wire_result(
        logical.stat_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=selected,
            path="",
        ),
        selected.generation,
    )
    root_id = root.get("stableId")
    if root.get("kind") != "directory" or not isinstance(root_id, str):
        raise DiaryMigrationError("Invalid prepared CoreFS root entry.")
    folders: list[PreparedWritingFolder] = [
        PreparedWritingFolder(
            stable_id=root_id,
            parent_id=None,
            path="",
            name="Core",
            role=None,
        )
    ]
    cursor: str | None = None
    while True:
        raw = logical.walk_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=selected,
            root="",
            cursor_after=cursor,
            page_size=100,
            include_directories=True,
        )
        result = _wire_result(raw, selected.generation)
        entries = result.get("entries")
        if not isinstance(entries, list):
            raise DiaryMigrationError("Invalid prepared CoreFS walk response.")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            stable_id = entry.get("stableId")
            if entry.get("kind") == "directory":
                parent_id = entry.get("parentId")
                role = entry.get("role")
                if (
                    not isinstance(path, str)
                    or not isinstance(stable_id, str)
                    or (parent_id is not None and not isinstance(parent_id, str))
                    or (role is not None and not isinstance(role, str))
                ):
                    raise DiaryMigrationError("Invalid prepared CoreFS directory entry.")
                folders.append(
                    PreparedWritingFolder(
                        stable_id=stable_id,
                        parent_id=parent_id,
                        path=path,
                        name=path.rsplit("/", 1)[-1] if path else "Core",
                        role=role,
                    )
                )
                continue
            if entry.get("kind") != "file":
                continue
            kind = entry.get("objectKind")
            revision = entry.get("revision")
            content_hash = entry.get("contentHash")
            created_at = entry.get("createdAt")
            updated_at = entry.get("updatedAt")
            metadata = entry.get("metadata")
            if (
                not isinstance(path, str)
                or not isinstance(stable_id, str)
                or not isinstance(kind, str)
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or not isinstance(content_hash, str)
                or not isinstance(created_at, str)
                or not isinstance(updated_at, str)
                or not isinstance(metadata, dict)
            ):
                raise DiaryMigrationError("Invalid prepared CoreFS walk entry.")
            content = _read_prepared_bytes(
                session=session,
                selected=selected,
                path=path,
            )
            if hashlib.sha256(content).hexdigest() != content_hash:
                raise DiaryMigrationError("Prepared object hash did not verify.")
            values.append(
                PreparedWritingObject(
                    stable_id=stable_id,
                    path=path,
                    kind=kind,
                    revision=revision,
                    content_hash=content_hash,
                    content=content,
                    created_at=created_at,
                    updated_at=updated_at,
                    metadata=dict(metadata),
                )
            )
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, dict) or not isinstance(next_cursor.get("after"), str):
            raise DiaryMigrationError("Invalid prepared CoreFS walk cursor.")
        cursor = str(next_cursor["after"])
    return PreparedWritingSnapshot(folders=tuple(folders), objects=tuple(values))


def prepare_diary_validation_catalog(
    *,
    session: Any,
    db: Session,
    staged_drafts: Iterable[LegacyDiaryDraft] = (),
    staged_notes: Iterable[LegacyNote] = (),
) -> DiaryMigrationResult:
    """Convert SQLCipher diary state into the inactive authenticated catalog.

    Legacy SQLCipher remains authoritative until PCF-008. This routine only
    advances the native validation head and records a non-secret checkpoint.
    """
    from anima_server.models import DiaryAttachment, DiaryEntry, DiaryFolder
    from anima_server.services.corefs.formats import decode_draft_document, decode_note_document
    from anima_server.services.data_crypto import df
    from anima_server.services.diary import read_attachment_blob

    if session.corefs_session is None or session.corefs_keys is None:
        raise DiaryMigrationError("Diary migration requires an unlocked CoreFS session.")

    staged_drafts = tuple(staged_drafts)
    staged_notes = tuple(staged_notes)
    current: tuple[PreparedWritingObject, ...]
    current_folders: tuple[PreparedWritingFolder, ...]
    try:
        prepared_snapshot = read_prepared_writing_snapshot(session=session)
        current = prepared_snapshot.objects
        current_folders = prepared_snapshot.folders
        head_value = session.corefs_session.validation_snapshot(session.corefs_keys)
        expected_head = (int(head_value["generation"]), str(head_value["catalogHash"]))
    except ValueError:
        current = ()
        current_folders = ()
        expected_head = None
    allowed_kinds = {"diary", "attachment", "draft", "note"}
    unknown = sorted({item.kind for item in current if item.kind not in allowed_kinds})
    if unknown:
        raise DiaryMigrationError(
            "Writing validation batch cannot replace unrelated prepared families: "
            + ", ".join(unknown)
        )

    existing_drafts: dict[str, LegacyDiaryDraft] = {}
    existing_notes: dict[str, LegacyNote] = {}
    for item in current:
        if item.kind == "draft":
            decoded = decode_draft_document(item.content)
            existing_drafts[decoded.stable_id] = LegacyDiaryDraft(
                id=decoded.stable_id,
                target_entry_id=None,
                body=decoded.body,
                content_type=decoded.content_type,
                updated_at=item.updated_at,
                stable_id=decoded.stable_id,
                metadata=decoded.metadata,
                target_stable_id=decoded.target_id,
                created_at=item.created_at,
                native_metadata=item.metadata,
            )
        elif item.kind == "note":
            decoded_note = decode_note_document(item.content)
            existing_notes[decoded_note.stable_id] = LegacyNote(
                id=decoded_note.stable_id,
                title=decoded_note.title,
                body=decoded_note.body,
                content_type=decoded_note.content_type,
                updated_at=item.updated_at,
                stable_id=decoded_note.stable_id,
                created_at=item.created_at,
                native_metadata=item.metadata,
            )
    for draft in staged_drafts:
        existing_drafts[migration_opaque_id("diary-draft", draft.id)] = draft
    for note in staged_notes:
        existing_notes[note.stable_id or migration_opaque_id("note", note.id)] = note

    folder_rows = list(
        db.scalars(
            select(DiaryFolder)
            .where(DiaryFolder.user_id == session.user_id)
            .order_by(DiaryFolder.created_at, DiaryFolder.id)
        ).all()
    )
    attachment_rows = list(
        db.scalars(
            select(DiaryAttachment)
            .where(DiaryAttachment.user_id == session.user_id)
            .order_by(DiaryAttachment.created_at, DiaryAttachment.id)
        ).all()
    )
    attachments_by_entry: dict[int, list[DiaryAttachment]] = {}
    legacy_attachments: dict[int, LegacyDiaryAttachment] = {}
    for row in attachment_rows:
        blob = read_attachment_blob(user_id=session.user_id, attachment=row)
        legacy = LegacyDiaryAttachment(
            id=row.id,
            entry_id=row.entry_id,
            kind=row.kind,
            mime_type=row.mime_type,
            data=blob.data,
            sha256=row.sha256,
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
        legacy_attachments[row.id] = legacy
        attachments_by_entry.setdefault(row.entry_id, []).append(row)

    entry_rows = list(
        db.scalars(
            select(DiaryEntry)
            .options(selectinload(DiaryEntry.attachments))
            .where(DiaryEntry.user_id == session.user_id)
            .order_by(DiaryEntry.id)
        ).all()
    )
    legacy_entries: list[LegacyDiaryEntry] = []
    for row in entry_rows:
        body = df(session.user_id, row.body, table="diary_entries", field="body")
        legacy_entries.append(
            LegacyDiaryEntry(
                id=row.id,
                entry_date=row.entry_date,
                title=df(session.user_id, row.title, table="diary_entries", field="title") or None,
                body=body,
                body_is_html=bool(re.match(r"\s*<", body)),
                mood=df(session.user_id, row.mood, table="diary_entries", field="mood") or None,
                folder_id=row.folder_id,
                cover_attachment_id=row.cover_attachment_id,
                attachments=tuple(legacy_attachments[item.id] for item in row.attachments),
                source=row.source,
                created_at=_timestamp(row.created_at),
                updated_at=_timestamp(row.updated_at),
            )
        )

    legacy_folders = tuple(
        LegacyDiaryFolder(
            id=row.id,
            name=df(session.user_id, row.name, table="diary_folders", field="name") or row.name,
            parent_id=None,
            order=index,
            created_at=_timestamp(row.created_at),
        )
        for index, row in enumerate(folder_rows)
    )
    catalog = build_inactive_diary_catalog(
        user_id=session.user_id,
        folders=legacy_folders,
        entries=legacy_entries,
        drafts=tuple(existing_drafts.values()),
        notes=tuple(existing_notes.values()),
        preserved_folders=tuple(
            InactiveFolder(
                stable_id=item.stable_id,
                parent_id=item.parent_id,
                name=item.name,
                order=0,
                role=item.role,
                owner="user",
                agent_access="write",
                policy="user-write" if item.role is not None else "inherit",
            )
            for item in current_folders
        ),
    )
    revisions = {item.stable_id: item.revision for item in current}
    catalog = catalog.with_expected_revisions(revisions)
    result = catalog.publish_native(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
        expected_head=expected_head,
    )
    verified = read_prepared_writing_objects(session=session)
    expected_hashes = {item.stable_id: item.content_hash for item in catalog.objects}
    actual_hashes = {item.stable_id: item.content_hash for item in verified}
    if expected_hashes != actual_hashes:
        raise DiaryMigrationError("Prepared diary object count/hash verification failed.")
    _verify_api_parity(legacy_entries, verified)
    source_counts = {
        "folders": len(legacy_folders),
        "entries": len(legacy_entries),
        "attachments": len(attachment_rows),
        "drafts": len(existing_drafts),
        "notes": len(existing_notes),
    }
    source_hash = _source_checkpoint_hash(catalog, source_counts)
    _write_checkpoint(
        user_id=session.user_id,
        generation=int(result["generation"]),
        catalog_hash=str(result["catalogHash"]),
        source_counts=source_counts,
        source_hash=source_hash,
    )
    staged_id = (
        staged_drafts[-1].stable_id or migration_opaque_id("diary-draft", staged_drafts[-1].id)
        if staged_drafts
        else None
    )
    staged_revision = next(
        (item.revision for item in verified if item.stable_id == staged_id),
        None,
    )
    return DiaryMigrationResult(
        generation=int(result["generation"]),
        catalog_hash=str(result["catalogHash"]),
        published=bool(result["published"]),
        source_counts=source_counts,
        source_hash=source_hash,
        stable_id=staged_id,
        revision=staged_revision,
    )


def _read_prepared_bytes(*, session: Any, selected: Any, path: str) -> bytes:
    from anima_server.services.corefs import logical

    chunks: list[bytes] = []
    offset = 0
    while True:
        raw = logical.read_chunk_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=selected,
            path=path,
            offset=offset,
            max_bytes=64 * 1024,
        )
        if raw is None:
            break
        result = _wire_result(raw, selected.generation)
        encoded = result.get("bytesBase64")
        if not isinstance(encoded, str) or result.get("offset") != offset:
            raise DiaryMigrationError("Invalid prepared CoreFS read response.")
        chunk = base64.b64decode(encoded, validate=True)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _wire_result(raw: bytes, generation: int) -> dict[str, object]:
    try:
        payload = json.loads(raw)
        result = payload["result"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise DiaryMigrationError("Invalid prepared CoreFS response.") from exc
    if payload.get("version") != "corefs-logical-v1" or not isinstance(result, dict):
        raise DiaryMigrationError("Invalid prepared CoreFS response.")
    if result.get("generation") not in {None, generation}:
        raise DiaryMigrationError("Prepared CoreFS generation changed during verification.")
    return result


def _verify_api_parity(
    legacy_entries: Iterable[LegacyDiaryEntry],
    prepared: Iterable[PreparedWritingObject],
) -> None:
    from anima_server.services.corefs.formats import decode_diary_document

    by_id = {item.stable_id: item for item in prepared if item.kind == "diary"}
    for legacy in legacy_entries:
        item = by_id.get(migration_opaque_id("diary-entry", str(legacy.id)))
        if item is None:
            raise DiaryMigrationError("Prepared diary API parity entry is missing.")
        decoded = decode_diary_document(item.content)
        if (
            decoded.legacy_id != legacy.id
            or decoded.legacy_folder_id != legacy.folder_id
            or decoded.title != legacy.title
            or decoded.mood != legacy.mood
            or decoded.source != legacy.source
            or decoded.created_at != legacy.created_at
            or decoded.updated_at != legacy.updated_at
        ):
            raise DiaryMigrationError("Prepared diary API parity metadata mismatch.")


def _source_checkpoint_hash(
    catalog: InactiveWritingCatalog,
    counts: dict[str, int],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {"catalogHash": catalog.catalog_hash, "counts": counts},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _write_checkpoint(
    *,
    user_id: int,
    generation: int,
    catalog_hash: str,
    source_counts: dict[str, int],
    source_hash: str,
) -> None:
    from anima_server.services.core import update_core_manifest

    def update(manifest: dict[str, object]) -> None:
        checkpoints = manifest.setdefault("migration_checkpoints", {})
        if not isinstance(checkpoints, dict):
            raise DiaryMigrationError("Core migration checkpoint registry is invalid.")
        checkpoints[f"pcf004:{user_id}"] = {
            "state": "verified-inactive",
            "generation": generation,
            "catalogHash": catalog_hash,
            "sourceCounts": source_counts,
            "sourceHash": source_hash,
            "verifiedAt": _now_iso(),
            "authoritative": False,
        }

    update_core_manifest(update)


def record_diary_migration_failure(*, user_id: int, error: Exception) -> None:
    """Journal a private-text-free failed lifecycle attempt for safe retry."""
    from anima_server.services.core import update_core_manifest

    def update(manifest: dict[str, object]) -> None:
        checkpoints = manifest.setdefault("migration_checkpoints", {})
        if not isinstance(checkpoints, dict):
            return
        checkpoints[f"pcf004:{user_id}"] = {
            "state": "retry-required",
            "errorCode": type(error).__name__,
            "errorDigest": hashlib.sha256(str(error).encode()).hexdigest(),
            "attemptedAt": _now_iso(),
            "authoritative": False,
        }

    update_core_manifest(update)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
