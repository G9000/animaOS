from __future__ import annotations

import json
from typing import cast

from fastapi import APIRouter, HTTPException, Request, status

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.schemas.corefs_security import (
    CoreFSFamilyReadinessResponse,
    CoreFSReadinessResponse,
    CoreFSRotateRequest,
    CoreFSRotateResponse,
    CoreFSRotationStatusResponse,
    CoreFSSecurityStatusResponse,
)
from anima_server.services.core import get_core_id, get_manifest_path
from anima_server.services.corefs.indexer import ReadinessState
from anima_server.services.corefs.migration import (
    reconcile_authenticated_catalog,
    schedule_unlocked_rebuild,
)
from anima_server.services.corefs.rotation import rotate_or_resume_frk
from anima_server.services.sessions import UnlockSession, unlock_session_store

router = APIRouter(prefix="/api/corefs/security", tags=["corefs-security"])


def _rotation_manifest_state() -> dict[str, object]:
    try:
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        rotation = manifest["frk_rotation"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_rotation_state_unavailable"},
        ) from exc
    if not isinstance(rotation, dict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_rotation_state_invalid"},
        )
    return cast(dict[str, object], rotation)


def _security_status(session: UnlockSession) -> CoreFSSecurityStatusResponse:
    index = session.runtime_index
    if index is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_runtime_index_unavailable"},
        )
    if (
        getattr(session, "corefs_session", None) is not None
        and getattr(session, "corefs_keys", None) is not None
    ):
        reconcile_authenticated_catalog(session)
        reconciled = index.snapshot()
        if reconciled.state in {
            ReadinessState.CATALOG_LOADING,
            ReadinessState.CATALOG_READY,
            ReadinessState.CATALOG_READY_DEGRADED,
            ReadinessState.TEXT_INDEXING,
            ReadinessState.SEMANTIC_INDEXING,
        } and callable(getattr(session.corefs_session, "walk_v1", None)):
            schedule_unlocked_rebuild(session)
    snapshot = index.snapshot()
    rotation = _rotation_manifest_state()
    try:
        active_version = int(rotation["active_version"])
        pending_raw = rotation.get("pending_version")
        pending_version = None if pending_raw is None else int(pending_raw)
        decrypt_only = sorted(int(item) for item in rotation.get("decrypt_only_versions", []))
        phase = str(rotation.get("phase", "idle"))
        if (
            active_version <= 0
            or (pending_version is not None and pending_version <= active_version)
            or any(version <= 0 for version in decrypt_only)
            or phase not in {"idle", "prepared", "verifying"}
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_rotation_state_invalid"},
        ) from exc

    return CoreFSSecurityStatusResponse(
        coreId=get_core_id(),
        readiness=CoreFSReadinessResponse(
            state=snapshot.state.value,
            catalogGeneration=snapshot.catalog_generation,
            processedObjects=snapshot.processed_objects,
            capabilities=sorted(item.value for item in snapshot.capabilities),
            retryable=snapshot.state
            not in {
                ReadinessState.LOCKED,
                ReadinessState.READY,
            },
            families={
                family: CoreFSFamilyReadinessResponse(
                    total=value.total,
                    processed=value.processed,
                    failed=value.failed,
                    degraded=value.degraded,
                )
                for family, value in snapshot.families.items()
            },
        ),
        rotation=CoreFSRotationStatusResponse(
            activeFrkVersion=active_version,
            pendingFrkVersion=pending_version,
            decryptOnlyFrkVersions=decrypt_only,
            phase=phase,  # type: ignore[arg-type]
            passwordReopenVerified=bool(rotation.get("password_reopen_verified", False)),
            recoveryReopenVerified=bool(rotation.get("recovery_reopen_verified", False)),
            oldKeyRetirementSafe=False,
            oldKeyRetirementBlockers=[
                *(["retained_catalogs_require_decrypt_only_keys"] if decrypt_only else []),
                "verified_active_backup_required",
                "pcf_010_authenticated_prune_required",
            ],
            blindIndexGeneration=snapshot.blind_index_generation,
            blindIndexPendingGeneration=snapshot.blind_index_pending_generation,
            blindIndexProgress=snapshot.blind_index_progress,
        ),
    )


@router.get("/status", response_model=CoreFSSecurityStatusResponse)
def get_corefs_security_status(request: Request) -> CoreFSSecurityStatusResponse:
    return _security_status(require_unlocked_session(request))


def _rotate_corefs_root_key(
    payload: CoreFSRotateRequest,
    request: Request,
) -> CoreFSRotateResponse:
    session = require_unlocked_session(request)
    try:
        result = rotate_or_resume_frk(
            session,
            current_password=payload.currentPassword,
            recovery_phrase=payload.recoveryPhrase,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "corefs_rotation_failed", "message": str(exc)},
        ) from None
    new_token = unlock_session_store.replace_user(
        session.user_id,
        session.deks,
        corefs_keys=result.active_subkeys,
    )
    replacement = unlock_session_store.resolve(new_token)
    if replacement is not None and replacement.runtime_index is not None:
        replacement.runtime_index.begin_blind_generation(
            generation=result.active_version,
            expected_count=0,
        )
        replacement.runtime_index.commit_blind_generation(result.active_version)
    return CoreFSRotateResponse(
        success=True,
        unlockToken=new_token,
        activeFrkVersion=result.active_version,
        committedCatalogGeneration=result.committed_catalog_generation,
        resumed=result.resumed,
    )


@router.post("/rotate", response_model=CoreFSRotateResponse)
def rotate_corefs_root_key(
    payload: CoreFSRotateRequest,
    request: Request,
) -> CoreFSRotateResponse:
    return _rotate_corefs_root_key(payload, request)


@router.post("/rotate/resume", response_model=CoreFSRotateResponse)
def resume_corefs_root_key_rotation(
    payload: CoreFSRotateRequest,
    request: Request,
) -> CoreFSRotateResponse:
    return _rotate_corefs_root_key(payload, request)
