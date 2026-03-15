from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import read_unlock_token
from anima_server.db import get_db
from anima_server.schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    CreateAIChatRequest,
    CreateAIChatResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RegisterRequest,
    RegisterResponse,
    UserResponse,
)
from anima_server.services.auth import (
    authenticate_user,
    change_user_password,
    create_user,
    get_user_by_id,
    get_user_by_username,
    normalize_username,
    serialize_user,
)
from anima_server.services.sessions import unlock_session_store

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/create-ai/chat", response_model=CreateAIChatResponse)
async def create_ai_chat(payload: CreateAIChatRequest) -> dict[str, object]:
    """Handle one turn of the AI creation ceremony."""
    from anima_server.services.agent.llm import LLMConfigError, LLMInvocationError
    from anima_server.services.creation_agent import handle_creation_turn

    llm_messages = [
        {"role": m.role, "content": m.content} for m in payload.messages
    ]

    try:
        result = await handle_creation_turn(llm_messages, payload.ownerName)
    except LLMConfigError:
        raise HTTPException(
            status_code=503, detail="AI provider is not configured."
        ) from None
    except LLMInvocationError as exc:
        raise HTTPException(
            status_code=503, detail=f"AI provider error: {exc}"
        ) from None

    return {
        "message": result.message,
        "done": result.done,
        "soulData": result.soul_data,
    }


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    username = normalize_username(payload.username)
    display_name = payload.name.strip()
    if not username:
        raise HTTPException(status_code=422, detail="Username is required")
    if not display_name:
        raise HTTPException(status_code=422, detail="Name is required")

    existing_user = get_user_by_username(db, username)
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="Username already taken")

    try:
        user, dek = create_user(
            db,
            username=username,
            password=payload.password,
            display_name=display_name,
            agent_name=payload.agentName,
            user_directive=payload.userDirective,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Username already taken") from None

    response = serialize_user(user)
    response["unlockToken"] = unlock_session_store.create(user.id, dek)
    return response


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    username = normalize_username(payload.username)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    try:
        user, dek = authenticate_user(db, username, payload.password)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {
        "id": user.id,
        "username": user.username,
        "name": user.display_name,
        "unlockToken": unlock_session_store.create(user.id, dek),
        "message": "Login successful",
    }


@router.get("/me", response_model=UserResponse)
def me(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = unlock_session_store.resolve(read_unlock_token(request))
    if session is None:
        raise HTTPException(status_code=401, detail="Session locked.")

    user = get_user_by_id(db, session.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return serialize_user(user)


@router.post("/logout", response_model=LogoutResponse)
def logout(request: Request) -> dict[str, bool]:
    unlock_session_store.revoke(read_unlock_token(request))
    return {"success": True}


@router.post("/change-password", response_model=ChangePasswordResponse)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = unlock_session_store.resolve(read_unlock_token(request))
    if session is None:
        raise HTTPException(
            status_code=401, detail="Session locked. Please sign in again.")

    user = get_user_by_id(db, session.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        change_user_password(
            db,
            user,
            old_password=payload.oldPassword,
            new_password=payload.newPassword,
            current_dek=session.dek,
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    unlock_session_store.revoke_user(user.id)
    new_unlock_token = unlock_session_store.create(user.id, session.dek)
    return {"success": True, "unlockToken": new_unlock_token}
