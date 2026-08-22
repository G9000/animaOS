from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.db import get_db
from anima_server.models import User
from anima_server.schemas.telegram import (
    TelegramLinkRequest,
    TelegramLinkResponse,
)
from anima_server.services.integration_registry import (
    link_integration,
    lookup_integration,
    migrate_legacy_integration_links,
    unlink_integration,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/link", response_model=TelegramLinkResponse, status_code=201)
def link_telegram(
    payload: TelegramLinkRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TelegramLinkResponse:
    session = require_unlocked_session(request)
    if session.user_id != payload.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session user mismatch.",
        )

    link_secret = os.environ.get("TELEGRAM_LINK_SECRET")
    if link_secret and (not payload.linkSecret or payload.linkSecret != link_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid link secret.",
        )

    user = db.get(User, payload.userId)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {payload.userId} not found.",
        )

    migrate_legacy_integration_links(db, user_id=payload.userId)
    link_integration(
        provider="telegram",
        external_id=str(payload.chatId),
        user_id=payload.userId,
    )

    return TelegramLinkResponse(chatId=payload.chatId, userId=payload.userId)


@router.get("/link", response_model=TelegramLinkResponse)
def lookup_telegram(
    request: Request,
    chatId: int = Query(),
    db: Session = Depends(get_db),
) -> TelegramLinkResponse:
    session = require_unlocked_session(request)
    migrate_legacy_integration_links(db, user_id=session.user_id)
    link = lookup_integration(provider="telegram", external_id=str(chatId))
    if link is None or link.user_id != session.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No link found for this chat.",
        )
    return TelegramLinkResponse(chatId=int(link.external_id), userId=link.user_id)


@router.delete("/link")
def unlink_telegram(
    request: Request,
    chatId: int = Query(),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    session = require_unlocked_session(request)
    migrate_legacy_integration_links(db, user_id=session.user_id)
    link = lookup_integration(provider="telegram", external_id=str(chatId))
    if link is not None and link.user_id == session.user_id:
        unlink_integration(provider="telegram", external_id=str(chatId))

    return {"status": "unlinked"}
