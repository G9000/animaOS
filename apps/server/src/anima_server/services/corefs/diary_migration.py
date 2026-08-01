from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from anima_server.services.corefs.formats import DIARY_CONTENT_TYPE, encode_diary_document


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
class InactiveFolder:
    stable_id: str
    parent_id: str | None
    name: str
    order: int
    role: str | None
    owner: str
    agent_access: str
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


def build_inactive_diary_catalog(
    *,
    user_id: int,
    folders: Iterable[LegacyDiaryFolder],
    entries: Iterable[LegacyDiaryEntry],
) -> InactiveWritingCatalog:
    """Build deterministic validation-catalog input without changing active authority."""
    legacy_folders = tuple(sorted(folders, key=lambda item: (item.order, item.id)))
    legacy_entries = tuple(sorted(entries, key=lambda item: item.id))
    folder_ids = {folder.id for folder in legacy_folders}
    if len(folder_ids) != len(legacy_folders):
        raise ValueError("Legacy diary folder IDs must be unique.")
    for folder in legacy_folders:
        if folder.parent_id is not None and folder.parent_id not in folder_ids:
            raise ValueError("Legacy diary folder parent is missing.")

    converted_folders: list[InactiveFolder] = [
        InactiveFolder(
            stable_id="core-journal",
            parent_id="core-root",
            name="Journal",
            order=0,
            role="core.journal",
            owner="user",
            agent_access="write",
        ),
        InactiveFolder(
            stable_id="core-notes",
            parent_id="core-root",
            name="Notes",
            order=1,
            role="core.notes",
            owner="user",
            agent_access="write",
        ),
    ]
    for folder in legacy_folders:
        converted_folders.append(
            InactiveFolder(
                stable_id=f"diary-folder-{folder.id}",
                parent_id=(
                    f"diary-folder-{folder.parent_id}"
                    if folder.parent_id is not None
                    else "core-journal"
                ),
                name=folder.name,
                order=folder.order,
                role=None,
                owner="user",
                agent_access="write",
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
            f"diary-folder-{entry.folder_id}"
            if entry.folder_id is not None
            else "core-journal"
        )
        attachment_uris: list[str] = []
        attachments = tuple(sorted(entry.attachments, key=lambda item: item.id))
        attachment_ids = {attachment.id for attachment in attachments}
        if entry.cover_attachment_id is not None and entry.cover_attachment_id not in attachment_ids:
            raise ValueError("Legacy diary cover must belong to the same entry.")
        for attachment in attachments:
            actual_hash = hashlib.sha256(attachment.data).hexdigest()
            if actual_hash != attachment.sha256:
                raise ValueError("Legacy diary attachment hash mismatch.")
            stable_id = f"diary-attachment-{attachment.id}"
            add_object(
                _object(
                    stable_id=stable_id,
                    parent_id=entry_parent_id,
                    name=attachment.filename or stable_id,
                    kind="binary",
                    content_type=attachment.mime_type,
                    content=attachment.data,
                    source_hash=attachment.sha256,
                )
            )
            attachment_uris.append(f"corefs://object/{stable_id}")

        def reference_inline(
            mime_type: str,
            data: bytes,
            digest: str,
            parent_id: str = entry_parent_id,
        ) -> str:
            stable_id = f"diary-inline-{digest}"
            add_object(
                _object(
                    stable_id=stable_id,
                    parent_id=parent_id,
                    name=f"inline-{digest[:12]}",
                    kind="binary",
                    content_type=mime_type,
                    content=data,
                    source_hash=digest,
                )
            )
            return f"corefs://object/{stable_id}"

        cover_uri = (
            f"corefs://object/diary-attachment-{entry.cover_attachment_id}"
            if entry.cover_attachment_id is not None
            else None
        )
        content = encode_diary_document(
            stable_id=f"diary-entry-{entry.id}",
            entry_date=entry.entry_date,
            title=entry.title,
            mood=entry.mood,
            folder_id=f"diary-folder-{entry.folder_id}" if entry.folder_id is not None else None,
            html=entry.body,
            cover_uri=cover_uri,
            attachment_uris=tuple(attachment_uris),
            media_reference_factory=reference_inline,
            legacy_plain_text=not entry.body_is_html,
        )
        add_object(
            _object(
                stable_id=f"diary-entry-{entry.id}",
                parent_id=entry_parent_id,
                name=f"{entry.entry_date}-{entry.id}.diary.json",
                kind="diary",
                content_type=DIARY_CONTENT_TYPE,
                content=content,
                source_hash=hashlib.sha256(entry.body.encode()).hexdigest(),
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
            }
            for item in objects
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
