"""Inactive CoreFS conversation migration and transcript merge."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.services.corefs.diary_migration import (
    InactiveFolder,
    migration_opaque_id,
)
from anima_server.services.corefs.messages import (
    MESSAGE_SEGMENT_CONTENT_TYPE,
    THREAD_CONTENT_TYPE,
    CanonicalMessageRecord,
    ConversationConflict,
    MessageEvent,
    ThreadDocument,
    encode_message_segments,
    encode_thread_document,
    merge_conversation_sources,
    message_segment_references,
)
from anima_server.services.corefs.writing_source import (
    WritingSourceBody,
    WritingSourceObjectDescriptor,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConversationThreadSource:
    source_id: str
    title: str | None
    status: str
    created_at: str
    updated_at: str
    last_message_at: str | None
    closed_at: str | None
    is_archived: bool


@dataclass(frozen=True, slots=True)
class ConversationShadowCatalog:
    folders: tuple[InactiveFolder, ...]
    objects: tuple[WritingSourceBody, ...]
    conflicts: tuple[ConversationConflict, ...]
    duplicate_count: int
    excluded_count: int
    thread_count: int
    message_count: int


@dataclass(frozen=True, slots=True)
class ArchivedConversationSources:
    threads: tuple[dict[str, object], ...]
    messages: tuple[dict[str, object], ...]
    conflicts: tuple[ConversationConflict, ...]


def build_conversation_shadow_catalog(
    *,
    user_id: int,
    active_threads: Iterable[object] = (),
    active_messages: Iterable[object] = (),
    archived_threads: Iterable[object] = (),
    archived_messages: Iterable[object] = (),
    legacy_threads: Iterable[object] = (),
    legacy_messages: Iterable[object] = (),
    attachment_resolver: Callable[[object], str | None] | None = None,
    attachment_objects: Iterable[WritingSourceBody] = (),
    preserved_conversations_folder: InactiveFolder | None = None,
    additional_conflicts: Iterable[ConversationConflict] = (),
) -> ConversationShadowCatalog:
    """Build one deterministic, non-authoritative conversation snapshot.

    Conflicting or unsafe rows remain in their legacy source and are represented
    by encrypted degraded-thread metadata. They are never silently selected.
    """
    del user_id  # IDs are source-stable and already scoped by the caller's Core.
    active_threads = tuple(active_threads)
    archived_threads = tuple(archived_threads)
    legacy_threads = tuple(legacy_threads)
    merge = merge_conversation_sources(
        active=active_messages,
        archived=archived_messages,
        legacy=legacy_messages,
        attachment_resolver=attachment_resolver,
    )
    conflicts = [*additional_conflicts, *merge.conflicts]
    thread_sources = _merge_thread_sources(
        active=active_threads,
        archived=archived_threads,
        legacy=legacy_threads,
        records=merge.records,
    )
    records_by_thread: dict[str, list[CanonicalMessageRecord]] = defaultdict(list)
    for record in merge.records:
        records_by_thread[record.thread_id].append(record)

    accepted_by_thread: dict[str, tuple[CanonicalMessageRecord, ...]] = {}
    for thread_id, records in records_by_thread.items():
        by_sequence: dict[int, list[CanonicalMessageRecord]] = defaultdict(list)
        for record in records:
            by_sequence[record.sequence].append(record)
        collided = {
            sequence: values for sequence, values in by_sequence.items() if len(values) > 1
        }
        if collided:
            rejected_ids = {
                record.message_id for values in collided.values() for record in values
            }
            for sequence, values in sorted(collided.items()):
                conflicts.append(
                    ConversationConflict(
                        reason="sequence_conflict",
                        source="merged",
                        identity=f"{thread_id}:{sequence}",
                        detail=(
                            "multiple canonical messages claim one logical sequence: "
                            + ",".join(sorted(record.message_id for record in values))
                        ),
                    )
                )
            records = [record for record in records if record.message_id not in rejected_ids]
        accepted_by_thread[thread_id] = tuple(
            sorted(records, key=lambda item: (item.sequence, item.message_id))
        )

    conflicts_by_thread: dict[str, list[ConversationConflict]] = defaultdict(list)
    global_conflicts: list[ConversationConflict] = []
    for conflict in conflicts:
        thread_id = _conflict_thread_id(conflict)
        if thread_id is None:
            global_conflicts.append(conflict)
        else:
            conflicts_by_thread[thread_id].append(conflict)

    root_id = migration_opaque_id("core-folder", "root")
    folder_id = migration_opaque_id("core-folder-role", "core.conversations")
    folder = preserved_conversations_folder or InactiveFolder(
        stable_id=folder_id,
        parent_id=root_id,
        name="Conversations",
        order=2,
        role="core.conversations",
        owner="shared",
        agent_access="manage",
        policy="shared-manage",
        metadata={"formatVersion": 1},
    )
    if (
        folder.role != "core.conversations"
        or folder.owner != "shared"
        or folder.agent_access != "manage"
        or folder.policy != "shared-manage"
    ):
        raise ValueError("Preserved conversations root violates the shared/manage contract.")

    all_thread_ids = set(thread_sources) | set(accepted_by_thread) | set(conflicts_by_thread)
    objects: list[WritingSourceBody] = list(attachment_objects)
    if any(item.descriptor.parent_id != folder.stable_id for item in objects):
        raise ValueError("Conversation attachment has the wrong parent folder.")
    message_count = 0
    for thread_id in sorted(all_thread_ids):
        source = thread_sources.get(thread_id) or _synthetic_thread_source(
            thread_id,
            accepted_by_thread.get(thread_id, ()),
        )
        records = accepted_by_thread.get(thread_id, ())
        events = tuple(_created_event(record) for record in records)
        segments = encode_message_segments(events)
        for segment in segments:
            objects.append(
                _body(
                    stable_id=segment.segment_id,
                    parent_id=folder.stable_id,
                    name=f"segment-{segment.index:08d}.jsonl",
                    kind="message-segment",
                    content_type=MESSAGE_SEGMENT_CONTENT_TYPE,
                    data=segment.data,
                    created_at=source.created_at,
                    updated_at=source.updated_at,
                    references=message_segment_references(segment),
                    metadata={
                        "threadId": thread_id,
                        "segmentOrdinal": segment.index,
                        "firstSequence": min(event.sequence for event in segment.events),
                        "lastSequence": max(event.sequence for event in segment.events),
                        "previousSegmentId": segment.previous_segment_id,
                        "previousSegmentSha256": segment.previous_segment_sha256,
                    },
                )
            )

        thread_conflicts = [*conflicts_by_thread.get(thread_id, ())]
        if global_conflicts:
            # Unknown-thread source failures stay durably visible without
            # inventing a plaintext quarantine file outside the encrypted Core.
            thread_conflicts.extend(global_conflicts)
            global_conflicts = []
        document = ThreadDocument(
            thread_id=thread_id,
            legacy_thread_id=_legacy_thread_id(source.source_id),
            title=source.title,
            status="degraded" if thread_conflicts else source.status,
            created_at=source.created_at,
            updated_at=source.updated_at,
            last_message_at=source.last_message_at,
            closed_at=source.closed_at,
            is_archived=source.is_archived,
            segment_ids=tuple(segment.segment_id for segment in segments),
            segment_sha256=tuple(segment.sha256 for segment in segments),
            segment_ranges=tuple(
                (
                    min(event.sequence for event in segment.events),
                    max(event.sequence for event in segment.events),
                )
                for segment in segments
            ),
            message_count=len(records),
            quarantine=tuple(thread_conflicts),
        )
        data = encode_thread_document(document)
        objects.append(
            _body(
                stable_id=thread_id,
                parent_id=folder.stable_id,
                name=f"thread-{thread_id}.json",
                kind="thread",
                content_type=THREAD_CONTENT_TYPE,
                data=data,
                created_at=source.created_at,
                updated_at=source.updated_at,
                references=document.segment_ids,
                metadata={
                    "threadStatus": document.status,
                    "quarantineCount": len(document.quarantine),
                },
            )
        )
        message_count += len(records)

    if global_conflicts:
        # A malformed source without any usable thread still gets an encrypted
        # synthetic degraded thread so the migration cannot appear complete.
        quarantine_id = migration_opaque_id("conversation-thread", "quarantine")
        source = _synthetic_thread_source(quarantine_id, ())
        document = ThreadDocument(
            thread_id=quarantine_id,
            legacy_thread_id=None,
            title="Migration quarantine",
            status="degraded",
            created_at=source.created_at,
            updated_at=source.updated_at,
            last_message_at=None,
            closed_at=None,
            is_archived=True,
            segment_ids=(),
            segment_sha256=(),
            segment_ranges=(),
            message_count=0,
            quarantine=tuple(global_conflicts),
        )
        data = encode_thread_document(document)
        objects.append(
            _body(
                stable_id=quarantine_id,
                parent_id=folder.stable_id,
                name=f"thread-{quarantine_id}.json",
                kind="thread",
                content_type=THREAD_CONTENT_TYPE,
                data=data,
                created_at=source.created_at,
                updated_at=source.updated_at,
                references=(),
                metadata={"threadStatus": "degraded", "quarantineCount": len(global_conflicts)},
            )
        )
        all_thread_ids.add(quarantine_id)

    return ConversationShadowCatalog(
        folders=(folder,),
        objects=tuple(objects),
        conflicts=tuple(conflicts),
        duplicate_count=merge.duplicate_count,
        excluded_count=merge.excluded_count,
        thread_count=len(all_thread_ids),
        message_count=message_count,
    )


def load_archived_conversation_sources(
    *,
    transcripts_dir: Path,
    user_id: int,
    dek: bytes | None,
) -> ArchivedConversationSources:
    """Decrypt legacy transcript sources without treating host paths as authority."""
    from anima_server.services.agent.transcript_archive import (
        decrypt_transcript,
        load_transcript_sidecar,
        resolve_transcript_path,
    )

    threads: list[dict[str, object]] = []
    messages: list[dict[str, object]] = []
    conflicts: list[ConversationConflict] = []
    if not transcripts_dir.exists():
        return ArchivedConversationSources((), (), ())
    for meta_path in sorted(transcripts_dir.glob("*.meta.json")):
        sidecar = load_transcript_sidecar(meta_path)
        raw_thread_id = sidecar.get("thread_id") if sidecar is not None else None
        identity = str(raw_thread_id) if raw_thread_id is not None else meta_path.name
        try:
            if sidecar is None or int(sidecar.get("user_id", -1)) != user_id:
                raise ValueError("transcript sidecar owner is invalid")
            thread_id = int(sidecar["thread_id"])
            path = resolve_transcript_path(meta_path)
            if path is None:
                raise ValueError("transcript body is missing")
            decoded = decrypt_transcript(path, dek=dek, thread_id=thread_id)
            for row in decoded:
                if not isinstance(row, dict):
                    raise ValueError("transcript message is invalid")
                normalized = dict(row)
                normalized.setdefault("thread_id", thread_id)
                messages.append(normalized)
            threads.append(
                {
                    "id": thread_id,
                    "title": None,
                    "status": "archived",
                    "created_at": _archive_created_at(sidecar, decoded),
                    "updated_at": str(sidecar.get("archived_at")),
                    "last_message_at": _archive_last_message_at(decoded),
                    "closed_at": str(sidecar.get("archived_at")),
                    "is_archived": True,
                }
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            conflicts.append(
                ConversationConflict(
                    reason="invalid_archive",
                    source="archived",
                    identity=identity,
                    detail=str(exc),
                )
            )
    return ArchivedConversationSources(
        tuple(threads),
        tuple(messages),
        tuple(conflicts),
    )


def collect_conversation_shadow_sources(
    *,
    soul_db: Session,
    runtime_db: Session,
    user_id: int,
    transcripts_dir: Path,
    dek: bytes | None,
    attachment_resolver: Callable[[object], str | None] | None = None,
    include_runtime_attachment_objects: bool = True,
) -> ConversationShadowCatalog:
    """Read all three legacy source families into one inactive snapshot."""
    from anima_server.models.agent_runtime import AgentMessage, AgentThread
    from anima_server.models.runtime import RuntimeMessage, RuntimeThread

    active_threads = tuple(
        runtime_db.scalars(
            select(RuntimeThread).where(RuntimeThread.user_id == user_id)
        ).all()
    )
    active_messages = tuple(
        runtime_db.scalars(
            select(RuntimeMessage)
            .where(RuntimeMessage.user_id == user_id)
            .order_by(RuntimeMessage.thread_id, RuntimeMessage.sequence_id)
        ).all()
    )
    legacy_threads = tuple(
        soul_db.scalars(select(AgentThread).where(AgentThread.user_id == user_id)).all()
    )
    legacy_thread_ids = [thread.id for thread in legacy_threads]
    legacy_messages = (
        tuple(
            soul_db.scalars(
                select(AgentMessage)
                .where(AgentMessage.thread_id.in_(legacy_thread_ids))
                .order_by(AgentMessage.thread_id, AgentMessage.sequence_id)
            ).all()
        )
        if legacy_thread_ids
        else ()
    )
    archived = load_archived_conversation_sources(
        transcripts_dir=transcripts_dir,
        user_id=user_id,
        dek=dek,
    )
    if include_runtime_attachment_objects:
        attachment_objects, resolved_attachment = _collect_runtime_message_attachments(
            runtime_db=runtime_db,
            user_id=user_id,
            messages=[*active_messages, *archived.messages, *legacy_messages],
        )
    else:
        if attachment_resolver is None:
            raise ValueError("External attachment inventory requires a resolver.")
        attachment_objects = ()

        def resolved_attachment(_value: object) -> str | None:
            return None

    def resolve_attachment(value: object) -> str | None:
        if attachment_resolver is not None:
            resolved = attachment_resolver(value)
            if resolved is not None:
                return resolved
        return resolved_attachment(value)

    return build_conversation_shadow_catalog(
        user_id=user_id,
        active_threads=active_threads,
        active_messages=active_messages,
        archived_threads=archived.threads,
        archived_messages=archived.messages,
        legacy_threads=legacy_threads,
        legacy_messages=legacy_messages,
        attachment_resolver=resolve_attachment,
        attachment_objects=attachment_objects,
        additional_conflicts=archived.conflicts,
    )


def prepare_conversation_validation_catalog(
    *,
    session: Any,
    soul_db: Session,
    runtime_db: Session,
    transcripts_dir: Path,
    attachment_resolver: Callable[[object], str | None] | None = None,
) -> tuple[Any, ConversationShadowCatalog]:
    """Merge conversations into the shared inactive writing preparation."""
    from anima_server.services.corefs.writing_source import prepare_writing_source_catalog

    shadow = collect_conversation_shadow_sources(
        soul_db=soul_db,
        runtime_db=runtime_db,
        user_id=session.user_id,
        transcripts_dir=transcripts_dir,
        dek=session.deks.get("conversations"),
        attachment_resolver=attachment_resolver,
    )
    result = prepare_writing_source_catalog(
        session=session,
        db=soul_db,
        supplemental_folders=shadow.folders,
        supplemental_objects=shadow.objects,
    )
    return result, shadow


def record_conversation_migration_failure(*, user_id: int, error: Exception) -> None:
    """Journal a private-text-free PCF-005 retry marker."""
    from anima_server.services.core import update_core_manifest

    def update(manifest: dict[str, object]) -> None:
        checkpoints = manifest.setdefault("migration_checkpoints", {})
        if not isinstance(checkpoints, dict):
            return
        checkpoints[f"pcf005:{user_id}"] = {
            "state": "retry-required",
            "errorCode": type(error).__name__,
            "errorDigest": hashlib.sha256(str(error).encode()).hexdigest(),
            "attemptedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "authoritative": False,
        }

    update_core_manifest(update)


def _merge_thread_sources(
    *,
    active: Iterable[object],
    archived: Iterable[object],
    legacy: Iterable[object],
    records: Iterable[CanonicalMessageRecord],
) -> dict[str, ConversationThreadSource]:
    result: dict[str, ConversationThreadSource] = {}
    for rows in (archived, legacy, active):
        for row in rows:
            source = _thread_source(row)
            result[migration_opaque_id("conversation-thread", source.source_id)] = source
    for record in records:
        result.setdefault(
            record.thread_id,
            _synthetic_thread_source(record.thread_id, (record,)),
        )
    return result


def _thread_source(value: object) -> ConversationThreadSource:
    raw_id = _source_value(value, "id", "thread_id", "threadId")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)) or not str(raw_id):
        raise ValueError("Conversation thread identity is invalid.")
    title = _source_value(value, "title")
    if title is not None and not isinstance(title, str):
        raise ValueError("Conversation thread title is invalid.")
    status = _source_value(value, "status")
    if status not in {"active", "closed", "archived", "deleted"}:
        status = "archived" if bool(_source_value(value, "is_archived", "isArchived")) else "closed"
    created_at = _timestamp(_source_value(value, "created_at", "createdAt"))
    updated_at = _timestamp(
        _source_value(value, "updated_at", "updatedAt") or created_at
    )
    last_raw = _source_value(value, "last_message_at", "lastMessageAt")
    closed_raw = _source_value(value, "closed_at", "closedAt")
    return ConversationThreadSource(
        source_id=str(raw_id),
        title=title,
        status=str(status),
        created_at=created_at,
        updated_at=updated_at,
        last_message_at=_timestamp(last_raw) if last_raw is not None else None,
        closed_at=_timestamp(closed_raw) if closed_raw is not None else None,
        is_archived=bool(_source_value(value, "is_archived", "isArchived")),
    )


def _synthetic_thread_source(
    thread_id: str,
    records: Iterable[CanonicalMessageRecord],
) -> ConversationThreadSource:
    values = tuple(records)
    timestamps = sorted(record.created_at for record in values)
    epoch = "1970-01-01T00:00:00.000000+00:00"
    return ConversationThreadSource(
        source_id=thread_id,
        title=None,
        status="archived",
        created_at=timestamps[0] if timestamps else epoch,
        updated_at=timestamps[-1] if timestamps else epoch,
        last_message_at=timestamps[-1] if timestamps else None,
        closed_at=timestamps[-1] if timestamps else None,
        is_archived=True,
    )


def _legacy_thread_id(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def _created_event(record: CanonicalMessageRecord) -> MessageEvent:
    return MessageEvent(
        event_id=migration_opaque_id("conversation-event", f"{record.message_id}:created:1"),
        message_id=record.message_id,
        legacy_message_id=_legacy_thread_id(record.stable_source_id)
        if record.stable_source_id is not None
        else None,
        thread_id=record.thread_id,
        sequence=record.sequence,
        kind="message.created",
        message_version=1,
        expected_prior_event_id=None,
        expected_prior_version=None,
        role=record.role,
        content=record.content,
        attachment_uris=record.attachment_uris,
        created_at=record.created_at,
    )


def _body(
    *,
    stable_id: str,
    parent_id: str,
    name: str,
    kind: str,
    content_type: str,
    data: bytes,
    created_at: str,
    updated_at: str,
    references: tuple[str, ...],
    metadata: dict[str, object],
) -> WritingSourceBody:
    digest = hashlib.sha256(data).hexdigest()
    return WritingSourceBody(
        descriptor=WritingSourceObjectDescriptor(
            stable_id=stable_id,
            parent_id=parent_id,
            name=name,
            kind=kind,
            content_type=content_type,
            body_encoding="utf-8",
            body_length=len(data),
            content_sha256=digest,
            source_fingerprint_sha256=digest,
            created_at=created_at,
            updated_at=updated_at,
            revision=1,
            references=references,
            metadata=metadata,
            body_source="supplemental",
            source_key=stable_id,
        ),
        body=data,
    )


def _collect_runtime_message_attachments(
    *,
    runtime_db: Session,
    user_id: int,
    messages: Iterable[object],
) -> tuple[tuple[WritingSourceBody, ...], Callable[[object], str | None]]:
    from anima_server.models.runtime import RuntimeImageAsset, RuntimeImageMessageLink
    from anima_server.services.images.store import resolve_image_storage_path

    raw_attachments = [
        attachment
        for message in messages
        for attachment in _message_attachments(message)
    ]
    raw_asset_ids = {
        int(value)
        for attachment in raw_attachments
        if (value := _source_value(attachment, "assetId", "asset_id")) is not None
        and not isinstance(value, bool)
        and isinstance(value, (int, str))
        and str(value).isdigit()
    }
    raw_attachment_ids = {
        str(value)
        for attachment in raw_attachments
        if isinstance((value := _source_value(attachment, "id")), str) and value
    }
    links = tuple(
        runtime_db.scalars(
            select(RuntimeImageMessageLink).where(
                RuntimeImageMessageLink.user_id == user_id,
                RuntimeImageMessageLink.attachment_id.in_(raw_attachment_ids),
            )
        ).all()
        if raw_attachment_ids
        else ()
    )
    asset_id_by_attachment = {
        str(link.attachment_id): int(link.image_asset_id)
        for link in links
        if link.attachment_id
    }
    asset_ids = raw_asset_ids | set(asset_id_by_attachment.values())
    assets = tuple(
        runtime_db.scalars(
            select(RuntimeImageAsset).where(
                RuntimeImageAsset.user_id == user_id,
                RuntimeImageAsset.id.in_(asset_ids),
            )
        ).all()
        if asset_ids
        else ()
    )
    by_id: dict[int, str] = {}
    by_sha256: dict[str, str] = {}
    objects: list[WritingSourceBody] = []
    parent_id = migration_opaque_id("core-folder-role", "core.conversations")
    for asset in sorted(assets, key=lambda value: int(value.id)):
        path = resolve_image_storage_path(asset.storage_path, user_id=user_id)
        size, digest = _file_identity(path)
        if size != int(asset.size_bytes) or digest != asset.sha256:
            raise ValueError("Conversation attachment source identity is invalid.")
        stable_id = migration_opaque_id("image-asset", str(asset.id))
        uri = f"corefs://object/{stable_id}"
        by_id[int(asset.id)] = uri
        by_sha256[str(asset.sha256)] = uri
        created_at = _timestamp(asset.created_at)
        updated_at = _timestamp(asset.updated_at)
        objects.append(
            WritingSourceBody(
                descriptor=WritingSourceObjectDescriptor(
                    stable_id=stable_id,
                    parent_id=parent_id,
                    name=f"attachment-{stable_id}",
                    kind="attachment",
                    content_type=asset.mime_type,
                    body_encoding="binary",
                    body_length=size,
                    content_sha256=digest,
                    source_fingerprint_sha256=digest,
                    created_at=created_at,
                    updated_at=updated_at,
                    revision=1,
                    metadata={
                        "origin": "conversation-attachment",
                        "legacyAssetId": int(asset.id),
                        "filename": asset.filename,
                        "mimeType": asset.mime_type,
                        "sizeBytes": size,
                    },
                    body_source="supplemental_path",
                    source_key=str(path),
                ),
                body=None,
            )
        )

    def resolve(value: object) -> str | None:
        asset_id = _source_value(value, "assetId", "asset_id")
        if not isinstance(asset_id, bool) and isinstance(asset_id, (int, str)):
            try:
                resolved = by_id.get(int(asset_id))
            except ValueError:
                resolved = None
            if resolved is not None:
                return resolved
        attachment_id = _source_value(value, "id")
        if isinstance(attachment_id, str):
            linked_id = asset_id_by_attachment.get(attachment_id)
            if linked_id is not None and linked_id in by_id:
                return by_id[linked_id]
        digest = _source_value(value, "sha256")
        return by_sha256.get(digest) if isinstance(digest, str) else None

    return tuple(objects), resolve


def _message_attachments(message: object) -> tuple[object, ...]:
    content = _source_value(message, "content_json", "contentJson")
    raw = content.get("attachments") if isinstance(content, Mapping) else None
    if raw is None and isinstance(message, Mapping):
        raw = message.get("attachments")
    return tuple(raw) if isinstance(raw, list) else ()


def _file_identity(path: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            size += len(chunk)
            hasher.update(chunk)
    return size, hasher.hexdigest()


def _conflict_thread_id(conflict: ConversationConflict) -> str | None:
    if conflict.identity is None:
        return None
    raw = conflict.identity
    if raw.startswith("stable:"):
        parts = raw.split(":", 2)
        return parts[1] if len(parts) == 3 else None
    candidate = raw.split(":", 1)[0]
    return candidate if len(candidate) == 26 else None


def _source_value(source: object, *names: str) -> object:
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return None
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return None


def _timestamp(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Conversation timestamp is invalid.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def _archive_created_at(sidecar: Mapping[str, object], rows: Iterable[object]) -> str:
    timestamps = [
        str(row.get("ts"))
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("ts"), str)
    ]
    return min(timestamps) if timestamps else str(sidecar.get("archived_at"))


def _archive_last_message_at(rows: Iterable[object]) -> str | None:
    timestamps = [
        str(row.get("ts"))
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("ts"), str)
    ]
    return max(timestamps) if timestamps else None


def conversation_shadow_digest(shadow: ConversationShadowCatalog) -> str:
    """Return a deterministic non-secret digest for retry/equality checks."""
    payload = {
        "folders": [folder.stable_id for folder in shadow.folders],
        "objects": [
            {
                "id": item.descriptor.stable_id,
                "sha256": item.descriptor.content_sha256,
            }
            for item in shadow.objects
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
