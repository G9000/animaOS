from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from anima_server.config import settings
from anima_server.schemas.chat import ChatRequestAttachment
from anima_server.services.agent.state import StoredAttachment
from anima_server.services.images.store import (
    ALLOWED_IMAGE_MIME_TYPES,
    detect_image_mime,
)


class AttachmentValidationError(ValueError):
    status_code = 400


class AttachmentTooLargeError(AttachmentValidationError):
    status_code = 413


class AttachmentReadError(RuntimeError):
    """Raised when a persisted attachment file cannot be read for provider input."""


def read_attachment_bytes(path: str) -> bytes:
    corefs_match = _COREFS_PROVIDER_PATH.fullmatch(path)
    if corefs_match is not None:
        from anima_server.services.corefs.asset_authority import (
            CoreFsSourceError,
            open_corefs_byte_source,
            require_canonical_asset_session,
        )

        user_id = int(corefs_match.group(1))
        stable_id = corefs_match.group(2)
        session = require_canonical_asset_session(user_id)
        try:
            return open_corefs_byte_source(
                session=session,
                object_uri=f"corefs://object/{stable_id}",
                expected_kinds=frozenset({"attachment", "gallery-asset"}),
            ).read_all(max_bytes=settings.chat_image_max_size_bytes)
        except CoreFsSourceError as exc:
            raise AttachmentReadError("Canonical image attachment is unavailable.") from exc
    raise AttachmentReadError("Only canonical CoreFS image attachments are supported.")


def prepare_corefs_chat_attachments(
    *,
    session: Any,
    attachments: Sequence[ChatRequestAttachment],
) -> tuple[StoredAttachment, ...]:
    from anima_server.services.corefs.asset_authority import CoreFsSourceError
    from anima_server.services.corefs.asset_mutations import (
        AssetMutationError,
        trash_canonical_asset,
        upsert_canonical_binary_asset,
    )
    from anima_server.services.corefs.catalog_reader import stable_opaque_id

    decoded = _decode_and_validate_attachments(attachments)
    stored: list[StoredAttachment] = []
    created_ids: list[str] = []
    try:
        for request_attachment, data, ext in decoded:
            stable_id = stable_opaque_id("chat-attachment", _new_attachment_id())
            filename = _sanitize_filename(request_attachment.filename)
            normalized_mime = _normalize_mime_type(request_attachment.mimeType)
            upsert_canonical_binary_asset(
                session=session,
                stable_id=stable_id,
                name=filename or f"chat-image.{ext}",
                object_kind="attachment",
                content_type=normalized_mime,
                data=data,
            )
            created_ids.append(stable_id)
            stored.append(
                StoredAttachment(
                    id=stable_id,
                    kind="image",
                    mime_type=normalized_mime,
                    path=_corefs_provider_path(int(session.user_id), stable_id),
                    filename=filename,
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    storage_path=f"corefs://object/{stable_id}",
                    delete_on_error=False,
                )
            )
    except (AssetMutationError, CoreFsSourceError, ValueError):
        for stable_id in reversed(created_ids):
            with suppress(AssetMutationError, CoreFsSourceError, ValueError):
                trash_canonical_asset(session=session, stable_id=stable_id)
        raise
    return tuple(stored)


def resolve_corefs_chat_attachment(*, session: Any, object_uri: str) -> StoredAttachment:
    from anima_server.services.corefs.asset_authority import open_corefs_byte_source

    source = open_corefs_byte_source(
        session=session,
        object_uri=object_uri,
        expected_kinds=frozenset({"attachment", "gallery-asset"}),
    )
    if (
        source.content_type not in ALLOWED_IMAGE_MIME_TYPES
        or source.size > settings.chat_image_max_size_bytes
    ):
        raise AttachmentReadError("Canonical chat attachment is not a supported image.")
    return StoredAttachment(
        id=source.stable_id,
        kind="image",
        mime_type=source.content_type,
        path=_corefs_provider_path(int(session.user_id), source.stable_id),
        filename=source.name or None,
        size_bytes=source.size,
        sha256=source.content_sha256,
        storage_path=object_uri,
        delete_on_error=False,
    )


def validate_chat_attachment_inputs(
    attachments: Sequence[ChatRequestAttachment],
) -> None:
    _decode_and_validate_attachments(attachments)


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
            raise AttachmentValidationError("Unsupported image type. Use PNG, JPEG, WebP, or GIF.")

        try:
            data = base64.b64decode(attachment.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentValidationError("Attachment data must be valid base64.") from exc

        if len(data) > settings.chat_image_max_size_bytes:
            raise AttachmentTooLargeError(
                f"Image attachment is too large. Limit is "
                f"{settings.chat_image_max_size_bytes} bytes."
            )

        actual_mime = detect_image_mime(data)
        if actual_mime != declared_mime:
            raise AttachmentValidationError("Declared MIME type does not match image bytes.")

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


_COREFS_PROVIDER_PATH = re.compile(r"corefs-user://([0-9]+)/object/([0-7][0-9A-HJKMNP-TV-Z]{25})")


def _corefs_provider_path(user_id: int, stable_id: str) -> str:
    return f"corefs-user://{user_id}/object/{stable_id}"
