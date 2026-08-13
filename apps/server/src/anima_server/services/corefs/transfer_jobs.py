from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from anima_server.services.core import get_core_dir
from anima_server.services.corefs.archive_transfer import (
    CoreArchiveExportResult,
    CoreArchiveImportResult,
    CoreArchiveInventory,
    CoreArchivePayloadKind,
    CoreArchiveTransferError,
    export_core_archive_v2,
    inspect_core_archive_v2,
    stage_core_archive_v2,
    verify_core_archive_v2,
)
from anima_server.services.corefs.cutover import CutoverState, read_cutover_record
from anima_server.services.corefs.transfer import (
    DestinationProbe,
    ImportCapacityProbe,
    PublicationMode,
    TransferCancelled,
    TransferError,
    TransferEstimate,
    estimate_transfer,
    probe_import_staging,
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


class CorefsReattachmentNotSupported(TransferError):
    """V1 deliberately cannot combine a CoreFS-only recovery with a Soul."""


@dataclass(frozen=True, slots=True)
class PreparedTransfer:
    inventory: CoreArchiveInventory
    estimate: TransferEstimate
    probe: DestinationProbe


@dataclass(frozen=True, slots=True)
class PreparedImport:
    archive_path: Path
    archive_bytes: int
    probe: ImportCapacityProbe


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


@dataclass(slots=True)
class ImportOperation:
    operation_id: str
    user_id: int
    prepared: PreparedImport
    state: TransferOperationState = TransferOperationState.PREPARED
    phase: str = "prepared"
    bytes_processed: int = 0
    progress_percent: int = 0
    payload_kind: CoreArchivePayloadKind | None = None
    core_id: str | None = field(default=None, repr=False)
    recovery_state: str | None = None
    staging_path: Path | None = None
    archive_id: str | None = None
    activation_id: str | None = None
    restart_required: bool = False
    error_code: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, object]:
        return {
            "operationId": self.operation_id,
            "state": self.state.value,
            "phase": self.phase,
            "archiveBytes": self.prepared.archive_bytes,
            "bytesProcessed": self.bytes_processed,
            "progressPercent": self.progress_percent,
            "payloadKind": self.payload_kind.value if self.payload_kind is not None else None,
            "recoveryState": self.recovery_state,
            "stagingPath": str(self.staging_path) if self.staging_path is not None else None,
            "archiveId": self.archive_id,
            "activationId": self.activation_id,
            "restartRequired": self.restart_required,
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


class CoreImportOperationManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._operations: dict[str, ImportOperation] = {}

    def inspect(
        self,
        *,
        archive_path: Path,
        staging_parent: Path,
    ) -> PreparedImport:
        candidate = archive_path.expanduser()
        if candidate.is_symlink():
            raise TransferError("ANIMA CORE import source must be a regular file")
        archive = candidate.resolve(strict=True)
        active = get_core_dir().expanduser().resolve(strict=True)
        if not archive.is_file() or archive.is_relative_to(active):
            raise TransferError("ANIMA CORE import source is invalid")
        archive_bytes = archive.stat().st_size
        if archive_bytes <= 0:
            raise TransferError("ANIMA CORE import source is empty")
        probe = probe_import_staging(
            staging_parent,
            restored_core_bytes=archive_bytes,
            active_core_path=active,
        )
        return PreparedImport(
            archive_path=archive,
            archive_bytes=archive_bytes,
            probe=probe,
        )

    def start_import(
        self,
        *,
        user_id: int,
        archive_path: Path,
        staging_parent: Path,
        passphrase: str,
    ) -> ImportOperation:
        prepared = self.inspect(
            archive_path=archive_path,
            staging_parent=staging_parent,
        )
        operation = ImportOperation(
            operation_id=str(uuid4()),
            user_id=user_id,
            prepared=prepared,
        )
        with self._lock:
            self._prune_locked()
            if len(self._operations) >= _MAX_OPERATIONS:
                raise TransferError("too many import operations are retained")
            self._operations[operation.operation_id] = operation
        worker = threading.Thread(
            target=self._run_import,
            kwargs={"operation": operation, "passphrase": passphrase},
            name=f"anima-core-import-{operation.operation_id[:8]}",
            daemon=True,
        )
        worker.start()
        return operation

    def get(self, operation_id: str, *, user_id: int) -> ImportOperation:
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None or operation.user_id != user_id:
                raise KeyError(operation_id)
            return operation

    def request_cancel(self, operation_id: str, *, user_id: int) -> ImportOperation:
        operation = self.get(operation_id, user_id=user_id)
        operation.cancel.set()
        return operation

    def request_corefs_reattachment(
        self,
        operation_id: str,
        *,
        user_id: int,
    ) -> ImportOperation:
        operation = self.get(operation_id, user_id=user_id)
        with self._lock:
            if (
                operation.state is not TransferOperationState.COMPLETED
                or operation.payload_kind is not CoreArchivePayloadKind.FS
                or operation.recovery_state != "recovery_only"
            ):
                raise TransferError("CoreFS recovery operation is not attachable")
        raise CorefsReattachmentNotSupported("CoreFS-to-Soul reattachment is not supported in V1")

    def schedule_activation(self, operation_id: str, *, user_id: int) -> ImportOperation:
        operation = self.get(operation_id, user_id=user_id)
        with self._lock:
            if (
                operation.state is not TransferOperationState.COMPLETED
                or operation.payload_kind is not CoreArchivePayloadKind.FULL
                or operation.staging_path is None
                or operation.core_id is None
            ):
                raise TransferError("only a completed full restore can schedule activation")
            if operation.activation_id is not None:
                return operation
        from anima_server.services.corefs.active_core_registry import (
            schedule_full_restore_activation,
        )

        scheduled = schedule_full_restore_activation(
            operation.staging_path,
            core_id=operation.core_id,
        )
        with self._lock:
            operation.phase = "activation_scheduled"
            operation.activation_id = scheduled.activation_id
            operation.restart_required = True
        return operation

    def _run_import(self, *, operation: ImportOperation, passphrase: str) -> None:
        staging = (
            operation.prepared.probe.staging_parent
            / f".anima-restore-{operation.operation_id}.partial"
        )

        def update(state: TransferOperationState, phase: str, progress: int) -> None:
            with self._lock:
                operation.state = state
                operation.phase = phase
                operation.progress_percent = progress

        try:
            if operation.cancel.is_set():
                raise TransferCancelled("ANIMA CORE import was cancelled")
            refreshed = self.inspect(
                archive_path=operation.prepared.archive_path,
                staging_parent=operation.prepared.probe.staging_parent,
            )
            if (
                refreshed.archive_path != operation.prepared.archive_path
                or refreshed.archive_bytes != operation.prepared.archive_bytes
                or refreshed.probe.staging_parent != operation.prepared.probe.staging_parent
            ):
                raise TransferError("ANIMA CORE import inputs changed after preflight")
            update(TransferOperationState.RUNNING, "authenticating", 10)
            result: CoreArchiveImportResult = stage_core_archive_v2(
                operation.prepared.archive_path,
                passphrase=passphrase,
                staging_path=staging,
            )
            if operation.cancel.is_set():
                raise TransferCancelled("ANIMA CORE import was cancelled")
            recovery_state = {
                CoreArchivePayloadKind.FULL: "complete",
                CoreArchivePayloadKind.SOUL: "filesystem_missing",
                CoreArchivePayloadKind.FS: "recovery_only",
            }[result.inventory.payload_kind]
            with self._lock:
                operation.state = TransferOperationState.COMPLETED
                operation.phase = (
                    "staged_restart_required"
                    if result.inventory.payload_kind is CoreArchivePayloadKind.FULL
                    else "staged_credential_required"
                )
                operation.bytes_processed = operation.prepared.archive_bytes
                operation.progress_percent = 100
                operation.payload_kind = result.inventory.payload_kind
                operation.core_id = result.inventory.core_id
                operation.recovery_state = recovery_state
                operation.staging_path = result.staging_path
                operation.archive_id = result.archive_id
        except TransferCancelled:
            shutil.rmtree(staging, ignore_errors=True)
            update(TransferOperationState.CANCELLED, "cancelled", operation.progress_percent)
        except (CoreArchiveTransferError, TransferError, OSError):
            shutil.rmtree(staging, ignore_errors=True)
            with self._lock:
                operation.state = TransferOperationState.FAILED
                operation.phase = "failed"
                operation.error_code = "core_import_failed"
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            with self._lock:
                operation.state = TransferOperationState.FAILED
                operation.phase = "failed"
                operation.error_code = "core_import_internal_failure"

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
core_import_operations = CoreImportOperationManager()
