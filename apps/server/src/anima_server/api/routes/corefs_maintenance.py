from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.api.routes.corefs import _resolve_principal
from anima_server.schemas.corefs_maintenance import (
    MaintenanceCredentials,
    MaintenanceExecuteRequest,
    MaintenanceInventoryResponse,
    MaintenancePrepareRequest,
    MaintenancePrepareResponse,
    MaintenanceResultResponse,
    MaintenanceStatusResponse,
)
from anima_server.services.corefs.gc import MaintenanceError, core_maintenance
from anima_server.services.sessions import UnlockSession

router = APIRouter(prefix="/api/corefs/maintenance", tags=["corefs-maintenance"])


def _owner_session(request: Request) -> UnlockSession:
    session = require_unlocked_session(request)
    if _resolve_principal(request, session).kind != "user":
        raise HTTPException(status_code=403, detail={"code": "corefs_maintenance_user_only"})
    return session


def _perform(function: Any, session: UnlockSession, payload: Any) -> Any:
    try:
        return function(session, payload)
    except MaintenanceError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "corefs_maintenance_blocked", "message": str(exc)}
        ) from None


@router.get("/status", response_model=MaintenanceStatusResponse)
def maintenance_status(request: Request) -> Any:
    return core_maintenance.status(_owner_session(request))


@router.post("/inventory", response_model=MaintenanceInventoryResponse)
def maintenance_inventory(payload: MaintenanceCredentials, request: Request) -> Any:
    return _perform(core_maintenance.inspect, _owner_session(request), payload)


@router.post("/prepare", response_model=MaintenancePrepareResponse)
def prepare_maintenance(payload: MaintenancePrepareRequest, request: Request) -> Any:
    return _perform(core_maintenance.prepare, _owner_session(request), payload)


@router.post("/execute", response_model=MaintenanceResultResponse)
def execute_maintenance(payload: MaintenanceExecuteRequest, request: Request) -> Any:
    return _perform(core_maintenance.execute, _owner_session(request), payload)
