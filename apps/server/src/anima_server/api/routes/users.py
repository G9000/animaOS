from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.contracts.auth import UserResponse
from anima_server.db import get_db
from anima_server.schemas.users import DeleteUserResponse, UserUpdateRequest
from anima_server.services.auth import normalize_username
from anima_server.services.corefs.account_profile import (
    AccountProfileAuthorityError,
    read_account_profile_for_session,
    serialize_account_profile,
    update_canonical_account_profile,
)
from anima_server.services.corefs.active_core_registry import schedule_active_core_account_deletion
from anima_server.services.corefs.logical import CoreFsMutationUnavailable
from anima_server.services.corefs.transfer import TransferError
from anima_server.services.sessions import unlock_session_store

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    session = require_unlocked_user(request, user_id)
    try:
        profile = read_account_profile_for_session(session)
    except (AccountProfileAuthorityError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if profile is None:
        raise HTTPException(status_code=409, detail="Canonical account profile is unavailable")
    return serialize_account_profile(profile)


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int, payload: UserUpdateRequest, request: Request, db: Session = Depends(get_db)
) -> dict[str, object]:
    session = require_unlocked_user(request, user_id)
    updates = payload.model_dump(exclude_unset=True)
    username: str | None = None
    display_name: str | None = None
    if "username" in updates:
        username = normalize_username(str(updates["username"]))
        if not username:
            raise HTTPException(status_code=422, detail="Username is required")
    if "name" in updates:
        display_name = str(updates["name"]).strip()
        if not display_name:
            raise HTTPException(status_code=422, detail="Name is required")
    birthday = updates.get("birthday")
    try:
        profile = update_canonical_account_profile(
            session=session,
            username=username,
            display_name=display_name,
            gender=updates.get("gender"),
            gender_present="gender" in updates,
            age=updates.get("age"),
            age_present="age" in updates,
            birthday=birthday.strip() if isinstance(birthday, str) else None,
            birthday_present="birthday" in updates,
        )
    except (AccountProfileAuthorityError, CoreFsMutationUnavailable, ValueError) as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    return serialize_account_profile(profile)


@router.delete("/{user_id}", response_model=DeleteUserResponse)
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    require_unlocked_user(request, user_id)
    try:
        scheduled = schedule_active_core_account_deletion(user_id=user_id)
    except (OSError, TransferError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="corefs_account_delete_schedule_failed"
        ) from exc
    unlock_session_store.revoke_user(user_id)
    return {
        "message": "Whole-Core account deletion scheduled for restart",
        "restartRequired": True,
        "deletionId": scheduled.deletion_id,
    }
