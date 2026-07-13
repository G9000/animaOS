from __future__ import annotations

from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.db import get_db
from anima_server.schemas.diary import (
    DiaryAttachmentResponse,
    DiaryEntryCreateRequest,
    DiaryEntryResponse,
    DiaryEntryUpdateRequest,
    DiaryFolderCreateRequest,
    DiaryFolderResponse,
    DiaryFolderUpdateRequest,
)
from anima_server.services.diary import (
    DiaryValidationError,
    attach_file_to_entry,
    create_diary_entry,
    create_diary_folder,
    delete_diary_entry,
    delete_diary_folder,
    diary_attachment_to_response,
    diary_entry_to_response,
    diary_folder_to_response,
    list_diary_entries,
    list_diary_folders,
    load_attachment_for_user,
    load_entry_for_user,
    load_folder_for_user,
    read_attachment_blob,
    rename_diary_folder,
    update_diary_entry,
)

router = APIRouter(prefix="/api/diary", tags=["diary"])


@router.get("", response_model=list[DiaryEntryResponse])
async def list_entries(
    request: Request,
    userId: int = Query(ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[DiaryEntryResponse]:
    session = require_unlocked_session(request)
    if session.user_id != userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

    entries = list_diary_entries(db, user_id=userId, limit=limit)
    return [diary_entry_to_response(entry, user_id=userId) for entry in entries]


@router.post("", response_model=DiaryEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_entry(
    payload: DiaryEntryCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> DiaryEntryResponse:
    session = require_unlocked_session(request)
    if session.user_id != payload.userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

    try:
        entry = create_diary_entry(
            db,
            user_id=payload.userId,
            entry_date=payload.entryDate,
            title=payload.title,
            body=payload.body,
            mood=payload.mood,
            folder_id=payload.folderId,
        )
    except DiaryValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return diary_entry_to_response(entry, user_id=payload.userId)


@router.get("/folders", response_model=list[DiaryFolderResponse])
async def list_folders(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
) -> list[DiaryFolderResponse]:
    session = require_unlocked_session(request)
    if session.user_id != userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

    folders = list_diary_folders(db, user_id=userId)
    return [
        diary_folder_to_response(folder, user_id=userId, entry_count=count) for folder, count in folders
    ]


@router.post("/folders", response_model=DiaryFolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: DiaryFolderCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> DiaryFolderResponse:
    session = require_unlocked_session(request)
    if session.user_id != payload.userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

    folder = create_diary_folder(db, user_id=payload.userId, name=payload.name)
    return diary_folder_to_response(folder, user_id=payload.userId, entry_count=0)


@router.patch("/folders/{folder_id}", response_model=DiaryFolderResponse)
async def update_folder(
    folder_id: int,
    payload: DiaryFolderUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> DiaryFolderResponse:
    session = require_unlocked_session(request)
    folder = load_folder_for_user(db, user_id=session.user_id, folder_id=folder_id)
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found.")

    folder = rename_diary_folder(db, folder=folder, name=payload.name)
    counts = list_diary_folders(db, user_id=session.user_id)
    entry_count = next((count for f, count in counts if f.id == folder.id), 0)
    return diary_folder_to_response(folder, user_id=session.user_id, entry_count=entry_count)


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    session = require_unlocked_session(request)
    folder = load_folder_for_user(db, user_id=session.user_id, folder_id=folder_id)
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found.")
    delete_diary_folder(db, folder=folder)
    return {"deleted": True}


@router.patch("/{entry_id}", response_model=DiaryEntryResponse)
async def update_entry(
    entry_id: int,
    payload: DiaryEntryUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> DiaryEntryResponse:
    session = require_unlocked_session(request)
    entry = load_entry_for_user(db, user_id=session.user_id, entry_id=entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diary entry not found.")

    try:
        entry = update_diary_entry(
            db,
            entry=entry,
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
    except DiaryValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return diary_entry_to_response(entry, user_id=session.user_id)


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
    db: Session = Depends(get_db),
) -> DiaryAttachmentResponse:
    session = require_unlocked_session(request)
    entry = load_entry_for_user(db, user_id=session.user_id, entry_id=entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diary entry not found.")

    try:
        attachment = await attach_file_to_entry(
            db,
            user_id=session.user_id,
            entry=entry,
            file=file,
            caption=caption,
        )
    except DiaryValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return diary_attachment_to_response(attachment, user_id=session.user_id)


@router.get("/{entry_id}/attachments/{attachment_id}")
async def download_attachment(
    entry_id: int,
    attachment_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    session = require_unlocked_session(request)
    attachment = load_attachment_for_user(
        db,
        user_id=session.user_id,
        entry_id=entry_id,
        attachment_id=attachment_id,
    )
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found.")

    try:
        blob = read_attachment_blob(user_id=session.user_id, attachment=attachment)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found.") from exc
    except DiaryValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    headers: dict[str, str] = {}
    if blob.filename:
        headers["Content-Disposition"] = (
            "attachment; filename*=UTF-8''" + quote(blob.filename)
        )
    return Response(content=blob.data, media_type=blob.mime_type, headers=headers)


@router.delete("/{entry_id}")
async def delete_entry(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    session = require_unlocked_session(request)
    entry = load_entry_for_user(db, user_id=session.user_id, entry_id=entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diary entry not found.")
    delete_diary_entry(db, entry=entry)
    return {"deleted": True}
