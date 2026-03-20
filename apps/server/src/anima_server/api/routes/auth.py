from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import read_unlock_token
from anima_server.db import get_db
from anima_server.db.user_store import (
    InvalidCredentialsError,
    authenticate_account,
    register_account,
)
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
    change_user_password,
    get_user_by_id,
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
) -> dict[str, object]:
    username = normalize_username(payload.username)
    display_name = payload.name.strip()
    if not username:
        raise HTTPException(status_code=422, detail="Username is required")
    if not display_name:
        raise HTTPException(status_code=422, detail="Name is required")

    try:
        response, deks = register_account(
            username=username,
            password=payload.password,
            display_name=display_name,
            agent_name=payload.agentName,
            user_directive=payload.userDirective,
            relationship=payload.relationship,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Core is already provisioned":
            raise HTTPException(status_code=403, detail=detail) from None
        if detail == "Username already taken":
            raise HTTPException(status_code=409, detail=detail) from None
        raise HTTPException(
            status_code=422, detail=detail) from None
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail=str(exc)) from None

    response["unlockToken"] = unlock_session_store.create(
        int(response["id"]), deks)
    return response


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
) -> dict[str, object]:
    username = normalize_username(payload.username)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    try:
        response, deks = authenticate_account(username, payload.password)
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {
        **response,
        "unlockToken": unlock_session_store.create(int(response["id"]), deks),
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
            current_deks=session.deks,
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Re-wrap SQLCipher key with new password (unified passphrase mode)
    _rewrap_sqlcipher_key_if_unified(payload.newPassword)

    unlock_session_store.revoke_user(user.id)
    new_unlock_token = unlock_session_store.create(user.id, session.deks)
    return {"success": True, "unlockToken": new_unlock_token}


def _rewrap_sqlcipher_key_if_unified(new_password: str) -> None:
    """Re-wrap the SQLCipher key with a new password in unified mode."""
    from anima_server.config import settings
    if settings.core_passphrase.strip():
        return

    from anima_server.services.core import get_wrapped_sqlcipher_key, store_wrapped_sqlcipher_key
    from anima_server.services.sessions import get_sqlcipher_key

    raw_key = get_sqlcipher_key()
    if raw_key is None:
        return

    wrapped_data = get_wrapped_sqlcipher_key()
    if wrapped_data is None:
        return

    from anima_server.services.crypto import wrap_dek

    wrapped = wrap_dek(new_password, raw_key)
    store_wrapped_sqlcipher_key({
        "kdf_salt": wrapped.kdf_salt,
        "kdf_time_cost": wrapped.kdf_time_cost,
        "kdf_memory_cost_kib": wrapped.kdf_memory_cost_kib,
        "kdf_parallelism": wrapped.kdf_parallelism,
        "kdf_key_length": wrapped.kdf_key_length,
        "wrap_iv": wrapped.wrap_iv,
        "wrap_tag": wrapped.wrap_tag,
        "wrapped_key": wrapped.wrapped_dek,
    })
