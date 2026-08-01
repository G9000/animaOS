from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime import RuntimeImageMessageLink, RuntimeMessage
from anima_server.services.agent.state import ATTACHMENTS_CONTENT_KEY
from anima_server.services.corefs.sealed_runtime import reseal_runtime_message
from anima_server.services.images.store import register_image_asset


@dataclass(frozen=True, slots=True)
class ImageBackfillReport:
    messages_scanned: int = 0
    assets_created: int = 0
    links_created: int = 0
    missing_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def backfill_legacy_chat_images(
    runtime_db: Session,
    *,
    user_id: int,
) -> ImageBackfillReport:
    messages = list(
        runtime_db.scalars(
            select(RuntimeMessage)
            .where(
                RuntimeMessage.user_id == user_id,
                RuntimeMessage.role == "user",
            )
            .order_by(RuntimeMessage.id)
        ).all()
    )

    messages_scanned = 0
    assets_created = 0
    links_created = 0
    missing_files: list[str] = []
    errors: list[str] = []

    for message in messages:
        payload = dict(message.content_json or {})
        raw_attachments = payload.get(ATTACHMENTS_CONTENT_KEY)
        if not isinstance(raw_attachments, list):
            continue

        changed = False
        next_attachments: list[object] = []
        message_had_legacy = False
        for raw_attachment in raw_attachments:
            if not isinstance(raw_attachment, dict):
                next_attachments.append(raw_attachment)
                continue
            attachment = dict(raw_attachment)
            if attachment.get("assetId") is not None:
                next_attachments.append(attachment)
                continue
            if attachment.get("kind") != "image":
                next_attachments.append(attachment)
                continue

            attachment_id = attachment.get("id")
            storage_path = attachment.get("storagePath")
            mime_type = attachment.get("mimeType")
            if (
                not isinstance(attachment_id, str)
                or not attachment_id
                or not isinstance(storage_path, str)
                or not storage_path
                or not isinstance(mime_type, str)
                or not mime_type
            ):
                next_attachments.append(attachment)
                continue

            message_had_legacy = True
            path = _resolve_legacy_path(storage_path)
            if path is None or not path.is_file():
                missing_files.append(f"message={message.id} attachment={attachment_id}")
                next_attachments.append(attachment)
                continue

            try:
                stored = register_image_asset(
                    runtime_db,
                    user_id=user_id,
                    data=path.read_bytes(),
                    mime_type=mime_type,
                    filename=attachment.get("filename")
                    if isinstance(attachment.get("filename"), str)
                    else None,
                    metadata_json={"origin": "legacy_chat_backfill"},
                )
            except Exception as exc:
                errors.append(f"message={message.id} attachment={attachment_id}: {exc}")
                next_attachments.append(attachment)
                continue

            if stored.created:
                assets_created += 1
            if _ensure_message_link(
                runtime_db,
                user_id=user_id,
                message_id=message.id,
                image_asset_id=stored.asset.id,
                attachment_id=attachment_id,
            ):
                links_created += 1

            attachment["assetId"] = stored.asset.id
            attachment["storagePath"] = stored.asset.storage_path
            attachment["sizeBytes"] = stored.asset.size_bytes
            attachment["sha256"] = stored.asset.sha256
            attachment["retentionState"] = stored.asset.retention_state
            next_attachments.append(attachment)
            changed = True

        if message_had_legacy:
            messages_scanned += 1
        if changed:
            payload[ATTACHMENTS_CONTENT_KEY] = next_attachments
            reseal_runtime_message(
                runtime_db,
                message,
                content_json=payload,
            )

    runtime_db.flush()
    return ImageBackfillReport(
        messages_scanned=messages_scanned,
        assets_created=assets_created,
        links_created=links_created,
        missing_files=missing_files,
        errors=errors,
    )


def _ensure_message_link(
    runtime_db: Session,
    *,
    user_id: int,
    message_id: int,
    image_asset_id: int,
    attachment_id: str,
) -> bool:
    existing = runtime_db.scalar(
        select(RuntimeImageMessageLink).where(
            RuntimeImageMessageLink.user_id == user_id,
            RuntimeImageMessageLink.message_id == message_id,
            RuntimeImageMessageLink.attachment_id == attachment_id,
        )
    )
    if existing is not None:
        return False
    runtime_db.add(
        RuntimeImageMessageLink(
            user_id=user_id,
            message_id=message_id,
            image_asset_id=image_asset_id,
            attachment_id=attachment_id,
        )
    )
    runtime_db.flush()
    return True


def _resolve_legacy_path(storage_path: str) -> Path | None:
    stripped = storage_path.strip()
    path = Path(stripped)
    windows_path = PureWindowsPath(stripped)
    if (
        not stripped
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
    ):
        return None
    try:
        data_root = settings.data_dir.resolve()
        resolved = (data_root / path).resolve()
        resolved.relative_to(data_root)
    except (OSError, ValueError):
        return None
    return resolved
