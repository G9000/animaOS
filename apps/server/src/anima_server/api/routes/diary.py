from __future__ import annotations

import hashlib
import re
import secrets
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.config import settings
from anima_server.schemas.diary import (
    DiaryAttachmentResponse,
    DiaryCorefsPreparedResponse,
    DiaryEntryCreateRequest,
    DiaryEntryResponse,
    DiaryEntryUpdateRequest,
    DiaryFolderCreateRequest,
    DiaryFolderResponse,
    DiaryFolderUpdateRequest,
)
from anima_server.services.corefs.asset_authority import CoreFsSourceError
from anima_server.services.corefs.asset_mutations import (
    AssetMutationError,
    trash_canonical_asset,
    upsert_canonical_binary_asset,
)
from anima_server.services.corefs.catalog_reader import (
    CatalogReadError,
    resolve_prepared_role,
    stable_opaque_id,
)
from anima_server.services.corefs.formats import CoreFormatError
from anima_server.services.corefs.writing_authority import (
    CanonicalDiaryEntry,
    CanonicalWritingCatalog,
    find_canonical_entry,
    find_canonical_folder,
    read_canonical_writing_catalog,
    writing_corefs_authority_active,
)
from anima_server.services.corefs.writing_mutations import (
    WritingMutationError,
    attach_canonical_diary_asset,
    create_canonical_diary_entry,
    create_canonical_diary_folder,
    delete_canonical_diary_entry,
    delete_canonical_diary_folder,
    rename_canonical_diary_folder,
    update_canonical_diary_entry,
)

router = APIRouter(prefix="/api/diary", tags=["diary"])


@router.get("/corefs-prepared", response_model=DiaryCorefsPreparedResponse)
async def corefs_prepared(request: Request) -> DiaryCorefsPreparedResponse:
    session = await require_unlocked_session_async(request)
    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    try:
        journal = resolve_prepared_role(session=session, role="core.journal")
        notes = resolve_prepared_role(session=session, role="core.notes")
    except CatalogReadError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if journal.get("generation") != notes.get("generation") or journal.get(
        "catalogHash"
    ) != notes.get("catalogHash"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Prepared writing roles do not share one authenticated head.",
        )
    return DiaryCorefsPreparedResponse(
        generation=int(journal["generation"]),
        catalogHash=str(journal["catalogHash"]),
        journalStableId=str(journal["stableId"]),
        notesStableId=str(notes["stableId"]),
        authoritative=writing_corefs_authority_active(session),
    )


@router.get("", response_model=list[DiaryEntryResponse])
async def list_entries(
    request: Request,
    userId: int = Query(ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DiaryEntryResponse]:
    session = await require_unlocked_session_async(request)
    if session.user_id != userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    catalog = read_canonical_writing_catalog(session=session)
    return [
        _canonical_entry_response(catalog, record, user_id=userId)
        for record in catalog.entries[:limit]
    ]


@router.post("", response_model=DiaryEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_entry(
    payload: DiaryEntryCreateRequest,
    request: Request,
) -> DiaryEntryResponse:
    session = await require_unlocked_session_async(request)
    if session.user_id != payload.userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    try:
        record = create_canonical_diary_entry(
            session=session,
            entry_date=payload.entryDate,
            title=payload.title,
            body=payload.body,
            mood=payload.mood,
            folder_id=payload.folderId,
        )
        catalog = read_canonical_writing_catalog(session=session)
    except (CoreFormatError, WritingMutationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _canonical_entry_response(catalog, record, user_id=payload.userId)


@router.get("/folders", response_model=list[DiaryFolderResponse])
async def list_folders(
    request: Request,
    userId: int = Query(ge=0),
) -> list[DiaryFolderResponse]:
    session = await require_unlocked_session_async(request)
    if session.user_id != userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    catalog = read_canonical_writing_catalog(session=session)
    counts: dict[str, int] = {}
    for entry in catalog.entries:
        if entry.document.folder_id is not None:
            counts[entry.document.folder_id] = counts.get(entry.document.folder_id, 0) + 1
    return [
        DiaryFolderResponse(
            id=folder.legacy_id,
            userId=userId,
            name=folder.name,
            entryCount=counts.get(folder.stable_id, 0),
            createdAt=folder.created_at,
        )
        for folder in catalog.folders
    ]


@router.post("/folders", response_model=DiaryFolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: DiaryFolderCreateRequest,
    request: Request,
) -> DiaryFolderResponse:
    session = await require_unlocked_session_async(request)
    if session.user_id != payload.userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    try:
        folder = create_canonical_diary_folder(session=session, name=payload.name)
    except WritingMutationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return DiaryFolderResponse(
        id=folder.legacy_id,
        userId=payload.userId,
        name=folder.name,
        entryCount=0,
        createdAt=folder.created_at,
    )


@router.patch("/folders/{folder_id}", response_model=DiaryFolderResponse)
async def update_folder(
    folder_id: int,
    payload: DiaryFolderUpdateRequest,
    request: Request,
) -> DiaryFolderResponse:
    session = await require_unlocked_session_async(request)
    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    try:
        folder = rename_canonical_diary_folder(
            session=session,
            folder_id=folder_id,
            name=payload.name,
        )
    except WritingMutationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found.")
    catalog = read_canonical_writing_catalog(session=session)
    count = sum(entry.document.folder_id == folder.stable_id for entry in catalog.entries)
    return DiaryFolderResponse(
        id=folder.legacy_id,
        userId=session.user_id,
        name=payload.name,
        entryCount=count,
        createdAt=folder.created_at,
    )


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: int,
    request: Request,
) -> dict[str, bool]:
    session = await require_unlocked_session_async(request)
    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    try:
        deleted = delete_canonical_diary_folder(session=session, folder_id=folder_id)
    except WritingMutationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found.")
    return {"deleted": True}


@router.patch("/{entry_id}", response_model=DiaryEntryResponse)
async def update_entry(
    entry_id: int,
    payload: DiaryEntryUpdateRequest,
    request: Request,
) -> DiaryEntryResponse:
    session = await require_unlocked_session_async(request)
    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    try:
        record = update_canonical_diary_entry(
            session=session,
            entry_id=entry_id,
            entry_date=payload.entryDate,
            title=payload.title,
            body=payload.body,
            mood=payload.mood,
            cover_attachment_id=payload.coverAttachmentId,
            folder_id=payload.folderId,
            clear_title=payload.clearTitle,
            clear_mood=payload.clearMood,
            clear_cover=payload.clearCover,
            clear_folder=payload.clearFolder,
        )
        catalog = read_canonical_writing_catalog(session=session)
    except (CoreFormatError, WritingMutationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diary entry not found.",
        )
    return _canonical_entry_response(catalog, record, user_id=session.user_id)


@router.post(
    "/{entry_id}/attachments",
    response_model=DiaryAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    entry_id: int,
    request: Request,
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
) -> DiaryAttachmentResponse:
    session = await require_unlocked_session_async(request)
    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    catalog = read_canonical_writing_catalog(session=session)
    entry = find_canonical_entry(catalog, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diary entry not found.",
        )
    data = await file.read(settings.diary_attachment_max_size_bytes + 1)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attachment file is empty.",
        )
    if len(data) > settings.diary_attachment_max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Attachment is too large. Limit is "
                f"{settings.diary_attachment_max_size_bytes} bytes."
            ),
        )
    mime_type = (file.content_type or "application/octet-stream").split(";", 1)[0]
    mime_type = mime_type.strip().lower() or "application/octet-stream"
    kind = _canonical_attachment_kind(mime_type)
    filename = _canonical_attachment_filename(file.filename)
    cleaned_caption = caption.strip() if caption is not None and caption.strip() else None
    digest = hashlib.sha256(data).hexdigest()
    existing_ids = {
        value.get("legacyId")
        for record in catalog.entries
        for value in record.document.attachment_metadata
    }
    attachment_id = _new_diary_attachment_id(existing_ids)
    stable_id = stable_opaque_id("diary-attachment", str(attachment_id))
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        upsert_canonical_binary_asset(
            session=session,
            stable_id=stable_id,
            name=filename or stable_id,
            object_kind="attachment",
            content_type=mime_type,
            data=data,
        )
        record = attach_canonical_diary_asset(
            session=session,
            entry_id=entry_id,
            attachment_id=attachment_id,
            stable_id=stable_id,
            kind=kind,
            mime_type=mime_type,
            filename=filename,
            caption=cleaned_caption,
            size_bytes=len(data),
            sha256=digest,
            created_at=created_at,
        )
    except (AssetMutationError, CoreFsSourceError, WritingMutationError) as exc:
        with suppress(AssetMutationError, CoreFsSourceError, ValueError):
            trash_canonical_asset(session=session, stable_id=stable_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if record is None:
        with suppress(AssetMutationError, CoreFsSourceError, ValueError):
            trash_canonical_asset(session=session, stable_id=stable_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diary entry not found.",
        )
    refreshed = read_canonical_writing_catalog(session=session)
    metadata = next(
        item
        for item in record.document.attachment_metadata
        if item.get("legacyId") == attachment_id
    )
    return _canonical_attachment_response(
        refreshed,
        entry_id=entry_id,
        metadata=metadata,
    )


@router.get("/{entry_id}/attachments/{attachment_id}")
async def download_attachment(
    entry_id: int,
    attachment_id: int,
    request: Request,
) -> Response:
    session = await require_unlocked_session_async(request)
    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    catalog = read_canonical_writing_catalog(session=session)
    entry = find_canonical_entry(catalog, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found.",
        )
    metadata = next(
        (
            item
            for item in entry.document.attachment_metadata
            if item.get("legacyId") == attachment_id
        ),
        None,
    )
    stable_id = metadata.get("stableId") if isinstance(metadata, dict) else None
    item = catalog.objects_by_stable_id.get(stable_id) if isinstance(stable_id, str) else None
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found.",
        )
    from anima_server.services.corefs.catalog_reader import read_prepared_writing_body

    body = read_prepared_writing_body(
        session=session,
        item=item,
        selected=catalog.selection.snapshot,
    )
    filename = metadata.get("filename") if isinstance(metadata, dict) else None
    headers: dict[str, str] = {}
    if isinstance(filename, str) and filename:
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(filename)
    return Response(content=body, media_type=item.content_type, headers=headers)


@router.delete("/{entry_id}")
async def delete_entry(
    entry_id: int,
    request: Request,
) -> dict[str, bool]:
    session = await require_unlocked_session_async(request)
    if not writing_corefs_authority_active(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_writing_authority_required"},
        )
    try:
        deleted = delete_canonical_diary_entry(session=session, entry_id=entry_id)
    except WritingMutationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diary entry not found.",
        )
    return {"deleted": True}


def _canonical_entry_response(
    catalog: CanonicalWritingCatalog,
    record: CanonicalDiaryEntry,
    *,
    user_id: int,
) -> DiaryEntryResponse:
    document = record.document
    entry_id = document.legacy_id
    if isinstance(entry_id, bool) or not isinstance(entry_id, int):
        raise WritingMutationError("Canonical diary entry has no API identity.")
    folder = (
        find_canonical_folder(catalog, document.folder_id)
        if document.folder_id is not None
        else None
    )
    attachments = [
        _canonical_attachment_response(
            catalog,
            entry_id=entry_id,
            metadata=metadata,
        )
        for metadata in document.attachment_metadata
    ]
    cover_id = None
    if document.cover_uri is not None:
        cover_stable_id = document.cover_uri.rsplit("/", 1)[-1]
        cover_id = next(
            (
                item.id
                for item in attachments
                if isinstance(item.id, int)
                and any(
                    metadata.get("legacyId") == item.id
                    and metadata.get("stableId") == cover_stable_id
                    for metadata in document.attachment_metadata
                )
            ),
            None,
        )
    return DiaryEntryResponse(
        id=entry_id,
        userId=user_id,
        entryDate=document.entry_date,
        title=document.title,
        body=document.html,
        mood=document.mood,
        source=document.source or "user",
        coverAttachmentId=cover_id,
        folderId=folder.legacy_id if folder is not None else None,
        attachments=attachments,
        createdAt=document.created_at,
        updatedAt=document.updated_at,
    )


def _canonical_attachment_response(
    catalog: CanonicalWritingCatalog,
    *,
    entry_id: int,
    metadata: dict[str, object],
) -> DiaryAttachmentResponse:
    attachment_id = metadata.get("legacyId")
    stable_id = metadata.get("stableId")
    if (
        isinstance(attachment_id, bool)
        or not isinstance(attachment_id, int)
        or not isinstance(stable_id, str)
    ):
        raise WritingMutationError("Canonical diary attachment identity is invalid.")
    item = catalog.objects_by_stable_id.get(stable_id)
    if item is None:
        raise WritingMutationError("Canonical diary attachment is unavailable.")
    kind = metadata.get("kind")
    mime_type = metadata.get("mimeType")
    digest = metadata.get("sha256")
    if not isinstance(kind, str) or not isinstance(mime_type, str) or not isinstance(digest, str):
        raise WritingMutationError("Canonical diary attachment metadata is invalid.")
    return DiaryAttachmentResponse(
        id=attachment_id,
        entryId=entry_id,
        kind=kind,
        mimeType=mime_type,
        filename=metadata.get("filename") if isinstance(metadata.get("filename"), str) else None,
        caption=metadata.get("caption") if isinstance(metadata.get("caption"), str) else None,
        sizeBytes=item.body_length,
        sha256=digest,
        createdAt=(
            metadata.get("createdAt") if isinstance(metadata.get("createdAt"), str) else None
        ),
        url=f"/api/diary/{entry_id}/attachments/{attachment_id}",
    )


def _new_diary_attachment_id(existing: set[object]) -> int:
    while True:
        candidate = secrets.randbelow((1 << 52) - 1) + 1
        if candidate not in existing:
            return candidate


def _canonical_attachment_kind(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "file"


_SAFE_ATTACHMENT_NAME_RE = re.compile(r"[^a-zA-Z0-9._ -]+")


def _canonical_attachment_filename(filename: str | None) -> str | None:
    if filename is None:
        return None
    name = _SAFE_ATTACHMENT_NAME_RE.sub("_", Path(filename).name.strip())
    return name[:180] or None
