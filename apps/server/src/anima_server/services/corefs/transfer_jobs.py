from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from anima_server.services.core import get_core_dir
from anima_server.services.corefs.archive_transfer import (
    CoreArchiveExportResult,
    CoreArchiveInventory,
    CoreArchivePayloadKind,
    CoreArchiveTransferError,
    export_core_archive_v2,
    inspect_core_archive_v2,
    verify_core_archive_v2,
)
from anima_server.services.corefs.cutover import CutoverState, read_cutover_record
from anima_server.services.corefs.transfer import (
    DestinationProbe,
    PublicationMode,
    TransferCancelled,
    TransferError,
    TransferEstimate,
    estimate_transfer,
    probe_local_destination,
    publish_single_file,
)

_MAX_OPERATIONS = 32


class TransferOperationState(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PreparedTransfer:
    inventory: CoreArchiveInventory
    estimate: TransferEstimate
    probe: DestinationProbe


@dataclass(slots=True)
class TransferOperation:
    operation_id: str
    user_id: int
    prepared: PreparedTransfer
    state: TransferOperationState = TransferOperationState.PREPARED
    phase: str = "prepared"
    bytes_published: int = 0
    progress_percent: int = 0
    result_path: Path | None = None
    archive_id: str | None = None
    error_code: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, object]:
        return {
            "operationId": self.operation_id,
            "payloadKind": self.prepared.inventory.payload_kind.value,
            "state": self.state.value,
            "phase": self.phase,
            "selectedBytes": self.prepared.inventory.selected_bytes,
            "bytesPublished": self.bytes_published,
            "progressPercent": self.progress_percent,
            "publicationMode": self.prepared.probe.publication_mode.value,
            "declaredVolumeCount": self.prepared.probe.declared_volume_count,
            "resultPath": str(self.result_path) if self.result_path is not None else None,
            "archiveId": self.archive_id,
            "errorCode": self.error_code,
        }


class CoreTransferOperationManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._operations: dict[str, TransferOperation] = {}

    def inspect(
        self,
        *,
        session: Any,
        payload_kind: CoreArchivePayloadKind,
        destination: Path | None = None,
    ) -> PreparedTransfer | tuple[CoreArchiveInventory, TransferEstimate]:
        soul_generation = _coherent_soul_generation(payload_kind)
        inventory = inspect_core_archive_v2(
            session=session,
            payload_kind=payload_kind,
            soul_generation=soul_generation,
        )
        estimate = estimate_transfer(
            selected_bytes=inventory.selected_bytes,
            record_count=inventory.record_count,
        )
        if destination is None:
            return inventory, estimate
        probe = probe_local_destination(
            destination,
            estimate,
            forbidden_roots=(get_core_dir(),),
        )
        return PreparedTransfer(inventory=inventory, estimate=estimate, probe=probe)

    def start_export(
        self,
        *,
        user_id: int,
        session: Any,
        destination: Path,
        final_name: str,
        passphrase: str,
        payload_kind: CoreArchivePayloadKind,
    ) -> TransferOperation:
        prepared = self.inspect(
            session=session,
            payload_kind=payload_kind,
            destination=destination,
        )
        assert isinstance(prepared, PreparedTransfer)
        if prepared.probe.publication_mode is not PublicationMode.SINGLE_FILE:
            raise TransferError(
                "authenticated multipart archive sets are not available in this build"
            )
        operation = TransferOperation(
            operation_id=str(uuid4()),
            user_id=user_id,
            prepared=prepared,
        )
        with self._lock:
            self._prune_locked()
            if len(self._operations) >= _MAX_OPERATIONS:
                raise TransferError("too many transfer operations are retained")
            self._operations[operation.operation_id] = operation
        worker = threading.Thread(
            target=self._run_export,
            kwargs={
                "operation": operation,
                "session": session,
                "destination": prepared.probe.destination,
                "final_name": final_name,
                "passphrase": passphrase,
            },
            name=f"anima-core-export-{operation.operation_id[:8]}",
            daemon=True,
        )
        worker.start()
        return operation

    def get(self, operation_id: str, *, user_id: int) -> TransferOperation:
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None or operation.user_id != user_id:
                raise KeyError(operation_id)
            return operation

    def request_cancel(self, operation_id: str, *, user_id: int) -> TransferOperation:
        operation = self.get(operation_id, user_id=user_id)
        operation.cancel.set()
        return operation

    def _run_export(
        self,
        *,
        operation: TransferOperation,
        session: Any,
        destination: Path,
        final_name: str,
        passphrase: str,
    ) -> None:
        result: CoreArchiveExportResult | None = None

        def update(
            state: TransferOperationState,
            phase: str,
            progress: int,
        ) -> None:
            with self._lock:
                operation.state = state
                operation.phase = phase
                operation.progress_percent = progress

        def producer(partial: Path) -> None:
            nonlocal result
            update(TransferOperationState.RUNNING, "streaming", 25)
            result = export_core_archive_v2(
                session=session,
                output_path=partial,
                passphrase=passphrase,
                payload_kind=operation.prepared.inventory.payload_kind,
                soul_generation=operation.prepared.inventory.soul_generation,
            )
            if result.inventory != operation.prepared.inventory:
                raise CoreArchiveTransferError(
                    "archive inventory changed after destination preflight"
                )

        def verifier(partial: Path) -> None:
            update(TransferOperationState.VERIFYING, "verifying", 75)
            verify_core_archive_v2(
                partial,
                passphrase=passphrase,
                expected=operation.prepared.inventory,
            )

        try:
            update(TransferOperationState.RUNNING, "starting", 5)
            publication = publish_single_file(
                destination,
                final_name,
                producer=producer,
                verifier=verifier,
                cancel_requested=operation.cancel.is_set,
            )
            if result is None:
                raise CoreArchiveTransferError("archive export produced no result")
            with self._lock:
                operation.state = TransferOperationState.COMPLETED
                operation.phase = "completed"
                operation.progress_percent = 100
                operation.bytes_published = publication.bytes_published
                operation.result_path = publication.path
                operation.archive_id = result.archive_id
        except TransferCancelled:
            update(TransferOperationState.CANCELLED, "cancelled", operation.progress_percent)
        except (CoreArchiveTransferError, TransferError):
            with self._lock:
                operation.state = TransferOperationState.FAILED
                operation.phase = "failed"
                operation.error_code = "core_transfer_failed"
        except Exception:
            with self._lock:
                operation.state = TransferOperationState.FAILED
                operation.phase = "failed"
                operation.error_code = "core_transfer_internal_failure"

    def _prune_locked(self) -> None:
        terminal = [
            operation_id
            for operation_id, operation in self._operations.items()
            if operation.state
            in {
                TransferOperationState.COMPLETED,
                TransferOperationState.CANCELLED,
                TransferOperationState.FAILED,
            }
        ]
        while len(self._operations) >= _MAX_OPERATIONS and terminal:
            self._operations.pop(terminal.pop(0), None)


def _coherent_soul_generation(payload_kind: CoreArchivePayloadKind) -> int | None:
    if payload_kind is CoreArchivePayloadKind.FS:
        return None
    cutover = read_cutover_record()
    if cutover.state is CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY:
        generation = cutover.authoritative_generation
    elif cutover.state in {
        CutoverState.CORE_FS_VALIDATION_READONLY,
        CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE,
    }:
        generation = cutover.validation_generation
    else:
        generation = None
    if generation is None or generation <= 0:
        raise TransferError("ANIMA CORE has no coherent Soul/filesystem transfer checkpoint")
    return generation


core_transfer_operations = CoreTransferOperationManager()
