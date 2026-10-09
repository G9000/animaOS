"""Image asset endpoints."""

from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.db import get_runtime_db
from anima_server.schemas.images import ImageRetentionUpdate
from anima_server.services.agent.companion import invalidate_companion
from anima_server.services.corefs.asset_authority import (
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
from anima_server.services.images.store import (
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
    if not conversation_corefs_authority_active(unlock_session):
        raise HTTPException(status_code=409, detail="Current CoreFS is unavailable.")
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
            attachment_uris=tuple(uri for uri in message.attachment_uris if uri != object_uri),
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
    raise HTTPException(status_code=404, detail="Image not found")


@router.delete("/{image_asset_id}")
async def forget_image_asset_endpoint(
    image_asset_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    unlock_session = await require_unlocked_session_async(request)
    if not asset_corefs_authority_active(unlock_session):
        raise HTTPException(status_code=409, detail="Current CoreFS is unavailable.")
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


@router.patch("/{image_asset_id}/retention")
async def update_image_retention(
    image_asset_id: int,
    payload: ImageRetentionUpdate,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    unlock_session = await require_unlocked_session_async(request)
    if not asset_corefs_authority_active(unlock_session):
        raise HTTPException(status_code=409, detail="Current CoreFS is unavailable.")
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
