from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, status

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.api.routes.credentials import _authorize_broker, _validate_mod_audience
from anima_server.config import settings
from anima_server.schemas.corefs_access import (
    CoreFsClientAccessResponse,
    CoreFsClientCapabilityIssueRequest,
    CoreFsClientCapabilityIssueResponse,
    CoreFsClientInstallationResponse,
    CoreFsGrantFolderResponse,
    CoreFsGrantUpdateRequest,
    CoreFsInstallationApprovalRequest,
)
from anima_server.services.core import get_core_id
from anima_server.services.corefs.client_access import (
    ClientAccessError,
    ClientReapprovalRequired,
    CoreFsFolderGrantTarget,
    approve_installation,
    client_capability_broker,
    list_corefs_grant_folders,
    public_registry,
    revoke_installation,
    set_folder_grant,
)
from anima_server.services.sessions import UnlockSession, active_unlock_sessions

router = APIRouter(prefix="/api/corefs/access", tags=["corefs-access"])
_BROKER_HEADER = "x-anima-credential-broker"


def _folder_inventory(session: UnlockSession) -> tuple[CoreFsFolderGrantTarget, ...]:
    try:
        return list_corefs_grant_folders(session)
    except ClientAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_folder_inventory_unavailable", "message": str(exc)},
        ) from exc


def _access_response(session: UnlockSession) -> CoreFsClientAccessResponse:
    installations = public_registry()
    folders = _folder_inventory(session)
    installed_client_ids = {
        item["clientId"] for item in installations if item["status"] != "revoked"
    }
    transferred_client_ids = {
        role.split(":", 2)[1]
        for folder in folders
        if (role := folder.role) is not None
        and role.startswith("client:")
        and len(role.split(":", 2)) == 3
    }
    return CoreFsClientAccessResponse(
        coreId=get_core_id(),
        localInstanceId=Path(settings.runtime_instance_data_dir).name,
        reapprovalRequiredAfterTransfer=bool(transferred_client_ids - installed_client_ids),
        installations=[
            CoreFsClientInstallationResponse.model_validate(item) for item in installations
        ],
        folders=[
            CoreFsGrantFolderResponse(stableId=item.stable_id, path=item.path, role=item.role)
            for item in folders
        ],
    )


@router.get("", response_model=CoreFsClientAccessResponse)
def get_client_access(request: Request) -> CoreFsClientAccessResponse:
    return _access_response(require_unlocked_session(request))


@router.post(
    "/installations/{installation_id}/approve",
    response_model=CoreFsClientAccessResponse,
)
def approve_client_installation(
    installation_id: str,
    payload: CoreFsInstallationApprovalRequest,
    request: Request,
) -> CoreFsClientAccessResponse:
    session = require_unlocked_session(request)
    try:
        approve_installation(installation_id, confirmed=payload.confirmed)
    except ClientAccessError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _access_response(session)


@router.put(
    "/installations/{installation_id}/grants/{folder_stable_id}",
    response_model=CoreFsClientAccessResponse,
)
def update_client_grant(
    installation_id: str,
    folder_stable_id: str,
    payload: CoreFsGrantUpdateRequest,
    request: Request,
) -> CoreFsClientAccessResponse:
    session = require_unlocked_session(request)
    folders = _folder_inventory(session)
    if folder_stable_id not in {folder.stable_id for folder in folders}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found.")
    try:
        set_folder_grant(
            installation_id,
            folder_stable_id=folder_stable_id,
            scope=payload.scope,
            confirmed=payload.confirmed,
        )
    except ClientReapprovalRequired as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ClientAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return _access_response(session)


@router.delete(
    "/installations/{installation_id}",
    response_model=CoreFsClientAccessResponse,
)
def revoke_client_installation(
    installation_id: str,
    request: Request,
) -> CoreFsClientAccessResponse:
    session = require_unlocked_session(request)
    try:
        revoke_installation(installation_id)
    except ClientAccessError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _access_response(session)


@router.post("/capabilities", response_model=CoreFsClientCapabilityIssueResponse)
async def issue_client_capability(
    payload: CoreFsClientCapabilityIssueRequest,
    request: Request,
    broker_secret: str | None = Header(default=None, alias=_BROKER_HEADER),
) -> CoreFsClientCapabilityIssueResponse:
    await asyncio.to_thread(_authorize_broker, request, broker_secret)
    _validate_mod_audience(payload.audience)
    try:
        capability, ttl = client_capability_broker.issue(
            audience=payload.audience,
            client_id=payload.clientId,
            install_digest=payload.installDigest,
            user_id=payload.userId,
            active_sessions=active_unlock_sessions(payload.userId),
            ttl_seconds=payload.ttlSeconds,
        )
    except ClientAccessError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return CoreFsClientCapabilityIssueResponse(
        capability=capability,
        expiresInSeconds=ttl,
    )
