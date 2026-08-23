"""Image asset endpoints."""

from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.db import get_runtime_db
from anima_server.models.runtime import RuntimeImageAsset
from anima_server.schemas.images import ImageRetentionUpdate
from anima_server.services.agent.companion import invalidate_companion
from anima_server.services.corefs.asset_authority import (
    CoreFsByteSource,
    asset_corefs_authority_active,
)
from anima_server.services.corefs.asset_mutations import (
    AssetMutationError,
    trash_canonical_asset,
)
from anima_server.services.corefs.conversation_authority import (
    canonical_message_api_id,
    conversation_corefs_authority_active,
    list_canonical_threads,
)
from anima_server.services.corefs.conversation_mutations import (
    ConversationMutationError,
    edit_canonical_message,
)
from anima_server.services.corefs.image_authority import (
    forget_canonical_image,
    set_canonical_image_retention,
)
from anima_server.services.images.deletion import (
    forget_image_asset,
    remove_message_image_link,
    set_image_retention_state,
)
from anima_server.services.images.store import (
    resolve_image_byte_source,
    resolve_projected_image_byte_source,
)

router = APIRouter(prefix="/api/images", tags=["images"])


@router.delete("/messages/{message_id}/attachments/{attachment_id}")
async def remove_message_image_attachment(
    message_id: int,
    attachment_id: str,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    unlock_session = await require_unlocked_session_async(request)
    if conversation_corefs_authority_active(unlock_session):
        matches = [
            (view, message)
            for view in list_canonical_threads(session=unlock_session)
            for message in view.messages
            if canonical_message_api_id(message) == message_id and message.role == "user"
        ]
        object_uri = f"corefs://object/{attachment_id}"
        if len(matches) != 1 or object_uri not in matches[0][1].attachment_uris:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image link not found",
            )
        view, message = matches[0]
        try:
            edit_canonical_message(
                session=unlock_session,
                thread_id=view.document.thread_id,
                message_id=message.message_id,
                content=message.content,
                expected_event_id=message.current_event_id,
                expected_version=message.version,
                attachment_uris=tuple(
                    uri for uri in message.attachment_uris if uri != object_uri
                ),
            )
        except ConversationMutationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        deleted = False
        with suppress(AssetMutationError, RuntimeError, ValueError):
            deleted = trash_canonical_asset(
                session=unlock_session,
                stable_id=attachment_id,
            )
        invalidate_companion(unlock_session.user_id)
        return {
            "status": "removed",
            "imageAssetId": None,
            "assetDeleted": deleted,
            "fileDeleted": False,
        }
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
) -> Response:
    unlock_session = await require_unlocked_session_async(request)
    try:
        projected = resolve_projected_image_byte_source(
            user_id=unlock_session.user_id,
            image_asset_id=image_asset_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        ) from exc
    if projected is not None:
        return StreamingResponse(
            projected.iter_chunks(),
            media_type=projected.content_type,
        )
    asset = _owned_image_asset(runtime_db, unlock_session.user_id, image_asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    source = resolve_image_byte_source(asset, user_id=unlock_session.user_id)
    if isinstance(source, CoreFsByteSource):
        return StreamingResponse(
            source.iter_chunks(),
            media_type=source.content_type,
        )
    path = source
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
    if asset_corefs_authority_active(unlock_session):
        try:
            forgotten = forget_canonical_image(
                session=unlock_session,
                image_asset_id=image_asset_id,
            )
        except (AssetMutationError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Canonical image mutation failed.",
            ) from exc
        if not forgotten:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image not found",
            )
        invalidate_companion(unlock_session.user_id)
        return {
            "status": "forgotten",
            "imageAssetId": image_asset_id,
            "fileDeleted": False,
        }
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
    if asset_corefs_authority_active(unlock_session):
        try:
            retention_state = set_canonical_image_retention(
                session=unlock_session,
                image_asset_id=image_asset_id,
                retention_state=payload.retentionState,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except (AssetMutationError, RuntimeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Canonical image mutation failed.",
            ) from exc
        if retention_state is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image not found",
            )
        return {
            "status": "updated",
            "imageAssetId": image_asset_id,
            "retentionState": retention_state,
        }
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
