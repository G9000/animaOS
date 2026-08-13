from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.schemas.preferences import (
    PortablePreferencesResponse,
    PortablePreferencesUpdateRequest,
)
from anima_server.services.corefs.logical import CoreFsMutationUnavailable
from anima_server.services.corefs.preferences import (
    PortablePreferenceError,
    portable_preference_corefs_authority_active,
    read_portable_preferences,
    update_canonical_preferences,
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
    if portable_preference_corefs_authority_active(session):
        try:
            values = update_canonical_preferences(session=session, values=payload.values)
        except CoreFsMutationUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": str(exc)},
            ) from exc
        except PortablePreferenceError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return PortablePreferencesResponse(userId=user_id, values=values)
    try:
        values = update_portable_preferences(
            session=session,
            db=db,
            values=payload.values,
        )
    except PortablePreferenceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return PortablePreferencesResponse(userId=user_id, values=values)
