from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.schemas.preferences import (
    PortablePreferencesResponse,
    PortablePreferencesUpdateRequest,
)
from anima_server.services.corefs.preferences import (
    PortablePreferenceError,
    read_portable_preferences,
    update_portable_preferences,
)

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


@router.get("/{user_id}", response_model=PortablePreferencesResponse)
def get_preferences(user_id: int, request: Request) -> PortablePreferencesResponse:
    session = require_unlocked_user(request, user_id)
    try:
        values = read_portable_preferences(session=session)
    except PortablePreferenceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PortablePreferencesResponse(userId=user_id, values=values)


@router.patch("/{user_id}", response_model=PortablePreferencesResponse)
def patch_preferences(
    user_id: int,
    payload: PortablePreferencesUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> PortablePreferencesResponse:
    session = require_unlocked_user(request, user_id)
    marker = getattr(session, "content_authority", None)
    if isinstance(marker, dict) and "preferences" in marker.get("families", []):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "corefs_preference_mutation_not_enabled",
                "message": "CoreFS preference mutation requires the PCF-008 activation adapter.",
            },
        )
    try:
        values = update_portable_preferences(
            session=session,
            db=db,
            values=payload.values,
        )
    except PortablePreferenceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return PortablePreferencesResponse(userId=user_id, values=values)
