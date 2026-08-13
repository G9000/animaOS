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

from sqlalchemy.orm import Session

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

_MAX_PORTABLE_NAME_BYTES = 255


def _truncate_portable_name(value: str, suffix: str = "") -> str:
    """Bound a portable component without splitting a Unicode scalar."""
    available = _MAX_PORTABLE_NAME_BYTES - len(suffix.encode("utf-8"))
    if available <= 0:
        raise ValueError("Portable name suffix exceeds the CoreFS limit.")
    prefix = value
    while len(prefix.encode("utf-8")) > available:
        prefix = prefix[:-1]
    return f"{prefix}{suffix}"


def _portable_name_base(value: str, *, stable_id: str) -> str:
    """Encode a legacy display name into one valid deterministic CoreFS component."""
    try:
        import anima_core  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Native CoreFS migration component mapping is unavailable.") from exc
    native = getattr(anima_core, "corefs_migration_component_v1", None)
    if native is None:
        raise RuntimeError("Native CoreFS migration component mapping is unavailable.")
    return str(native(value, stable_id))


def _portable_catalog_names(
    folders: list[InactiveFolder],
    objects: list[InactiveObject],
) -> tuple[list[InactiveFolder], list[InactiveObject]]:
    """Normalize all components and deterministically disambiguate siblings."""
    entries = [
        ("folder", index, item.parent_id, item.stable_id, item.name)
        for index, item in enumerate(folders)
    ] + [
        ("object", index, item.parent_id, item.stable_id, item.name)
        for index, item in enumerate(objects)
    ]
    bases = {
        (kind, index): _portable_name_base(name, stable_id=stable_id)
        for kind, index, _parent_id, stable_id, name in entries
    }
    groups: dict[tuple[str | None, str], list[tuple[str, int, str]]] = {}
    for kind, index, parent_id, stable_id, _name in entries:
        groups.setdefault((parent_id, bases[(kind, index)]), []).append((kind, index, stable_id))
    names = dict(bases)
    for members in groups.values():
        if len(members) < 2:
            continue
        for kind, index, stable_id in members:
            names[(kind, index)] = _truncate_portable_name(bases[(kind, index)], f"~{stable_id}")

    # A legacy base may itself equal another member's disambiguated spelling.
    # In that rare case suffix every component with its globally unique stable
    # identity, which makes sibling uniqueness unconditional and deterministic.
    resulting = [
        (parent_id, names[(kind, index)]) for kind, index, parent_id, _stable_id, _name in entries
    ]
    if len(resulting) != len(set(resulting)):
        for kind, index, _parent_id, stable_id, _name in entries:
            names[(kind, index)] = _truncate_portable_name(bases[(kind, index)], f"~{stable_id}")

    return (
        [replace(item, name=names[("folder", index)]) for index, item in enumerate(folders)],
        [replace(item, name=names[("object", index)]) for index, item in enumerate(objects)],
    )


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
    client_revision: int | None = None
    content_sha256: str | None = None
    stable_id: str | None = None
    metadata: dict[str, Any] | None = None
    target_stable_id: str | None = None
    created_at: str | None = None
    native_metadata: dict[str, Any] | None = None
    source_character_count: int | None = None


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
    source_character_count: int | None = None


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
    source_character_count: int | None = None


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
    preserved_objects: Iterable[InactiveObject] = (),
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
    preserved_by_id = {item.stable_id: item for item in preserved_objects}
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
                journal_preserved.parent_id if journal_preserved is not None else core_root_id
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
                    "displayName": folder.name,
                    "originalName": folder.name,
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
                        "displayName": attachment.filename,
                        "originalName": attachment.filename,
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
                source_character_count=len(entry.body),
            )
        )

    for draft in legacy_drafts:
        stable_id = draft.stable_id or migration_opaque_id("diary-draft", draft.id)
        target_id = draft.target_stable_id or (
            migration_opaque_id("diary-entry", str(draft.target_entry_id))
            if draft.target_entry_id is not None
            else None
        )
        draft_inline_ids: list[str] = []
        existing_reference_ids = tuple(
            dict.fromkeys(re.findall(r"corefs://object/([0-7][0-9A-HJKMNP-TV-Z]{25})", draft.body))
        )
        for reference_id in existing_reference_ids:
            preserved_object = preserved_by_id.get(reference_id)
            if preserved_object is None:
                raise ValueError("Draft contains a dangling CoreFS attachment reference.")
            if preserved_object.kind != "attachment":
                raise ValueError("Draft contains a foreign CoreFS object reference.")
            add_object(preserved_object)

        def reference_draft_inline(
            mime_type: str,
            data: bytes,
            digest: str,
            created_at: str = draft.created_at or draft.updated_at,
            updated_at: str = draft.updated_at,
            reference_ids: list[str] = draft_inline_ids,
        ) -> str:
            media_id = migration_opaque_id("diary-inline-media", digest)
            add_object(
                _object(
                    stable_id=media_id,
                    parent_id=journal_id,
                    name=f"inline-{digest[:12]}",
                    kind="attachment",
                    content_type=mime_type,
                    content=data,
                    source_hash=digest,
                    body_encoding="binary",
                    created_at=created_at,
                    updated_at=updated_at,
                    metadata={"origin": "legacy-local-storage-draft"},
                )
            )
            reference_ids.append(media_id)
            return f"corefs://object/{media_id}"

        draft_content = encode_draft_document(
            stable_id=stable_id,
            target_id=target_id,
            content_type=draft.content_type,
            body=draft.body,
            metadata=draft.metadata,
            media_reference_factory=reference_draft_inline,
        )
        add_object(
            _object(
                stable_id=stable_id,
                parent_id=journal_id,
                name=f"{stable_id}.draft.json",
                kind="draft",
                content_type=DRAFT_CONTENT_TYPE,
                content=draft_content,
                source_hash=hashlib.sha256(draft.body.encode()).hexdigest(),
                body_encoding="utf-8",
                created_at=draft.created_at or draft.updated_at,
                updated_at=draft.updated_at,
                references=(
                    *((target_id,) if target_id is not None else ()),
                    *existing_reference_ids,
                    *draft_inline_ids,
                ),
                metadata=draft.native_metadata,
                source_character_count=(
                    draft.source_character_count
                    if draft.source_character_count is not None
                    else len(draft.body)
                ),
                persist_source_character_count=(
                    draft.native_metadata is None or "sourceCharacterCount" in draft.native_metadata
                ),
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
                source_character_count=(
                    note.source_character_count
                    if note.source_character_count is not None
                    else len(note.body)
                ),
                persist_source_character_count=(
                    note.native_metadata is None or "sourceCharacterCount" in note.native_metadata
                ),
            )
        )

    converted_folders, objects = _portable_catalog_names(converted_folders, objects)
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
    source_character_count: int | None = None,
    persist_source_character_count: bool = True,
) -> InactiveObject:
    authenticated_metadata = dict(metadata or {})
    if source_character_count is not None and persist_source_character_count:
        authenticated_metadata["sourceCharacterCount"] = source_character_count
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
        metadata=authenticated_metadata,
        source_character_count=source_character_count,
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
                "sourceCharacterCount": item.source_character_count,
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
    parent_id: str
    path: str
    name: str
    kind: str
    revision: int
    content_hash: str
    body_length: int
    created_at: str
    updated_at: str
    metadata: dict[str, Any]
    content_type: str
    body_encoding: str


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
            parent_id = entry.get("parentId")
            revision = entry.get("revision")
            content_hash = entry.get("contentHash")
            created_at = entry.get("createdAt")
            updated_at = entry.get("updatedAt")
            metadata = entry.get("metadata")
            if (
                not isinstance(path, str)
                or not isinstance(stable_id, str)
                or not isinstance(parent_id, str)
                or not isinstance(kind, str)
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or not isinstance(content_hash, str)
                or not isinstance(created_at, str)
                or not isinstance(updated_at, str)
                or not isinstance(metadata, dict)
            ):
                raise DiaryMigrationError("Invalid prepared CoreFS walk entry.")
            stat = _wire_result(
                logical.stat_v1(
                    corefs_session=session.corefs_session,
                    keys=session.corefs_keys,
                    selected=selected,
                    path=path,
                ),
                selected.generation,
            )
            content_type = stat.get("contentType")
            body_encoding = stat.get("bodyEncoding")
            body_length = stat.get("size")
            if (
                not isinstance(content_type, str)
                or body_encoding not in {"utf-8", "binary"}
                or isinstance(body_length, bool)
                or not isinstance(body_length, int)
                or body_length < 0
            ):
                raise DiaryMigrationError("Invalid prepared CoreFS envelope identity.")
            values.append(
                PreparedWritingObject(
                    stable_id=stable_id,
                    parent_id=parent_id,
                    path=path,
                    name=path.rsplit("/", 1)[-1],
                    kind=kind,
                    revision=revision,
                    content_hash=content_hash,
                    body_length=body_length,
                    created_at=created_at,
                    updated_at=updated_at,
                    metadata=dict(metadata),
                    content_type=content_type,
                    body_encoding=str(body_encoding),
                )
            )
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, dict) or not isinstance(next_cursor.get("after"), str):
            raise DiaryMigrationError("Invalid prepared CoreFS walk cursor.")
        cursor = str(next_cursor["after"])
    return PreparedWritingSnapshot(folders=tuple(folders), objects=tuple(values))


def read_prepared_writing_body(*, session: Any, item: PreparedWritingObject) -> bytes:
    """Read and verify exactly one inactive writing body."""
    from anima_server.services.corefs import logical

    if session.corefs_session is None or session.corefs_keys is None:
        raise DiaryMigrationError("CoreFS prepared access requires an unlocked session.")
    selected = logical.select_validation_snapshot(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    content = _read_prepared_bytes(session=session, selected=selected, path=item.path)
    if len(content) != item.body_length or hashlib.sha256(content).hexdigest() != item.content_hash:
        raise DiaryMigrationError("Prepared object body did not verify.")
    return content


def prepare_diary_validation_catalog(
    *,
    session: Any,
    db: Session,
    staged_drafts: Iterable[LegacyDiaryDraft] = (),
    staged_notes: Iterable[LegacyNote] = (),
) -> DiaryMigrationResult:
    """Stream legacy writing into resumable inactive CoreFS preparation."""
    from anima_server.services.corefs.writing_source import prepare_writing_source_catalog

    try:
        return prepare_writing_source_catalog(
            session=session,
            db=db,
            staged_drafts=staged_drafts,
            staged_notes=staged_notes,
        )
    except Exception as exc:
        record_diary_migration_failure(user_id=session.user_id, error=exc)
        if isinstance(exc, (DiaryMigrationError, ValueError)):
            raise
        raise DiaryMigrationError("CoreFS writing preparation failed safely.") from exc


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


def _write_checkpoint(
    *,
    user_id: int,
    generation: int,
    catalog_hash: str,
    source_counts: dict[str, int],
    source_hash: str,
    source_mutation_generation: int | None = None,
    completion_token: dict[str, object] | None = None,
) -> None:
    from anima_server.services.core import update_core_manifest

    def update(manifest: dict[str, object]) -> None:
        checkpoints = manifest.setdefault("migration_checkpoints", {})
        if not isinstance(checkpoints, dict):
            raise DiaryMigrationError("Core migration checkpoint registry is invalid.")
        checkpoint: dict[str, object] = {
            "state": "verified-inactive",
            "generation": generation,
            "catalogHash": catalog_hash,
            "sourceCounts": source_counts,
            "sourceHash": source_hash,
            "verifiedAt": _now_iso(),
            "authoritative": False,
        }
        if source_mutation_generation is not None:
            checkpoint["sourceMutationGeneration"] = source_mutation_generation
        if completion_token is not None:
            checkpoint["completionToken"] = completion_token
        checkpoints[f"pcf004:{user_id}"] = checkpoint
        if source_counts.get("conversationRoot") == 1:
            checkpoints[f"pcf005:{user_id}"] = {
                **checkpoint,
                "sourceCounts": {
                    "threads": source_counts.get("threads", 0),
                    "messageSegments": source_counts.get("messageSegments", 0),
                },
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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
