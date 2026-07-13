from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from anima_server.config import settings
from anima_server.models import DiaryAttachment, DiaryEntry, DiaryFolder
from anima_server.schemas.diary import (
    DiaryAttachmentResponse,
    DiaryEntryResponse,
    DiaryFolderResponse,
)
from anima_server.services.crypto import decrypt_blob, encrypt_blob
from anima_server.services.data_crypto import DOMAIN_MEMORIES, df, ef, require_dek_for_user


class DiaryValidationError(ValueError):
    """Raised when diary input cannot be safely stored."""


@dataclass(frozen=True, slots=True)
class DecryptedDiaryBlob:
    data: bytes
    mime_type: str
    filename: str | None


def _validate_folder_ownership(db: Session, *, user_id: int, folder_id: int) -> None:
    folder = db.scalar(
        select(DiaryFolder).where(DiaryFolder.id == folder_id, DiaryFolder.user_id == user_id)
    )
    if folder is None:
        raise DiaryValidationError("Folder must belong to this user.")


def create_diary_entry(
    db: Session,
    *,
    user_id: int,
    entry_date: str,
    title: str | None,
    body: str,
    mood: str | None,
    folder_id: int | None = None,
) -> DiaryEntry:
    if folder_id is not None:
        _validate_folder_ownership(db, user_id=user_id, folder_id=folder_id)
    entry = DiaryEntry(
        user_id=user_id,
        entry_date=entry_date,
        title=ef(user_id, title, table="diary_entries", field="title"),
        body=ef(user_id, body, table="diary_entries", field="body") or "",
        mood=ef(user_id, mood, table="diary_entries", field="mood"),
        folder_id=folder_id,
        source="user",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def update_diary_entry(
    db: Session,
    *,
    entry: DiaryEntry,
    entry_date: str | None,
    title: str | None,
    body: str | None,
    mood: str | None,
    cover_attachment_id: int | None = None,
    folder_id: int | None = None,
    clear_title: bool = False,
    clear_mood: bool = False,
    clear_cover: bool = False,
    clear_folder: bool = False,
) -> DiaryEntry:
    user_id = entry.user_id
    if entry_date is not None:
        entry.entry_date = entry_date
    if body is not None:
        entry.body = ef(user_id, body, table="diary_entries", field="body") or ""
    if clear_title:
        entry.title = None
    elif title is not None:
        entry.title = ef(user_id, title, table="diary_entries", field="title")
    if clear_mood:
        entry.mood = None
    elif mood is not None:
        entry.mood = ef(user_id, mood, table="diary_entries", field="mood")
    if clear_cover:
        entry.cover_attachment_id = None
    elif cover_attachment_id is not None:
        cover = next(
            (a for a in entry.attachments if a.id == cover_attachment_id),
            None,
        )
        if cover is None:
            raise DiaryValidationError("Cover attachment must belong to this entry.")
        if cover.kind != "image":
            raise DiaryValidationError("Cover attachment must be an image.")
        entry.cover_attachment_id = cover_attachment_id
    if clear_folder:
        entry.folder_id = None
    elif folder_id is not None:
        _validate_folder_ownership(db, user_id=user_id, folder_id=folder_id)
        entry.folder_id = folder_id
    entry.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(entry)
    return entry


def create_diary_folder(db: Session, *, user_id: int, name: str) -> DiaryFolder:
    folder = DiaryFolder(
        user_id=user_id,
        name=ef(user_id, name, table="diary_folders", field="name") or name,
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return folder


def list_diary_folders(db: Session, *, user_id: int) -> list[tuple[DiaryFolder, int]]:
    folders = list(
        db.scalars(
            select(DiaryFolder)
            .where(DiaryFolder.user_id == user_id)
            .order_by(DiaryFolder.created_at)
        ).all()
    )
    counts = dict(
        db.execute(
            select(DiaryEntry.folder_id, func.count(DiaryEntry.id))
            .where(DiaryEntry.user_id == user_id, DiaryEntry.folder_id.isnot(None))
            .group_by(DiaryEntry.folder_id)
        ).all()
    )
    return [(folder, counts.get(folder.id, 0)) for folder in folders]


def load_folder_for_user(db: Session, *, user_id: int, folder_id: int) -> DiaryFolder | None:
    return db.scalar(
        select(DiaryFolder).where(DiaryFolder.id == folder_id, DiaryFolder.user_id == user_id)
    )


def rename_diary_folder(db: Session, *, folder: DiaryFolder, name: str) -> DiaryFolder:
    folder.name = ef(folder.user_id, name, table="diary_folders", field="name") or name
    db.commit()
    db.refresh(folder)
    return folder


def delete_diary_folder(db: Session, *, folder: DiaryFolder) -> None:
    # SQLite FK enforcement isn't enabled for these per-user databases, so the
    # ON DELETE SET NULL on diary_entries.folder_id is not applied by the DB
    # itself; unfile entries explicitly instead of relying on it.
    entries = db.scalars(select(DiaryEntry).where(DiaryEntry.folder_id == folder.id)).all()
    for entry in entries:
        entry.folder_id = None
    db.delete(folder)
    db.commit()


def diary_folder_to_response(folder: DiaryFolder, *, user_id: int, entry_count: int) -> DiaryFolderResponse:
    return DiaryFolderResponse(
        id=folder.id,
        userId=folder.user_id,
        name=df(user_id, folder.name, table="diary_folders", field="name") or folder.name,
        entryCount=entry_count,
        createdAt=folder.created_at,
    )


def list_diary_entries(
    db: Session,
    *,
    user_id: int,
    limit: int,
) -> list[DiaryEntry]:
    return list(
        db.scalars(
            select(DiaryEntry)
            .options(selectinload(DiaryEntry.attachments))
            .where(DiaryEntry.user_id == user_id)
            .order_by(DiaryEntry.entry_date.desc(), DiaryEntry.created_at.desc())
            .limit(limit)
        ).all()
    )


async def attach_file_to_entry(
    db: Session,
    *,
    user_id: int,
    entry: DiaryEntry,
    file: UploadFile,
    caption: str | None,
) -> DiaryAttachment:
    data = await file.read()
    if not data:
        raise DiaryValidationError("Attachment file is empty.")
    if len(data) > settings.diary_attachment_max_size_bytes:
        raise DiaryValidationError(
            f"Attachment is too large. Limit is {settings.diary_attachment_max_size_bytes} bytes."
        )

    mime_type = _normalize_mime_type(file.content_type or "application/octet-stream")
    kind = _kind_from_mime_type(mime_type)
    filename = _sanitize_filename(file.filename)
    sha256 = hashlib.sha256(data).hexdigest()
    storage_path = _new_storage_path(user_id=user_id, kind=kind)
    encrypted = encrypt_blob(
        data,
        require_dek_for_user(user_id, DOMAIN_MEMORIES),
        aad=_attachment_blob_aad(user_id=user_id, storage_path=storage_path),
    )

    path = _resolve_storage_path(storage_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(encrypted)
        attachment = DiaryAttachment(
            entry_id=entry.id,
            user_id=user_id,
            kind=kind,
            mime_type=mime_type,
            size_bytes=len(data),
            storage_path=storage_path,
            original_filename=ef(
                user_id,
                filename,
                table="diary_attachments",
                field="original_filename",
            ),
            caption=ef(
                user_id,
                _clean_optional(caption),
                table="diary_attachments",
                field="caption",
            ),
            sha256=sha256,
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)
        return attachment
    except Exception:
        _unlink_if_safe(storage_path)
        raise


def load_entry_for_user(db: Session, *, user_id: int, entry_id: int) -> DiaryEntry | None:
    return db.scalar(
        select(DiaryEntry)
        .options(selectinload(DiaryEntry.attachments))
        .where(DiaryEntry.id == entry_id, DiaryEntry.user_id == user_id)
    )


def load_attachment_for_user(
    db: Session,
    *,
    user_id: int,
    entry_id: int,
    attachment_id: int,
) -> DiaryAttachment | None:
    return db.scalar(
        select(DiaryAttachment).where(
            DiaryAttachment.id == attachment_id,
            DiaryAttachment.entry_id == entry_id,
            DiaryAttachment.user_id == user_id,
        )
    )


def read_attachment_blob(*, user_id: int, attachment: DiaryAttachment) -> DecryptedDiaryBlob:
    path = _resolve_storage_path(attachment.storage_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Attachment file is missing.")
    encrypted = path.read_bytes()
    data = decrypt_blob(
        encrypted,
        require_dek_for_user(user_id, DOMAIN_MEMORIES),
        aad=_attachment_blob_aad(user_id=user_id, storage_path=attachment.storage_path),
    )
    return DecryptedDiaryBlob(
        data=data,
        mime_type=attachment.mime_type,
        filename=df(
            user_id,
            attachment.original_filename,
            table="diary_attachments",
            field="original_filename",
        )
        or None,
    )


def delete_diary_entry(db: Session, *, entry: DiaryEntry) -> None:
    storage_paths = [attachment.storage_path for attachment in entry.attachments]
    db.delete(entry)
    db.commit()
    for storage_path in storage_paths:
        _unlink_if_safe(storage_path)


def diary_entry_to_response(entry: DiaryEntry, *, user_id: int) -> DiaryEntryResponse:
    return DiaryEntryResponse(
        id=entry.id,
        userId=entry.user_id,
        entryDate=entry.entry_date,
        title=df(user_id, entry.title, table="diary_entries", field="title") or None,
        body=df(user_id, entry.body, table="diary_entries", field="body"),
        mood=df(user_id, entry.mood, table="diary_entries", field="mood") or None,
        source=entry.source,
        coverAttachmentId=entry.cover_attachment_id,
        folderId=entry.folder_id,
        attachments=[
            diary_attachment_to_response(attachment, user_id=user_id)
            for attachment in entry.attachments
        ],
        createdAt=entry.created_at,
        updatedAt=entry.updated_at,
    )


def diary_attachment_to_response(
    attachment: DiaryAttachment,
    *,
    user_id: int,
) -> DiaryAttachmentResponse:
    return DiaryAttachmentResponse(
        id=attachment.id,
        entryId=attachment.entry_id,
        kind=attachment.kind,
        mimeType=attachment.mime_type,
        filename=df(
            user_id,
            attachment.original_filename,
            table="diary_attachments",
            field="original_filename",
        )
        or None,
        caption=df(user_id, attachment.caption, table="diary_attachments", field="caption")
        or None,
        sizeBytes=attachment.size_bytes,
        sha256=attachment.sha256,
        createdAt=attachment.created_at,
        url=f"/api/diary/{attachment.entry_id}/attachments/{attachment.id}",
    )


def _new_storage_path(*, user_id: int, kind: str) -> str:
    token = secrets.token_hex(16)
    return f"users/{user_id}/diary/attachments/{kind}_{token}.bin"


def _resolve_storage_path(storage_path: str) -> Path:
    try:
        data_root = settings.data_dir.resolve()
        path = (settings.data_dir / storage_path).resolve()
        path.relative_to(data_root)
    except (OSError, ValueError) as exc:
        raise DiaryValidationError("Invalid attachment storage path.") from exc
    return path


def _unlink_if_safe(storage_path: str) -> None:
    try:
        path = _resolve_storage_path(storage_path)
    except DiaryValidationError:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _attachment_blob_aad(*, user_id: int, storage_path: str) -> bytes:
    return f"diary_attachments:{user_id}:{storage_path}".encode()


def _normalize_mime_type(value: str) -> str:
    normalized = value.split(";", maxsplit=1)[0].strip().lower()
    return normalized or "application/octet-stream"


def _kind_from_mime_type(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "file"


_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._ -]+")


def _sanitize_filename(filename: str | None) -> str | None:
    if filename is None:
        return None
    name = Path(filename).name.strip()
    if not name:
        return None
    name = _SAFE_FILENAME_RE.sub("_", name)
    return name[:180] or None


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
