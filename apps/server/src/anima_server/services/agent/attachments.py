from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
from collections.abc import Sequence
from pathlib import Path

from anima_server.config import settings
from anima_server.schemas.chat import ChatRequestAttachment
from anima_server.services.agent.state import StoredAttachment, deserialize_stored_attachments

ALLOWED_IMAGE_MIME_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


class AttachmentValidationError(ValueError):
    status_code = 400


class AttachmentTooLargeError(AttachmentValidationError):
    status_code = 413


def validate_chat_attachment_inputs(
    attachments: Sequence[ChatRequestAttachment],
) -> None:
    _decode_and_validate_attachments(attachments)


def prepare_chat_attachments(
    *,
    user_id: int,
    attachments: Sequence[ChatRequestAttachment],
) -> tuple[StoredAttachment, ...]:
    decoded = _decode_and_validate_attachments(attachments)
    if not decoded:
        return ()

    stored: list[StoredAttachment] = []
    attachment_dir = settings.data_dir / "users" / str(user_id) / "attachments" / "chat"
    attachment_dir.mkdir(parents=True, exist_ok=True)

    for request_attachment, data, ext in decoded:
        attachment_id = _new_attachment_id()
        filename = _sanitize_filename(request_attachment.filename)
        storage_path = f"users/{user_id}/attachments/chat/{attachment_id}.{ext}"
        path = settings.data_dir / storage_path
        path.write_bytes(data)
        stored.append(
            StoredAttachment(
                id=attachment_id,
                kind="image",
                mime_type=_normalize_mime_type(request_attachment.mimeType),
                path=str(path),
                storage_path=storage_path,
                filename=filename,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )

    return tuple(stored)


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

        actual_mime = _detect_image_mime(data)
        if actual_mime != declared_mime:
            raise AttachmentValidationError(
                "Declared MIME type does not match image bytes."
            )

        decoded.append((attachment, data, ALLOWED_IMAGE_MIME_TYPES[declared_mime]))

    return decoded


def _detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
    return f"img_{secrets.token_hex(4)}"
