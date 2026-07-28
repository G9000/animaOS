"""Image asset endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.db import get_runtime_db
from anima_server.models.runtime import RuntimeImageAsset
from anima_server.schemas.images import ImageRetentionUpdate
from anima_server.services.agent.companion import invalidate_companion
from anima_server.services.images.deletion import (
    forget_image_asset,
    remove_message_image_link,
    set_image_retention_state,
)
from anima_server.services.images.store import resolve_image_storage_path

router = APIRouter(prefix="/api/images", tags=["images"])


@router.delete("/messages/{message_id}/attachments/{attachment_id}")
async def remove_message_image_attachment(
    message_id: int,
    attachment_id: str,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    unlock_session = await require_unlocked_session_async(request)
    result = remove_message_image_link(
        runtime_db,
        user_id=unlock_session.user_id,
        message_id=message_id,
        attachment_id=attachment_id,
    )
    if not result.removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image link not found")
    runtime_db.commit()
    invalidate_companion(unlock_session.user_id)
    return {
        "status": "removed",
        "imageAssetId": result.image_asset_id,
        "assetDeleted": result.asset_deleted,
        "fileDeleted": result.file_deleted,
    }


@router.get("/{image_asset_id}")
async def get_image_asset(
    image_asset_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> FileResponse:
    unlock_session = await require_unlocked_session_async(request)
    asset = _owned_image_asset(runtime_db, unlock_session.user_id, image_asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    path = resolve_image_storage_path(asset.storage_path, user_id=unlock_session.user_id)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    return FileResponse(path, media_type=asset.mime_type)


@router.delete("/{image_asset_id}")
async def forget_image_asset_endpoint(
    image_asset_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    unlock_session = await require_unlocked_session_async(request)
    result = forget_image_asset(
        runtime_db,
        user_id=unlock_session.user_id,
        image_asset_id=image_asset_id,
    )
    if not result.forgotten:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    runtime_db.commit()
    invalidate_companion(unlock_session.user_id)
    return {
        "status": "forgotten",
        "imageAssetId": image_asset_id,
        "fileDeleted": result.file_deleted,
    }


@router.patch("/{image_asset_id}/retention")
async def update_image_retention(
    image_asset_id: int,
    payload: ImageRetentionUpdate,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    unlock_session = await require_unlocked_session_async(request)
    try:
        asset = set_image_retention_state(
            runtime_db,
            user_id=unlock_session.user_id,
            image_asset_id=image_asset_id,
            retention_state=payload.retentionState,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    runtime_db.commit()
    return {
        "status": "updated",
        "imageAssetId": image_asset_id,
        "retentionState": asset.retention_state,
    }


def _owned_image_asset(
    runtime_db: Session,
    user_id: int,
    image_asset_id: int,
) -> RuntimeImageAsset | None:
    return runtime_db.scalar(
        select(RuntimeImageAsset).where(
            RuntimeImageAsset.id == image_asset_id,
            RuntimeImageAsset.user_id == user_id,
            RuntimeImageAsset.status != "deleted",
        )
    )
