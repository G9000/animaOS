from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime import (
    RuntimeImageAnnotation,
    RuntimeImageAsset,
    RuntimeImageMessageLink,
    RuntimeMessage,
    RuntimeStep,
    RuntimeThread,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.models.runtime_memory import RuntimeSessionNote
from anima_server.services.agent.state import ATTACHMENTS_CONTENT_KEY, PILLS_CONTENT_KEY
from anima_server.services.corefs.sealed_runtime import (
    delete_sealed_runtime_records,
    reseal_runtime_message,
)
from anima_server.services.data_crypto import get_active_dek
from anima_server.services.images.store import delete_image_asset_file_if_safe

RETAINED_IMAGE_STATES = frozenset({"retained", "durable"})
_ARCHIVED_THREAD_TRANSCRIPT_RE = re.compile(r"_thread-(\d+)\.jsonl(?:\.enc)?$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ForgetImageResult:
    forgotten: bool
    image_asset_id: int
    file_deleted: bool = False


@dataclass(frozen=True, slots=True)
class RemoveImageLinkResult:
    removed: bool
    image_asset_id: int | None = None
    asset_deleted: bool = False
    file_deleted: bool = False


@dataclass(frozen=True, slots=True)
class DeleteThreadImageCleanupResult:
    deleted: bool
    thread_id: int
    assets_deleted: list[int] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)


def forget_image_asset(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
) -> ForgetImageResult:
    asset = runtime_db.scalar(
        select(RuntimeImageAsset).where(
            RuntimeImageAsset.id == image_asset_id,
            RuntimeImageAsset.user_id == user_id,
        )
    )
    if asset is None:
        return ForgetImageResult(forgotten=False, image_asset_id=image_asset_id)

    linked_messages = list(
        runtime_db.execute(
            select(RuntimeImageMessageLink, RuntimeMessage)
            .join(RuntimeMessage, RuntimeImageMessageLink.message_id == RuntimeMessage.id)
            .where(
                RuntimeImageMessageLink.user_id == user_id,
                RuntimeImageMessageLink.image_asset_id == image_asset_id,
                RuntimeMessage.user_id == user_id,
            )
        ).all()
    )
    linked_attachment_ids = {
        link.attachment_id for link, _message in linked_messages if link.attachment_id
    }
    for link, message in linked_messages:
        _remove_image_asset_metadata(
            runtime_db,
            message,
            image_asset_id=image_asset_id,
            attachment_id=link.attachment_id,
        )
    _remove_image_source_pills(
        runtime_db,
        user_id=user_id,
        image_asset_id=image_asset_id,
        attachment_ids=linked_attachment_ids,
    )

    annotation_ids = list(
        runtime_db.scalars(
            select(RuntimeImageAnnotation.id).where(
                RuntimeImageAnnotation.user_id == user_id,
                RuntimeImageAnnotation.image_asset_id == image_asset_id,
            )
        ).all()
    )
    if annotation_ids:
        runtime_db.execute(
            delete(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == "image_annotation",
                RuntimeEmbedding.source_id.in_(annotation_ids),
            )
        )
        delete_sealed_runtime_records(
            runtime_db,
            row_type="runtime_image_annotation",
            row_ids=annotation_ids,
            owner_id=user_id,
        )

    file_deleted = delete_image_asset_file_if_safe(asset)
    runtime_db.delete(asset)
    runtime_db.flush()
    return ForgetImageResult(
        forgotten=True,
        image_asset_id=image_asset_id,
        file_deleted=file_deleted,
    )


def remove_message_image_link(
    runtime_db: Session,
    *,
    user_id: int,
    message_id: int,
    attachment_id: str,
) -> RemoveImageLinkResult:
    message = runtime_db.scalar(
        select(RuntimeMessage).where(
            RuntimeMessage.id == message_id,
            RuntimeMessage.user_id == user_id,
        )
    )
    if message is None:
        return RemoveImageLinkResult(removed=False)

    link = runtime_db.scalar(
        select(RuntimeImageMessageLink).where(
            RuntimeImageMessageLink.user_id == user_id,
            RuntimeImageMessageLink.message_id == message_id,
            RuntimeImageMessageLink.attachment_id == attachment_id,
        )
    )
    if link is None:
        return RemoveImageLinkResult(removed=False)

    image_asset_id = link.image_asset_id
    _remove_attachment_metadata(runtime_db, message, attachment_id)
    _remove_image_source_pills(
        runtime_db,
        user_id=user_id,
        image_asset_id=None,
        attachment_ids={attachment_id},
    )
    runtime_db.delete(link)
    runtime_db.flush()
    deleted, file_deleted = _delete_orphaned_transient_asset(
        runtime_db,
        user_id=user_id,
        image_asset_id=image_asset_id,
    )
    return RemoveImageLinkResult(
        removed=True,
        image_asset_id=image_asset_id,
        asset_deleted=deleted,
        file_deleted=file_deleted,
    )


def delete_thread_with_image_cleanup(
    runtime_db: Session,
    *,
    user_id: int,
    thread_id: int,
) -> DeleteThreadImageCleanupResult:
    thread = runtime_db.scalar(
        select(RuntimeThread).where(
            RuntimeThread.id == thread_id,
            RuntimeThread.user_id == user_id,
        )
    )
    if thread is None:
        return DeleteThreadImageCleanupResult(deleted=False, thread_id=thread_id)

    message_ids = list(
        runtime_db.scalars(
            select(RuntimeMessage.id).where(
                RuntimeMessage.user_id == user_id,
                RuntimeMessage.thread_id == thread_id,
            )
        ).all()
    )
    step_ids = list(
        runtime_db.scalars(select(RuntimeStep.id).where(RuntimeStep.thread_id == thread_id)).all()
    )
    session_note_ids = list(
        runtime_db.scalars(
            select(RuntimeSessionNote.id).where(
                RuntimeSessionNote.user_id == user_id,
                RuntimeSessionNote.thread_id == thread_id,
            )
        ).all()
    )
    candidate_asset_ids = _candidate_thread_image_asset_ids(
        runtime_db,
        user_id=user_id,
        thread_id=thread_id,
        message_ids=message_ids,
    )

    if message_ids:
        runtime_db.execute(
            delete(RuntimeImageMessageLink).where(
                RuntimeImageMessageLink.user_id == user_id,
                RuntimeImageMessageLink.message_id.in_(message_ids),
            )
        )
        delete_sealed_runtime_records(
            runtime_db,
            row_type="runtime_message",
            row_ids=message_ids,
            owner_id=user_id,
        )
    if step_ids:
        delete_sealed_runtime_records(
            runtime_db,
            row_type="runtime_step",
            row_ids=step_ids,
            owner_id=user_id,
        )
    if session_note_ids:
        delete_sealed_runtime_records(
            runtime_db,
            row_type="runtime_session_note",
            row_ids=session_note_ids,
            owner_id=user_id,
        )
    runtime_db.execute(delete(RuntimeMessage).where(RuntimeMessage.thread_id == thread_id))
    runtime_db.delete(thread)
    runtime_db.flush()

    assets_deleted: list[int] = []
    files_deleted: list[str] = []
    for image_asset_id in candidate_asset_ids:
        asset = runtime_db.get(RuntimeImageAsset, image_asset_id)
        file_path = None
        if asset is not None:
            try:
                from anima_server.services.images.store import resolve_image_storage_path

                file_path = resolve_image_storage_path(
                    asset.storage_path,
                    user_id=asset.user_id,
                )
            except Exception:
                file_path = None
        deleted, file_deleted = _delete_orphaned_transient_asset(
            runtime_db,
            user_id=user_id,
            image_asset_id=image_asset_id,
            ignored_archive_thread_id=thread_id,
        )
        if deleted:
            assets_deleted.append(image_asset_id)
        if file_deleted and file_path is not None:
            files_deleted.append(str(file_path))

    return DeleteThreadImageCleanupResult(
        deleted=True,
        thread_id=thread_id,
        assets_deleted=assets_deleted,
        files_deleted=files_deleted,
    )


def set_image_retention_state(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
    retention_state: str,
) -> RuntimeImageAsset | None:
    normalized = retention_state.strip().lower()
    if normalized not in {"transient", "retained", "durable"}:
        raise ValueError("retention_state must be transient, retained, or durable")
    asset = runtime_db.scalar(
        select(RuntimeImageAsset).where(
            RuntimeImageAsset.id == image_asset_id,
            RuntimeImageAsset.user_id == user_id,
        )
    )
    if asset is None:
        return None
    asset.retention_state = normalized
    runtime_db.flush()
    return asset


def _candidate_thread_image_asset_ids(
    runtime_db: Session,
    *,
    user_id: int,
    thread_id: int,
    message_ids: list[int],
) -> list[int]:
    candidate_asset_ids: list[int] = []
    seen: set[int] = set()

    if message_ids:
        for image_asset_id in runtime_db.scalars(
            select(RuntimeImageMessageLink.image_asset_id)
            .where(
                RuntimeImageMessageLink.user_id == user_id,
                RuntimeImageMessageLink.message_id.in_(message_ids),
            )
            .distinct()
        ).all():
            _append_candidate_asset_id(candidate_asset_ids, seen, image_asset_id)

    for image_asset_id in _archived_thread_image_asset_ids(
        user_id=user_id,
        thread_id=thread_id,
    ):
        _append_candidate_asset_id(candidate_asset_ids, seen, image_asset_id)

    return candidate_asset_ids


def _append_candidate_asset_id(
    candidate_asset_ids: list[int],
    seen: set[int],
    value: object,
) -> None:
    image_asset_id = _coerce_positive_int(value)
    if image_asset_id is None or image_asset_id in seen:
        return
    seen.add(image_asset_id)
    candidate_asset_ids.append(image_asset_id)


def _archived_thread_image_asset_ids(
    *,
    user_id: int,
    thread_id: int,
) -> list[int]:
    transcripts_dir = settings.data_dir / "transcripts"
    if not transcripts_dir.exists():
        return []

    candidates = [
        path
        for path in transcripts_dir.glob(f"*_thread-{thread_id}.jsonl*")
        if path.suffix in {".jsonl", ".enc"}
    ]
    if not candidates:
        return []

    transcript_path = sorted(candidates)[-1]
    return _archived_transcript_image_asset_ids(
        user_id=user_id,
        thread_id=thread_id,
        transcript_path=transcript_path,
        dek=get_active_dek(user_id, "conversations"),
    )


def _archived_transcript_image_asset_ids(
    *,
    user_id: int,
    thread_id: int,
    transcript_path: Path,
    dek: bytes | None,
) -> list[int]:
    from anima_server.services.agent.transcript_archive import decrypt_transcript

    try:
        messages = decrypt_transcript(
            transcript_path,
            dek=dek,
            thread_id=thread_id,
        )
    except Exception:
        logger.debug(
            "Failed to inspect archived transcript image assets for thread %s at %s",
            thread_id,
            transcript_path,
            exc_info=True,
        )
        return []

    candidate_asset_ids: list[int] = []
    seen: set[int] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        raw_attachments = message.get("attachments")
        if not isinstance(raw_attachments, list):
            continue
        for attachment in raw_attachments:
            if not isinstance(attachment, dict):
                continue
            _append_candidate_asset_id(
                candidate_asset_ids,
                seen,
                attachment.get("assetId"),
            )
    return candidate_asset_ids


def _coerce_positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _delete_orphaned_transient_asset(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
    ignored_archive_thread_id: int | None = None,
) -> tuple[bool, bool]:
    asset = runtime_db.scalar(
        select(RuntimeImageAsset).where(
            RuntimeImageAsset.id == image_asset_id,
            RuntimeImageAsset.user_id == user_id,
        )
    )
    if asset is None:
        return False, False
    if asset.retention_state in RETAINED_IMAGE_STATES:
        return False, False
    link_count = (
        runtime_db.scalar(
            select(func.count(RuntimeImageMessageLink.id)).where(
                RuntimeImageMessageLink.user_id == user_id,
                RuntimeImageMessageLink.image_asset_id == image_asset_id,
            )
        )
        or 0
    )
    if link_count > 0:
        return False, False
    if _archived_image_asset_reference_exists(
        runtime_db,
        user_id=user_id,
        image_asset_id=image_asset_id,
        ignored_thread_id=ignored_archive_thread_id,
    ):
        return False, False
    result = forget_image_asset(
        runtime_db,
        user_id=user_id,
        image_asset_id=image_asset_id,
    )
    return result.forgotten, result.file_deleted


def _archived_image_asset_reference_exists(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
    ignored_thread_id: int | None = None,
) -> bool:
    transcripts_dir = settings.data_dir / "transcripts"
    if not transcripts_dir.exists():
        return False

    dek = get_active_dek(user_id, "conversations")
    for thread_id, transcript_path in _iter_archived_transcripts(transcripts_dir):
        if ignored_thread_id is not None and thread_id == ignored_thread_id:
            continue
        if not _runtime_thread_exists(
            runtime_db,
            user_id=user_id,
            thread_id=thread_id,
        ):
            continue
        asset_ids = _archived_transcript_image_asset_ids(
            user_id=user_id,
            thread_id=thread_id,
            transcript_path=transcript_path,
            dek=dek,
        )
        if image_asset_id in asset_ids:
            return True
    return False


def _runtime_thread_exists(
    runtime_db: Session,
    *,
    user_id: int,
    thread_id: int,
) -> bool:
    thread_id = runtime_db.scalar(
        select(RuntimeThread.id).where(
            RuntimeThread.id == thread_id,
            RuntimeThread.user_id == user_id,
        )
    )
    return thread_id is not None


def _iter_archived_transcripts(transcripts_dir: Path) -> list[tuple[int, Path]]:
    candidates: list[tuple[int, Path]] = []
    for path in transcripts_dir.glob("*_thread-*.jsonl*"):
        if path.suffix not in {".jsonl", ".enc"}:
            continue
        thread_id = _archived_transcript_thread_id(path)
        if thread_id is None:
            continue
        candidates.append((thread_id, path))
    return sorted(candidates, key=lambda candidate: (candidate[0], candidate[1].name))


def _archived_transcript_thread_id(path: Path) -> int | None:
    match = _ARCHIVED_THREAD_TRANSCRIPT_RE.search(path.name)
    if match is None:
        return None
    return _coerce_positive_int(match.group(1))


def _remove_attachment_metadata(
    runtime_db: Session,
    message: RuntimeMessage,
    attachment_id: str,
) -> None:
    _remove_image_asset_metadata(
        runtime_db,
        message,
        image_asset_id=None,
        attachment_id=attachment_id,
    )


def _remove_image_asset_metadata(
    runtime_db: Session,
    message: RuntimeMessage,
    *,
    image_asset_id: int | None,
    attachment_id: str | None,
) -> None:
    payload = dict(message.content_json or {})
    raw_attachments = payload.get(ATTACHMENTS_CONTENT_KEY)
    if not isinstance(raw_attachments, list):
        return
    payload[ATTACHMENTS_CONTENT_KEY] = [
        attachment
        for attachment in raw_attachments
        if not (
            isinstance(attachment, dict)
            and (
                (attachment_id is not None and attachment.get("id") == attachment_id)
                or (image_asset_id is not None and attachment.get("assetId") == image_asset_id)
            )
        )
    ]
    reseal_runtime_message(
        runtime_db,
        message,
        content_json=payload,
    )


def _remove_image_source_pills(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int | None,
    attachment_ids: set[str],
) -> None:
    messages = list(
        runtime_db.scalars(
            select(RuntimeMessage).where(
                RuntimeMessage.user_id == user_id,
            )
        ).all()
    )
    for message in messages:
        payload = dict(message.content_json or {})
        raw_pills = payload.get(PILLS_CONTENT_KEY)
        if not isinstance(raw_pills, list):
            continue
        next_pills = [
            pill
            for pill in raw_pills
            if not _is_matching_image_source_pill(
                pill,
                image_asset_id=image_asset_id,
                attachment_ids=attachment_ids,
            )
        ]
        if len(next_pills) == len(raw_pills):
            continue
        payload[PILLS_CONTENT_KEY] = next_pills
        reseal_runtime_message(
            runtime_db,
            message,
            content_json=payload,
        )


def _is_matching_image_source_pill(
    pill: object,
    *,
    image_asset_id: int | None,
    attachment_ids: set[str],
) -> bool:
    if not isinstance(pill, dict) or pill.get("kind") != "image_source":
        return False
    ref = pill.get("ref")
    return (
        image_asset_id is not None and (ref == image_asset_id or ref == f"image:{image_asset_id}")
    ) or (isinstance(ref, str) and ref in attachment_ids)
