from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime import RuntimeImageMessageLink, RuntimeMessage
from anima_server.schemas.chat import ChatRequestAttachment
from anima_server.services.agent.state import StoredAttachment, deserialize_stored_attachments
from anima_server.services.images.store import (
    ALLOWED_IMAGE_MIME_TYPES,
    detect_image_mime,
    register_image_asset,
    resolve_image_storage_path,
)


class AttachmentValidationError(ValueError):
    status_code = 400


class AttachmentTooLargeError(AttachmentValidationError):
    status_code = 413


class AttachmentReadError(RuntimeError):
    """Raised when a persisted attachment file cannot be read for provider input."""


def read_attachment_bytes(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise AttachmentReadError(
            "Unable to read image attachment; the saved file is missing or unreadable."
        ) from exc


def validate_chat_attachment_inputs(
    attachments: Sequence[ChatRequestAttachment],
) -> None:
    _decode_and_validate_attachments(attachments)


def prepare_chat_attachments(
    *,
    user_id: int,
    attachments: Sequence[ChatRequestAttachment],
    runtime_db: Session | None = None,
) -> tuple[StoredAttachment, ...]:
    decoded = _decode_and_validate_attachments(attachments)
    if not decoded:
        return ()

    stored: list[StoredAttachment] = []
    attachment_dir = settings.data_dir / "users" / str(user_id) / "attachments" / "chat"
    if runtime_db is None:
        attachment_dir.mkdir(parents=True, exist_ok=True)

    for request_attachment, data, ext in decoded:
        attachment_id = _new_attachment_id()
        filename = _sanitize_filename(request_attachment.filename)
        normalized_mime = _normalize_mime_type(request_attachment.mimeType)
        if runtime_db is not None:
            stored_asset = register_image_asset(
                runtime_db,
                user_id=user_id,
                data=data,
                mime_type=normalized_mime,
                filename=filename,
                metadata_json={"origin": "chat"},
            )
            stored.append(
                StoredAttachment(
                    id=attachment_id,
                    kind="image",
                    mime_type=stored_asset.asset.mime_type,
                    path=str(stored_asset.path),
                    asset_id=stored_asset.asset.id,
                    storage_path=stored_asset.asset.storage_path,
                    filename=filename,
                    size_bytes=stored_asset.asset.size_bytes,
                    sha256=stored_asset.asset.sha256,
                    retention_state=stored_asset.asset.retention_state,
                    delete_on_error=stored_asset.created,
                )
            )
            continue

        storage_path = f"users/{user_id}/attachments/chat/{attachment_id}.{ext}"
        path = settings.data_dir / storage_path
        path.write_bytes(data)
        stored.append(
            StoredAttachment(
                id=attachment_id,
                kind="image",
                mime_type=normalized_mime,
                path=str(path),
                storage_path=storage_path,
                filename=filename,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )

    return tuple(stored)


def resolve_message_attachment(
    runtime_db: Session,
    *,
    message: RuntimeMessage,
    attachment_id: str,
) -> tuple[Path, str] | None:
    for attachment in deserialize_stored_attachments(message.content_json):
        if attachment.id != attachment_id:
            continue
        if attachment.asset_id is None:
            return resolve_message_attachment_path(
                message.content_json,
                attachment_id=attachment_id,
            )

        link = runtime_db.scalar(
            select(RuntimeImageMessageLink).where(
                RuntimeImageMessageLink.user_id == message.user_id,
                RuntimeImageMessageLink.message_id == message.id,
                RuntimeImageMessageLink.image_asset_id == attachment.asset_id,
            )
        )
        if link is None or link.image_asset is None:
            return None

        try:
            path = resolve_image_storage_path(
                link.image_asset.storage_path,
                user_id=message.user_id,
            )
        except ValueError:
            return None
        return path, link.image_asset.mime_type
    return None


def resolve_message_attachment_path(
    content_json: dict[str, object] | None,
    *,
    attachment_id: str,
) -> tuple[Path, str] | None:
    for attachment in deserialize_stored_attachments(content_json):
        if attachment.id != attachment_id:
            continue
        if not attachment.storage_path:
            return None
        try:
            data_root = settings.data_dir.resolve()
            path = (settings.data_dir / attachment.storage_path).resolve()
            path.relative_to(data_root)
        except (OSError, ValueError):
            return None
        return path, attachment.mime_type
    return None


def _decode_and_validate_attachments(
    attachments: Sequence[ChatRequestAttachment],
) -> list[tuple[ChatRequestAttachment, bytes, str]]:
    if not attachments:
        return []

    max_count = settings.chat_image_max_count
    if len(attachments) > max_count:
        noun = "image" if max_count == 1 else "images"
        raise AttachmentValidationError(f"Attach at most {max_count} {noun} per message.")

    decoded: list[tuple[ChatRequestAttachment, bytes, str]] = []
    for attachment in attachments:
        declared_mime = _normalize_mime_type(attachment.mimeType)
        if attachment.kind != "image" or declared_mime not in ALLOWED_IMAGE_MIME_TYPES:
            raise AttachmentValidationError(
                "Unsupported image type. Use PNG, JPEG, WebP, or GIF."
            )

        try:
            data = base64.b64decode(attachment.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentValidationError(
                "Attachment data must be valid base64."
            ) from exc

        if len(data) > settings.chat_image_max_size_bytes:
            raise AttachmentTooLargeError(
                f"Image attachment is too large. Limit is "
                f"{settings.chat_image_max_size_bytes} bytes."
            )

        actual_mime = detect_image_mime(data)
        if actual_mime != declared_mime:
            raise AttachmentValidationError(
                "Declared MIME type does not match image bytes."
            )

        decoded.append((attachment, data, ALLOWED_IMAGE_MIME_TYPES[declared_mime]))

    return decoded


def _normalize_mime_type(value: str) -> str:
    return value.split(";", maxsplit=1)[0].strip().lower()


_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._ -]+")


def _sanitize_filename(filename: str | None) -> str | None:
    if filename is None:
        return None
    name = Path(filename).name.strip()
    if not name:
        return None
    name = _SAFE_FILENAME_RE.sub("_", name)
    return name[:180] or None


def _new_attachment_id() -> str:
    return f"img_{secrets.token_hex(8)}"
