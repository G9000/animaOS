from __future__ import annotations

import shutil
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.schemas.auth import UserResponse
from anima_server.schemas.users import DeleteUserResponse, UserUpdateRequest
from anima_server.services.auth import get_user_by_id, normalize_username, serialize_user
from anima_server.services.sessions import unlock_session_store
from anima_server.services.storage import get_user_data_dir

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    require_unlocked_user(request, user_id)
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
    require_unlocked_user(request, user_id)
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    updates = payload.model_dump(exclude_unset=True)
    if "username" in updates:
        username = normalize_username(str(updates["username"]))
        if not username:
            raise HTTPException(status_code=422, detail="Username is required")
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
    return serialize_user(user)


@router.delete("/{user_id}", response_model=DeleteUserResponse)
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    require_unlocked_user(request, user_id)
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    db.delete(user)
    db.commit()
    unlock_session_store.revoke_user(user_id)
    shutil.rmtree(get_user_data_dir(user_id), ignore_errors=True)
    return {"message": "User deleted"}
