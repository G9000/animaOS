from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from anima_server.services.corefs.formats import (
    DIARY_CONTENT_TYPE,
    DRAFT_CONTENT_TYPE,
    NOTE_CONTENT_TYPE,
    encode_diary_document,
    encode_draft_document,
    encode_note_document,
)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


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


@dataclass(frozen=True, slots=True)
class LegacyDiaryDraft:
    id: str
    target_entry_id: int | None
    body: str
    content_type: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LegacyNote:
    id: str
    title: str | None
    body: str
    content_type: str
    updated_at: str


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


def build_inactive_diary_catalog(
    *,
    user_id: int,
    folders: Iterable[LegacyDiaryFolder],
    entries: Iterable[LegacyDiaryEntry],
    drafts: Iterable[LegacyDiaryDraft] = (),
    notes: Iterable[LegacyNote] = (),
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

    core_root_id = migration_opaque_id("core-folder", "root")
    journal_id = migration_opaque_id("core-folder-role", "core.journal")
    notes_id = migration_opaque_id("core-folder-role", "core.notes")
    converted_folders: list[InactiveFolder] = [
        InactiveFolder(
            stable_id=core_root_id,
            parent_id=None,
            name="Core",
            order=0,
            role=None,
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
        InactiveFolder(
            stable_id=journal_id,
            parent_id=core_root_id,
            name="Journal",
            order=0,
            role="core.journal",
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
        InactiveFolder(
            stable_id=notes_id,
            parent_id=core_root_id,
            name="Notes",
            order=1,
            role="core.notes",
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
    ]
    for folder in legacy_folders:
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
                agent_access="write",
                policy="inherit",
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
                    created_at=f"{entry.entry_date}T00:00:00Z",
                    updated_at=f"{entry.entry_date}T00:00:00Z",
                )
            )
            attachment_uris.append(f"corefs://object/{stable_id}")

        def reference_inline(
            mime_type: str,
            data: bytes,
            digest: str,
            parent_id: str = entry_parent_id,
            entry_date: str = entry.entry_date,
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
                    created_at=f"{entry_date}T00:00:00Z",
                    updated_at=f"{entry_date}T00:00:00Z",
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
                created_at=f"{entry.entry_date}T00:00:00Z",
                updated_at=f"{entry.entry_date}T00:00:00Z",
                references=(
                    *(uri.rsplit("/", 1)[-1] for uri in attachment_uris),
                    *inline_reference_ids,
                ),
            )
        )

    for draft in legacy_drafts:
        stable_id = migration_opaque_id("diary-draft", draft.id)
        target_id = (
            migration_opaque_id("diary-entry", str(draft.target_entry_id))
            if draft.target_entry_id is not None
            else None
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
                ),
                source_hash=hashlib.sha256(draft.body.encode()).hexdigest(),
                body_encoding="utf-8",
                created_at=draft.updated_at,
                updated_at=draft.updated_at,
                references=(target_id,) if target_id is not None else (),
            )
        )

    for note in legacy_notes:
        stable_id = migration_opaque_id("note", note.id)
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
                created_at=note.updated_at,
                updated_at=note.updated_at,
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
            }
            for item in objects
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
