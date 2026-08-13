from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.schemas.corefs_transfer import (
    CoreActiveStatusResponse,
    CoreFsRecoveryBrowseRequest,
    CoreFsRecoveryBrowseResponse,
    CoreImportOperationResponse,
    CoreImportPrepareRequest,
    CoreImportProbeRequest,
    CoreImportProbeResponse,
    CoreRollbackRequest,
    CoreTransferDestinationRequest,
    CoreTransferEstimateResponse,
    CoreTransferOperationResponse,
    CoreTransferPayloadRequest,
    CoreTransferPrepareRequest,
    CoreTransferProbeResponse,
)
from anima_server.services.corefs.active_core_registry import (
    read_active_core_status,
    schedule_active_core_rollback,
)
from anima_server.services.corefs.admission import (
    FsCredentialAdmission,
    FsCredentialAdmissionRejected,
)
from anima_server.services.corefs.archive_transfer import (
    CoreArchivePayloadKind,
    CoreArchiveTransferError,
)
from anima_server.services.corefs.recovery_access import CoreFsRecoveryAccessError
from anima_server.services.corefs.transfer import TransferError
from anima_server.services.corefs.transfer_jobs import (
    CorefsReattachmentNotSupported,
    PreparedTransfer,
    core_import_operations,
    core_transfer_operations,
)
from anima_server.services.corefs.types import WrappingPath

router = APIRouter(prefix="/api/corefs/transfer", tags=["corefs-transfer"])
_RECOVERY_BROWSE_ADMISSION = FsCredentialAdmission()


@router.post("/estimate", response_model=CoreTransferEstimateResponse)
def estimate_core_transfer(
    payload: CoreTransferPayloadRequest,
    request: Request,
) -> CoreTransferEstimateResponse:
    session = require_unlocked_session(request)
    try:
        inventory, estimate = core_transfer_operations.inspect(
            session=session,
            payload_kind=CoreArchivePayloadKind(payload.payloadKind),
        )
    except (CoreArchiveTransferError, TransferError, ValueError) as exc:
        raise _transfer_conflict() from exc
    return CoreTransferEstimateResponse(
        payloadKind=inventory.payload_kind.value,
        selectedBytes=inventory.selected_bytes,
        recordCount=inventory.record_count,
        archiveBytes=estimate.archive_bytes,
        requiredCapacityBytes=estimate.required_capacity_bytes,
        soulGeneration=inventory.soul_generation,
        filesystemGeneration=inventory.filesystem_generation,
    )


@router.post("/probe", response_model=CoreTransferProbeResponse)
def probe_core_transfer_destination(
    payload: CoreTransferDestinationRequest,
    request: Request,
) -> CoreTransferProbeResponse:
    session = require_unlocked_session(request)
    try:
        prepared = core_transfer_operations.inspect(
            session=session,
            payload_kind=CoreArchivePayloadKind(payload.payloadKind),
            destination=Path(payload.destination),
        )
        assert isinstance(prepared, PreparedTransfer)
    except (CoreArchiveTransferError, TransferError, OSError, ValueError) as exc:
        raise _transfer_conflict() from exc
    return CoreTransferProbeResponse(
        payloadKind=prepared.inventory.payload_kind.value,
        selectedBytes=prepared.inventory.selected_bytes,
        recordCount=prepared.inventory.record_count,
        archiveBytes=prepared.estimate.archive_bytes,
        requiredCapacityBytes=prepared.estimate.required_capacity_bytes,
        soulGeneration=prepared.inventory.soul_generation,
        filesystemGeneration=prepared.inventory.filesystem_generation,
        destination=str(prepared.probe.destination),
        availableBytes=prepared.probe.available_bytes,
        maximumSingleFileBytes=prepared.probe.maximum_single_file_bytes,
        publicationMode=prepared.probe.publication_mode.value,
        partLimitBytes=prepared.probe.part_limit_bytes,
        declaredVolumeCount=prepared.probe.declared_volume_count,
    )


@router.post(
    "/prepare",
    response_model=CoreTransferOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def prepare_core_transfer_export(
    payload: CoreTransferPrepareRequest,
    request: Request,
) -> CoreTransferOperationResponse:
    session = require_unlocked_session(request)
    try:
        operation = core_transfer_operations.start_export(
            user_id=session.user_id,
            session=session,
            destination=Path(payload.destination),
            final_name=payload.finalName,
            passphrase=payload.passphrase,
            payload_kind=CoreArchivePayloadKind(payload.payloadKind),
        )
    except (CoreArchiveTransferError, TransferError, OSError, ValueError) as exc:
        raise _transfer_conflict() from exc
    return CoreTransferOperationResponse(**operation.public())


@router.get("/operations/{operation_id}", response_model=CoreTransferOperationResponse)
def get_core_transfer_operation(
    operation_id: str,
    request: Request,
) -> CoreTransferOperationResponse:
    session = require_unlocked_session(request)
    try:
        operation = core_transfer_operations.get(operation_id, user_id=session.user_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "core_transfer_operation_not_found"},
        ) from exc
    return CoreTransferOperationResponse(**operation.public())


@router.post(
    "/operations/{operation_id}/cancel",
    response_model=CoreTransferOperationResponse,
)
def cancel_core_transfer_operation(
    operation_id: str,
    request: Request,
) -> CoreTransferOperationResponse:
    session = require_unlocked_session(request)
    try:
        operation = core_transfer_operations.request_cancel(
            operation_id,
            user_id=session.user_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "core_transfer_operation_not_found"},
        ) from exc
    return CoreTransferOperationResponse(**operation.public())


@router.post("/import/probe", response_model=CoreImportProbeResponse)
def probe_core_import(
    payload: CoreImportProbeRequest,
    request: Request,
) -> CoreImportProbeResponse:
    require_unlocked_session(request)
    try:
        prepared = core_import_operations.inspect(
            archive_path=Path(payload.archivePath),
            staging_parent=Path(payload.stagingParent),
        )
    except (CoreArchiveTransferError, TransferError, OSError, ValueError) as exc:
        raise _transfer_conflict() from exc
    return CoreImportProbeResponse(
        archiveBytes=prepared.archive_bytes,
        stagingParent=str(prepared.probe.staging_parent),
        availableBytes=prepared.probe.available_bytes,
        requiredCapacityBytes=prepared.probe.required_capacity_bytes,
    )


@router.post(
    "/import/prepare",
    response_model=CoreImportOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def prepare_core_import(
    payload: CoreImportPrepareRequest,
    request: Request,
) -> CoreImportOperationResponse:
    session = require_unlocked_session(request)
    try:
        operation = core_import_operations.start_import(
            user_id=session.user_id,
            archive_path=Path(payload.archivePath),
            staging_parent=Path(payload.stagingParent),
            passphrase=payload.passphrase,
        )
    except (CoreArchiveTransferError, TransferError, OSError, ValueError) as exc:
        raise _transfer_conflict() from exc
    return CoreImportOperationResponse(**operation.public())


@router.get(
    "/import/operations/{operation_id}",
    response_model=CoreImportOperationResponse,
)
def get_core_import_operation(
    operation_id: str,
    request: Request,
) -> CoreImportOperationResponse:
    session = require_unlocked_session(request)
    try:
        operation = core_import_operations.get(operation_id, user_id=session.user_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "core_import_operation_not_found"},
        ) from exc
    return CoreImportOperationResponse(**operation.public())


@router.post(
    "/import/operations/{operation_id}/cancel",
    response_model=CoreImportOperationResponse,
)
def cancel_core_import_operation(
    operation_id: str,
    request: Request,
) -> CoreImportOperationResponse:
    session = require_unlocked_session(request)
    try:
        operation = core_import_operations.request_cancel(
            operation_id,
            user_id=session.user_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "core_import_operation_not_found"},
        ) from exc
    return CoreImportOperationResponse(**operation.public())


@router.post(
    "/import/operations/{operation_id}/activate-on-restart",
    response_model=CoreImportOperationResponse,
)
def schedule_core_import_activation(
    operation_id: str,
    request: Request,
) -> CoreImportOperationResponse:
    session = require_unlocked_session(request)
    try:
        operation = core_import_operations.schedule_activation(
            operation_id,
            user_id=session.user_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "core_import_operation_not_found"},
        ) from exc
    except (CoreArchiveTransferError, TransferError, OSError, ValueError) as exc:
        raise _transfer_conflict() from exc
    return CoreImportOperationResponse(**operation.public())


@router.post(
    "/import/operations/{operation_id}/attach-corefs",
    response_model=CoreImportOperationResponse,
)
def reject_v1_corefs_reattachment(
    operation_id: str,
    request: Request,
) -> CoreImportOperationResponse:
    session = require_unlocked_session(request)
    try:
        operation = core_import_operations.request_corefs_reattachment(
            operation_id,
            user_id=session.user_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "core_import_operation_not_found"},
        ) from exc
    except CorefsReattachmentNotSupported as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_reattachment_not_supported"},
        ) from exc
    except TransferError as exc:
        raise _transfer_conflict() from exc
    return CoreImportOperationResponse(**operation.public())


@router.post(
    "/import/operations/{operation_id}/browse-corefs",
    response_model=CoreFsRecoveryBrowseResponse,
)
def browse_corefs_recovery(
    operation_id: str,
    payload: CoreFsRecoveryBrowseRequest,
    request: Request,
) -> CoreFsRecoveryBrowseResponse:
    session = require_unlocked_session(request)
    try:
        client_host = request.client.host if request.client is not None else "unknown"
        admission_key = f"{client_host}:{session.user_id}:{operation_id}"
        credential = payload.credential.get_secret_value()
        if payload.credentialKind == "recovery":
            credential = credential.strip().lower()
        with _RECOVERY_BROWSE_ADMISSION.admit(admission_key):
            result = core_import_operations.browse_corefs_recovery(
                operation_id,
                user_id=session.user_id,
                credential=credential,
                wrapping_path=WrappingPath(payload.credentialKind),
                browse_operation=payload.operation,
                logical_path=payload.path,
                cursor_after=payload.cursorAfter,
                cursor_generation=payload.cursorGeneration,
                limit=payload.limit,
                offset=payload.offset,
                max_bytes=payload.maxBytes,
                response_bytes=payload.responseBytes,
            )
    except FsCredentialAdmissionRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "corefs_recovery_browse_rate_limited"},
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "core_import_operation_not_found"},
        ) from exc
    except (CoreFsRecoveryAccessError, TransferError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_recovery_browse_failed"},
        ) from exc
    decoded: dict[str, object] | None = None
    if result.payload is not None:
        try:
            value = json.loads(result.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "corefs_invalid_native_response"},
            ) from exc
        if not isinstance(value, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "corefs_invalid_native_response"},
            )
        decoded = value
    return CoreFsRecoveryBrowseResponse(
        operation=result.operation,
        generation=result.generation,
        catalogHash=result.catalog_hash,
        result=decoded,
    )


@router.get("/active-core", response_model=CoreActiveStatusResponse)
def get_active_core_transfer_status(request: Request) -> CoreActiveStatusResponse:
    require_unlocked_session(request)
    try:
        active = read_active_core_status()
    except (TransferError, OSError, ValueError) as exc:
        raise _transfer_conflict() from exc
    return CoreActiveStatusResponse(
        generation=active.generation,
        activeCoreId=active.active_core_id,
        retainedCoreId=active.retained_core_id,
        activationId=active.activation_id,
        rollbackScheduled=active.rollback_scheduled,
    )


@router.post("/active-core/rollback-on-restart", response_model=CoreActiveStatusResponse)
def schedule_active_core_transfer_rollback(
    payload: CoreRollbackRequest,
    request: Request,
) -> CoreActiveStatusResponse:
    require_unlocked_session(request)
    if payload.confirmed is not True:
        raise _transfer_conflict()
    try:
        schedule_active_core_rollback()
        active = read_active_core_status()
    except (TransferError, OSError, ValueError) as exc:
        raise _transfer_conflict() from exc
    return CoreActiveStatusResponse(
        generation=active.generation,
        activeCoreId=active.active_core_id,
        retainedCoreId=active.retained_core_id,
        activationId=active.activation_id,
        rollbackScheduled=active.rollback_scheduled,
    )


def _transfer_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "core_transfer_precondition_failed"},
    )
