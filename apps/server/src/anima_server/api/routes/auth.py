from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import read_unlock_token
from anima_server.contracts.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    ConfirmCorefsRecoveryCredentialRequest,
    ConfirmRecoveryCredentialRequest,
    ConfirmRecoveryCredentialResponse,
    CorefsChangePasswordRequest,
    CorefsCredentialResponse,
    CreateAIChatRequest,
    CreateAIChatResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    PrepareCorefsRecoveryCredentialRequest,
    PrepareRecoveryCredentialRequest,
    PrepareRecoveryCredentialResponse,
    RecoverRequest,
    RecoverResponse,
    RegisterRequest,
    RegisterResponse,
    UserResponse,
)
from anima_server.db import dispose_all_user_engines, get_db
from anima_server.db.session import get_user_session_factory
from anima_server.db.user_store import (
    InvalidCredentialsError,
    authenticate_account,
    recover_account_with_phrase,
    register_account,
)
from anima_server.services.auth import (
    get_user_by_id,
    normalize_username,
    serialize_user,
)
from anima_server.services.corefs.account_profile import (
    read_account_profile_for_session,
    serialize_account_profile,
)
from anima_server.services.corefs.admission import (
    FsCredentialAdmission,
    FsCredentialAdmissionRejected,
)
from anima_server.services.corefs.credentials import (
    change_account_password_credential,
    change_filesystem_password_credential,
    confirm_filesystem_recovery_credential,
    confirm_recovery_credential,
    prepare_filesystem_recovery_credential,
    prepare_recovery_credential,
)
from anima_server.services.corefs.legacy_soul import migrate_legacy_soul_file
from anima_server.services.corefs.types import PayloadScope
from anima_server.services.integration_registry import migrate_legacy_integration_links
from anima_server.services.regeneration_work import migrate_legacy_regeneration_flags
from anima_server.services.sessions import unlock_session_store

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_FAILED_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_LIMIT_WINDOW_SECONDS = 60.0
_FS_CREDENTIAL_ADMISSION = FsCredentialAdmission()


def _migrate_legacy_device_state(user_id: int) -> None:
    with get_user_session_factory(user_id)() as db:
        migrate_legacy_integration_links(db, user_id=user_id)
        migrate_legacy_soul_file(db, user_id=user_id)
        migrate_legacy_regeneration_flags(db, user_id=user_id)


def _prune_failed_login_attempts(now: float) -> None:
    stale_before = now - _LOGIN_RATE_LIMIT_WINDOW_SECONDS
    for username, attempts in list(_FAILED_LOGIN_ATTEMPTS.items()):
        recent_attempts = [ts for ts in attempts if ts > stale_before]
        if recent_attempts:
            _FAILED_LOGIN_ATTEMPTS[username] = recent_attempts
        else:
            _FAILED_LOGIN_ATTEMPTS.pop(username, None)


def _get_login_retry_after(username: str, now: float) -> int | None:
    _prune_failed_login_attempts(now)
    attempts = _FAILED_LOGIN_ATTEMPTS.get(username, [])
    if len(attempts) < _LOGIN_RATE_LIMIT:
        return None
    retry_after = attempts[0] + _LOGIN_RATE_LIMIT_WINDOW_SECONDS - now
    return max(1, math.ceil(retry_after))


def _record_failed_login_attempt(username: str, now: float) -> int | None:
    attempts = _FAILED_LOGIN_ATTEMPTS.setdefault(username, [])
    attempts.append(now)
    return _get_login_retry_after(username, now)


def _rate_limited_login_response(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": "Too many failed login attempts. Try again later."},
        headers={"Retry-After": str(retry_after)},
    )


@contextmanager
def _admit_fs_credential_work(request: Request) -> Iterator[None]:
    client_id = request.client.host if request.client is not None else "unknown"
    try:
        with _FS_CREDENTIAL_ADMISSION.admit(client_id):
            yield
    except FsCredentialAdmissionRejected as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many filesystem credential attempts. Try again later.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None


@router.post("/create-ai/chat", response_model=CreateAIChatResponse)
async def create_ai_chat(payload: CreateAIChatRequest) -> dict[str, object]:
    """Handle one turn of the AI creation ceremony."""
    from anima_server.services.agent.llm import LLMConfigError, LLMInvocationError
    from anima_server.services.creation_agent import handle_creation_turn

    llm_messages = [{"role": m.role, "content": m.content} for m in payload.messages]

    try:
        result = await handle_creation_turn(llm_messages, payload.ownerName)
    except LLMConfigError:
        raise HTTPException(status_code=503, detail="AI provider is not configured.") from None
    except LLMInvocationError as exc:
        logger.exception("AI provider invocation failed", exc_info=exc)
        raise HTTPException(status_code=503, detail="AI provider error occurred") from None

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
        response, deks, recovery_phrase, corefs_keys = register_account(
            username=username,
            password=payload.password,
            display_name=display_name,
            agent_name=payload.agentName,
            user_directive=payload.userDirective,
            relationship=payload.relationship,
            persona_template=payload.personaTemplate,
            agent_type=payload.agentType,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Core is already provisioned":
            raise HTTPException(status_code=403, detail=detail) from None
        if detail == "Username already taken":
            raise HTTPException(status_code=409, detail=detail) from None
        raise HTTPException(status_code=422, detail=detail) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    response["unlockToken"] = unlock_session_store.create(
        int(response["id"]),
        deks,
        corefs_keys=corefs_keys,
    )
    response["recoveryPhrase"] = recovery_phrase
    return response


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
) -> dict[str, object]:
    username = normalize_username(payload.username)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    now = time.time()
    retry_after = _get_login_retry_after(username, now)
    if retry_after is not None:
        return _rate_limited_login_response(retry_after)

    try:
        response, deks, corefs_keys = authenticate_account(username, payload.password)
    except InvalidCredentialsError as exc:
        logger.warning("Login failed for %s: %s", username, exc)
        retry_after = _record_failed_login_attempt(username, now)
        if retry_after is not None:
            return _rate_limited_login_response(retry_after)
        raise HTTPException(status_code=401, detail="Invalid credentials") from None

    _FAILED_LOGIN_ATTEMPTS.pop(username, None)
    user_id = int(response["id"])
    token = unlock_session_store.create(
        user_id,
        deks,
        corefs_keys=corefs_keys,
    )
    try:
        _migrate_legacy_device_state(user_id)
    except Exception:
        unlock_session_store.revoke(token)
        raise
    return {**response, "unlockToken": token, "message": "Login successful"}


@router.get("/me", response_model=UserResponse)
def me(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = unlock_session_store.resolve(read_unlock_token(request))
    if session is None:
        raise HTTPException(status_code=401, detail="Session locked.")

    _migrate_legacy_device_state(session.user_id)

    profile = read_account_profile_for_session(session)
    if profile is not None:
        return serialize_account_profile(profile)

    user = get_user_by_id(db, session.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return serialize_user(user)


@router.post("/logout", response_model=LogoutResponse)
async def logout(request: Request) -> dict[str, bool]:
    token = read_unlock_token(request)

    def detach_and_destroy() -> None:
        if unlock_session_store.revoke_and_clear_sqlcipher_key_if_idle(token):
            dispose_all_user_engines()

    await asyncio.to_thread(detach_and_destroy)
    return {"success": True}


@router.post("/change-password", response_model=ChangePasswordResponse)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = unlock_session_store.resolve(read_unlock_token(request))
    if session is None:
        raise HTTPException(status_code=401, detail="Session locked. Please sign in again.")

    user = get_user_by_id(db, session.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    scope = PayloadScope(payload.scope)
    try:
        change_account_password_credential(
            db,
            user,
            old_password=payload.oldPassword,
            new_password=payload.newPassword,
            current_deks=session.deks,
            scope=scope,
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid credentials") from None

    new_unlock_token = unlock_session_store.replace_user(
        user.id,
        session.deks,
        corefs_keys=session.corefs_keys if scope is PayloadScope.FULL else None,
    )
    return {"success": True, "unlockToken": new_unlock_token}


@router.post("/corefs/change-password", response_model=CorefsCredentialResponse)
def change_corefs_password(
    payload: CorefsChangePasswordRequest,
    request: Request,
) -> dict[str, object]:
    with _admit_fs_credential_work(request):
        try:
            change_filesystem_password_credential(
                current_password=payload.currentPassword,
                new_password=payload.newPassword,
            )
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from None
    return {"success": True, "scope": "fs"}


@router.post(
    "/corefs/recovery-credential/prepare",
    response_model=PrepareRecoveryCredentialResponse,
)
def prepare_corefs_recovery(
    payload: PrepareCorefsRecoveryCredentialRequest,
    request: Request,
) -> dict[str, object]:
    with _admit_fs_credential_work(request):
        try:
            prepared = prepare_filesystem_recovery_credential(
                current_password=payload.currentPassword,
                current_recovery_phrase=payload.currentRecoveryPhrase.strip().lower(),
                replace_pending=payload.replacePending,
            )
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from None
    return {
        "success": True,
        "recoveryPhrase": prepared.recovery_phrase,
        "pendingGeneration": prepared.pending_generation,
        "scope": "fs",
    }


@router.post(
    "/corefs/recovery-credential/confirm",
    response_model=CorefsCredentialResponse,
)
def confirm_corefs_recovery(
    payload: ConfirmCorefsRecoveryCredentialRequest,
    request: Request,
) -> dict[str, object]:
    with _admit_fs_credential_work(request):
        try:
            confirm_filesystem_recovery_credential(
                recovery_phrase=payload.recoveryPhrase.strip().lower(),
                pending_generation=payload.pendingGeneration,
            )
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from None
    return {"success": True, "scope": "fs"}


@router.post("/recover", response_model=RecoverResponse)
def recover(payload: RecoverRequest) -> dict[str, object]:
    if PayloadScope(payload.scope) is PayloadScope.FS:
        return JSONResponse(
            status_code=422,
            content={"detail": "CoreFS-only recovery cannot start an agent session"},
        )
    phrase = payload.recoveryPhrase.strip().lower()
    if not phrase:
        raise HTTPException(status_code=422, detail="Recovery phrase is required")

    try:
        response, deks, corefs_keys = recover_account_with_phrase(
            phrase,
            payload.newPassword,
            scope=PayloadScope(payload.scope),
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None

    return {
        **response,
        "unlockToken": unlock_session_store.create(
            int(response["id"]),
            deks,
            corefs_keys=corefs_keys,
        ),
        "message": "Account recovered successfully",
    }


@router.post(
    "/recovery-credential/prepare",
    response_model=PrepareRecoveryCredentialResponse,
)
def prepare_recovery(
    payload: PrepareRecoveryCredentialRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = unlock_session_store.resolve(read_unlock_token(request))
    if session is None:
        raise HTTPException(status_code=401, detail="Session locked. Please sign in again.")
    user = get_user_by_id(db, session.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        prepared = prepare_recovery_credential(
            db,
            user,
            current_recovery_phrase=payload.currentRecoveryPhrase.strip().lower(),
            current_password=payload.currentPassword,
            scope=PayloadScope(payload.scope),
            replace_pending=payload.replacePending,
        )
    except InvalidTag:
        raise HTTPException(status_code=401, detail="Invalid recovery phrase") from None
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    return {
        "success": True,
        "recoveryPhrase": prepared.recovery_phrase,
        "pendingGeneration": prepared.pending_generation,
        "scope": prepared.scope.value,
    }


@router.post(
    "/recovery-credential/confirm",
    response_model=ConfirmRecoveryCredentialResponse,
)
def confirm_recovery(
    payload: ConfirmRecoveryCredentialRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = unlock_session_store.resolve(read_unlock_token(request))
    if session is None:
        raise HTTPException(status_code=401, detail="Session locked. Please sign in again.")
    user = get_user_by_id(db, session.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        confirm_recovery_credential(
            db,
            user,
            recovery_phrase=payload.recoveryPhrase.strip().lower(),
            pending_generation=payload.pendingGeneration,
            scope=PayloadScope(payload.scope),
            current_password=payload.currentPassword,
        )
    except InvalidTag:
        raise HTTPException(status_code=401, detail="Invalid recovery phrase") from None
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    return {"success": True}
