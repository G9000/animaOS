from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.contracts.auth import UserResponse
from anima_server.db import get_db
from anima_server.db.user_store import delete_account_storage, username_exists
from anima_server.schemas.users import DeleteUserResponse, UserUpdateRequest
from anima_server.services.auth import get_user_by_id, normalize_username, serialize_user
from anima_server.services.corefs.account_profile import (
    AccountProfileAuthorityError,
    account_profile_corefs_authority_active,
    read_account_profile_for_session,
    serialize_account_profile,
    update_canonical_account_profile,
)
from anima_server.services.corefs.active_core_registry import (
    schedule_active_core_account_deletion,
)
from anima_server.services.corefs.logical import CoreFsMutationUnavailable
from anima_server.services.corefs.transfer import TransferError
from anima_server.services.corefs.writing_source import prepare_writing_source_catalog
from anima_server.services.sessions import unlock_session_store

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = require_unlocked_user(request, user_id)
    if account_profile_corefs_authority_active(session):
        try:
            profile = read_account_profile_for_session(session)
        except (AccountProfileAuthorityError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if profile is None:
            raise HTTPException(status_code=409, detail="Canonical account profile is unavailable")
        return serialize_account_profile(profile)
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return serialize_user(user)


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = require_unlocked_user(request, user_id)
    updates = payload.model_dump(exclude_unset=True)
    if account_profile_corefs_authority_active(session):
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
                birthday=(birthday.strip() if isinstance(birthday, str) else None),
                birthday_present="birthday" in updates,
            )
        except (AccountProfileAuthorityError, CoreFsMutationUnavailable, ValueError) as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
        return serialize_account_profile(profile)

    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if "username" in updates:
        username = normalize_username(str(updates["username"]))
        if not username:
            raise HTTPException(status_code=422, detail="Username is required")
        if username_exists(username, exclude_user_id=user_id):
            raise HTTPException(status_code=409, detail="Username already taken")
        user.username = username
    if "name" in updates:
        display_name = str(updates["name"]).strip()
        if not display_name:
            raise HTTPException(status_code=422, detail="Name is required")
        user.display_name = display_name
    if "gender" in updates:
        user.gender = updates["gender"]
    if "age" in updates:
        user.age = updates["age"]
    if "birthday" in updates:
        birthday = updates["birthday"]
        user.birthday = birthday.strip() if isinstance(birthday, str) else None
    user.updated_at = datetime.now(UTC)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already taken") from None

    db.refresh(user)
    try:
        prepare_writing_source_catalog(session=session, db=db)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "corefs_account_shadow_validation_failed"},
        ) from exc
    return serialize_user(user)


@router.delete("/{user_id}", response_model=DeleteUserResponse)
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = require_unlocked_user(request, user_id)
    if account_profile_corefs_authority_active(session):
        try:
            scheduled = schedule_active_core_account_deletion(user_id=user_id)
        except (OSError, TransferError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="corefs_account_delete_schedule_failed",
            ) from exc
        unlock_session_store.revoke_user(user_id)
        return {
            "message": "Whole-Core account deletion scheduled for restart",
            "restartRequired": True,
            "deletionId": scheduled.deletion_id,
        }
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    unlock_session_store.revoke_user(user_id)
    db.close()
    delete_account_storage(user_id)
    return {"message": "User deleted", "restartRequired": False, "deletionId": None}
