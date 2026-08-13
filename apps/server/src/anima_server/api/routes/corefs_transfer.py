from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.schemas.corefs_transfer import (
    CoreImportOperationResponse,
    CoreImportPrepareRequest,
    CoreImportProbeRequest,
    CoreImportProbeResponse,
    CoreTransferDestinationRequest,
    CoreTransferEstimateResponse,
    CoreTransferOperationResponse,
    CoreTransferPayloadRequest,
    CoreTransferPrepareRequest,
    CoreTransferProbeResponse,
)
from anima_server.services.corefs.archive_transfer import (
    CoreArchivePayloadKind,
    CoreArchiveTransferError,
)
from anima_server.services.corefs.transfer import TransferError
from anima_server.services.corefs.transfer_jobs import (
    PreparedTransfer,
    core_import_operations,
    core_transfer_operations,
)

router = APIRouter(prefix="/api/corefs/transfer", tags=["corefs-transfer"])


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


def _transfer_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "core_transfer_precondition_failed"},
    )
