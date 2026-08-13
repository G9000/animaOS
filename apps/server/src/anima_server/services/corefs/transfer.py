from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import platform
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

ARCHIVE_FRAME_RESERVE_BYTES = 1024 * 1024
CAPACITY_MARGIN_BYTES = 64 * 1024 * 1024
DEFAULT_PART_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
HASH_BUFFER_BYTES = 1024 * 1024
FAT32_MAX_FILE_BYTES = (4 * 1024 * 1024 * 1024) - 1
FAT16_MAX_FILE_BYTES = (2 * 1024 * 1024 * 1024) - 1
REGISTRY_AUTH_DOMAIN = b"anima-active-core-registry-v1\x00"
ACTIVATION_AUTH_DOMAIN = b"anima-active-core-activation-v1\x00"
COMPLETION_AUTH_DOMAIN = b"anima-active-core-completion-v1\x00"
ACTIVATION_REQUEST_AUTH_DOMAIN = b"anima-active-core-request-v1\x00"
ROLLBACK_REQUEST_AUTH_DOMAIN = b"anima-active-core-rollback-request-v1\x00"
_ACTIVATION_LOCK = threading.RLock()


class TransferError(RuntimeError):
    pass


class TransferCancelled(TransferError):
    pass


class PublicationMode(StrEnum):
    SINGLE_FILE = "single_file"
    MULTIPART = "multipart"


@dataclass(frozen=True, slots=True)
class TransferEstimate:
    selected_bytes: int
    record_count: int
    archive_bytes: int
    required_capacity_bytes: int


@dataclass(frozen=True, slots=True)
class DestinationProbe:
    destination: Path
    available_bytes: int
    maximum_single_file_bytes: int | None
    publication_mode: PublicationMode
    part_limit_bytes: int | None
    declared_volume_count: int


@dataclass(frozen=True, slots=True)
class PublishedVolume:
    ordinal: int
    filename: str
    length: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PublicationResult:
    path: Path
    mode: PublicationMode
    bytes_published: int
    volumes: tuple[PublishedVolume, ...]


@dataclass(frozen=True, slots=True)
class ImportCapacityProbe:
    staging_parent: Path
    restored_core_bytes: int
    available_bytes: int
    required_capacity_bytes: int


@dataclass(frozen=True, slots=True)
class ActiveCorePointer:
    generation: int
    core_id: str
    active_core_path: Path
    retained_core_path: Path | None
    retained_core_id: str | None
    activation_id: str


@dataclass(frozen=True, slots=True)
class ActivationResult:
    pointer: ActiveCorePointer
    completion_path: Path


@dataclass(frozen=True, slots=True)
class ScheduledActivation:
    activation_id: str
    core_id: str
    staging_core_path: Path
    final_core_path: Path
    request_path: Path


@dataclass(frozen=True, slots=True)
class ScheduledRollback:
    rollback_id: str
    request_path: Path


BoundaryHook = Callable[[str], None]
CancelCheck = Callable[[], bool]
FileProducer = Callable[[Path], None]
FileVerifier = Callable[[Path], None]
ControllerProducer = Callable[[Path, tuple[PublishedVolume, ...]], None]
CoreVerifier = Callable[[Path], None]


def estimate_transfer(*, selected_bytes: int, record_count: int) -> TransferEstimate:
    if selected_bytes < 0 or record_count <= 0:
        raise TransferError("transfer estimate inputs are invalid")
    framing = ARCHIVE_FRAME_RESERVE_BYTES + (record_count * 64)
    archive_bytes = selected_bytes + framing
    required_capacity = archive_bytes + max(CAPACITY_MARGIN_BYTES, archive_bytes // 20)
    return TransferEstimate(
        selected_bytes=selected_bytes,
        record_count=record_count,
        archive_bytes=archive_bytes,
        required_capacity_bytes=required_capacity,
    )


def probe_local_destination(
    destination: Path,
    estimate: TransferEstimate,
    *,
    forbidden_roots: Sequence[Path] = (),
    available_bytes: int | None = None,
    maximum_single_file_bytes: int | None = None,
) -> DestinationProbe:
    resolved = destination.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise TransferError("transfer destination must be an existing local directory")
    for forbidden in forbidden_roots:
        forbidden_resolved = forbidden.expanduser().resolve(strict=False)
        if resolved == forbidden_resolved or resolved.is_relative_to(forbidden_resolved):
            raise TransferError("transfer destination cannot be inside the active Core")

    _probe_writable_atomic_directory(resolved)
    available = shutil.disk_usage(resolved).free if available_bytes is None else available_bytes
    if available < estimate.required_capacity_bytes:
        raise TransferError("transfer destination has insufficient capacity")

    detected_limit = maximum_single_file_bytes
    if detected_limit is None:
        detected_limit = detect_maximum_single_file_bytes(resolved)
    if detected_limit is not None and detected_limit <= 0:
        raise TransferError("transfer destination reported an invalid single-file limit")

    if detected_limit is None or estimate.archive_bytes <= detected_limit:
        return DestinationProbe(
            destination=resolved,
            available_bytes=available,
            maximum_single_file_bytes=detected_limit,
            publication_mode=PublicationMode.SINGLE_FILE,
            part_limit_bytes=None,
            declared_volume_count=1,
        )

    part_limit = min(DEFAULT_PART_LIMIT_BYTES, detected_limit)
    if part_limit <= ARCHIVE_FRAME_RESERVE_BYTES:
        raise TransferError("transfer destination single-file limit is too small")
    volume_count = math.ceil(estimate.archive_bytes / part_limit)
    if volume_count <= 1 or volume_count > (2**32 - 1):
        raise TransferError("transfer multipart volume count is invalid")
    return DestinationProbe(
        destination=resolved,
        available_bytes=available,
        maximum_single_file_bytes=detected_limit,
        publication_mode=PublicationMode.MULTIPART,
        part_limit_bytes=part_limit,
        declared_volume_count=volume_count,
    )


def detect_maximum_single_file_bytes(destination: Path) -> int | None:
    filesystem = _filesystem_name(destination)
    normalized = filesystem.casefold().replace("-", "") if filesystem else ""
    if normalized in {"vfat", "fat32", "msdos", "msdosfs"}:
        return FAT32_MAX_FILE_BYTES
    if normalized in {"fat", "fat16"}:
        return FAT16_MAX_FILE_BYTES
    return None


def probe_import_staging(
    staging_parent: Path,
    *,
    restored_core_bytes: int,
    active_core_path: Path | None = None,
    available_bytes: int | None = None,
) -> ImportCapacityProbe:
    if restored_core_bytes <= 0:
        raise TransferError("restored Core size must be positive")
    parent = staging_parent.expanduser().resolve(strict=True)
    if not parent.is_dir():
        raise TransferError("import staging parent must be an existing directory")
    if active_core_path is not None:
        active = active_core_path.expanduser().resolve(strict=True)
        if parent == active or parent.is_relative_to(active):
            raise TransferError("import staging cannot be inside the active Core")
    _probe_writable_atomic_directory(parent)
    available = shutil.disk_usage(parent).free if available_bytes is None else available_bytes
    required = restored_core_bytes + max(CAPACITY_MARGIN_BYTES, restored_core_bytes // 20)
    if available < required:
        raise TransferError("import destination has insufficient same-volume staging capacity")
    return ImportCapacityProbe(
        staging_parent=parent,
        restored_core_bytes=restored_core_bytes,
        available_bytes=available,
        required_capacity_bytes=required,
    )


def initialize_active_core_pointer(
    registry_path: Path,
    *,
    authentication_key: bytes,
    core_id: str,
    active_core_path: Path,
) -> ActiveCorePointer:
    registry = registry_path.expanduser().resolve(strict=False)
    active = active_core_path.expanduser().resolve(strict=True)
    _validate_authentication_key(authentication_key)
    _validate_core_identity(core_id)
    if not active.is_dir():
        raise TransferError("active Core pointer target must be a directory")
    _verify_manifest_core_id(active, core_id)
    registry.parent.mkdir(parents=True, exist_ok=True)
    with _ACTIVATION_LOCK, _exclusive_activation_lock(registry):
        if registry.exists():
            raise TransferError("active Core pointer is already initialized")
        activation_id = str(uuid4())
        body = _pointer_body(
            generation=1,
            core_id=core_id,
            active_core_path=active,
            retained_core_path=None,
            retained_core_id=None,
            activation_id=activation_id,
        )
        _write_authenticated_record(
            registry,
            body,
            authentication_key,
            REGISTRY_AUTH_DOMAIN,
        )
        return _pointer_from_body(body)


def read_active_core_pointer(
    registry_path: Path,
    *,
    authentication_key: bytes,
) -> ActiveCorePointer:
    _validate_authentication_key(authentication_key)
    body = _read_authenticated_record(
        registry_path.expanduser().resolve(strict=True),
        authentication_key,
        REGISTRY_AUTH_DOMAIN,
        expected_keys={
            "version",
            "generation",
            "coreId",
            "activeCorePath",
            "retainedCorePath",
            "retainedCoreId",
            "activationId",
        },
    )
    return _pointer_from_body(body)


def activate_staged_core(
    staging_core_path: Path,
    final_core_path: Path,
    registry_path: Path,
    *,
    authentication_key: bytes,
    core_id: str,
    activation_id: str,
    verifier: CoreVerifier,
    boundary_hook: BoundaryHook | None = None,
) -> ActivationResult:
    _validate_authentication_key(authentication_key)
    _validate_core_identity(core_id)
    try:
        normalized_activation_id = str(UUID(activation_id))
    except (ValueError, AttributeError) as exc:
        raise TransferError("activation ID is invalid") from exc

    staging = staging_core_path.expanduser().resolve(strict=False)
    final = final_core_path.expanduser().resolve(strict=False)
    registry = registry_path.expanduser().resolve(strict=True)
    if staging.parent != final.parent or staging == final:
        raise TransferError("import staging and final Core must be same-parent siblings")
    if final.name in {"", ".", ".."}:
        raise TransferError("final Core path is invalid")

    journal = registry.with_name(f"{registry.name}.activation")
    completion = registry.with_name(f"{registry.name}.completion")
    with _ACTIVATION_LOCK, _exclusive_activation_lock(registry):
        current = _pointer_from_body(
            _read_authenticated_record(
                registry,
                authentication_key,
                REGISTRY_AUTH_DOMAIN,
                expected_keys={
                    "version",
                    "generation",
                    "coreId",
                    "activeCorePath",
                    "retainedCorePath",
                    "retainedCoreId",
                    "activationId",
                },
            )
        )
        if current.active_core_path == final and current.activation_id == normalized_activation_id:
            verifier(current.active_core_path)
            return _finalize_activation_recovery(
                journal=journal,
                completion=completion,
                pointer=current,
                authentication_key=authentication_key,
                boundary_hook=boundary_hook,
            )
        if current.active_core_path == final:
            raise TransferError("final Core is already active under another activation")
        if current.retained_core_path is not None:
            raise TransferError("a retained rollback Core must be resolved before reactivation")

        existing_journal = _load_activation_journal(
            journal,
            authentication_key=authentication_key,
        )
        target_generation = current.generation + 1
        journal_body = {
            "version": 1,
            "activationId": normalized_activation_id,
            "targetGeneration": target_generation,
            "coreId": core_id,
            "stagingCorePath": os.fspath(staging),
            "finalCorePath": os.fspath(final),
            "retainedCorePath": os.fspath(current.active_core_path),
            "retainedCoreId": current.core_id,
        }
        if existing_journal is not None and existing_journal != journal_body:
            raise TransferError("another authenticated Core activation is incomplete")

        if not staging.exists() and not final.exists():
            raise TransferError("import staging Core is missing")
        if staging.exists() and final.exists():
            raise TransferError("both staging and final Core directories exist")
        candidate = staging if staging.exists() else final
        if not candidate.is_dir():
            raise TransferError("import activation candidate is not a directory")
        if candidate.stat().st_dev != final.parent.stat().st_dev:
            raise TransferError("import staging Core is not on the activation volume")
        verifier(candidate)
        _sync_tree(candidate)

        if existing_journal is None:
            _write_authenticated_record(
                journal,
                journal_body,
                authentication_key,
                ACTIVATION_AUTH_DOMAIN,
            )
        _boundary(boundary_hook, "activation:after_journal")

        if staging.exists():
            _replace_path(staging, final)
        _boundary(boundary_hook, "activation:after_directory_rename")
        verifier(final)

        pointer_body = _pointer_body(
            generation=target_generation,
            core_id=core_id,
            active_core_path=final,
            retained_core_path=current.active_core_path,
            retained_core_id=current.core_id,
            activation_id=normalized_activation_id,
        )
        _write_authenticated_record(
            registry,
            pointer_body,
            authentication_key,
            REGISTRY_AUTH_DOMAIN,
        )
        pointer = _pointer_from_body(pointer_body)
        _boundary(boundary_hook, "activation:after_pointer")
        return _finalize_activation_recovery(
            journal=journal,
            completion=completion,
            pointer=pointer,
            authentication_key=authentication_key,
            boundary_hook=boundary_hook,
        )


def recover_active_core_activation(
    registry_path: Path,
    *,
    authentication_key: bytes,
    verifier: CoreVerifier,
    boundary_hook: BoundaryHook | None = None,
) -> ActivationResult | None:
    _validate_authentication_key(authentication_key)
    registry = registry_path.expanduser().resolve(strict=True)
    journal = registry.with_name(f"{registry.name}.activation")
    journal_body = _load_activation_journal(
        journal,
        authentication_key=authentication_key,
    )
    if journal_body is None:
        return None
    return activate_staged_core(
        Path(cast(str, journal_body["stagingCorePath"])),
        Path(cast(str, journal_body["finalCorePath"])),
        registry,
        authentication_key=authentication_key,
        core_id=cast(str, journal_body["coreId"]),
        activation_id=cast(str, journal_body["activationId"]),
        verifier=verifier,
        boundary_hook=boundary_hook,
    )


def rollback_to_retained_core(
    registry_path: Path,
    *,
    authentication_key: bytes,
    rollback_id: str,
    verifier: CoreVerifier,
    boundary_hook: BoundaryHook | None = None,
) -> ActivationResult:
    _validate_authentication_key(authentication_key)
    try:
        normalized_rollback_id = str(UUID(rollback_id))
    except (ValueError, AttributeError) as exc:
        raise TransferError("rollback ID is invalid") from exc
    registry = registry_path.expanduser().resolve(strict=True)
    completion = registry.with_name(f"{registry.name}.completion")
    with _ACTIVATION_LOCK, _exclusive_activation_lock(registry):
        current = _pointer_from_body(
            _read_authenticated_record(
                registry,
                authentication_key,
                REGISTRY_AUTH_DOMAIN,
                expected_keys={
                    "version",
                    "generation",
                    "coreId",
                    "activeCorePath",
                    "retainedCorePath",
                    "retainedCoreId",
                    "activationId",
                },
            )
        )
        if current.activation_id == normalized_rollback_id:
            verifier(current.active_core_path)
            return _write_pointer_completion(
                completion=completion,
                pointer=current,
                authentication_key=authentication_key,
                boundary_hook=boundary_hook,
                boundary_name="rollback:after_completion",
            )
        if current.retained_core_path is None or current.retained_core_id is None:
            raise TransferError("active Core pointer has no retained rollback Core")
        verifier(current.active_core_path)
        verifier(current.retained_core_path)
        pointer_body = _pointer_body(
            generation=current.generation + 1,
            core_id=current.retained_core_id,
            active_core_path=current.retained_core_path,
            retained_core_path=current.active_core_path,
            retained_core_id=current.core_id,
            activation_id=normalized_rollback_id,
        )
        _write_authenticated_record(
            registry,
            pointer_body,
            authentication_key,
            REGISTRY_AUTH_DOMAIN,
        )
        pointer = _pointer_from_body(pointer_body)
        _boundary(boundary_hook, "rollback:after_pointer")
        return _write_pointer_completion(
            completion=completion,
            pointer=pointer,
            authentication_key=authentication_key,
            boundary_hook=boundary_hook,
            boundary_name="rollback:after_completion",
        )


def schedule_staged_core_activation(
    staging_core_path: Path,
    final_core_path: Path,
    registry_path: Path,
    *,
    authentication_key: bytes,
    core_id: str,
    activation_id: str,
    verifier: CoreVerifier,
) -> ScheduledActivation:
    """Durably schedule activation for consumption by the next startup."""
    _validate_authentication_key(authentication_key)
    _validate_core_identity(core_id)
    try:
        normalized_activation_id = str(UUID(activation_id))
    except (ValueError, AttributeError) as exc:
        raise TransferError("activation ID is invalid") from exc
    staging_input = staging_core_path.expanduser()
    if staging_input.is_symlink():
        raise TransferError("scheduled activation staging path is invalid")
    staging = staging_input.resolve(strict=True)
    final = final_core_path.expanduser().resolve(strict=False)
    registry = registry_path.expanduser().resolve(strict=True)
    if staging.parent != final.parent or staging == final or final.exists():
        raise TransferError("scheduled activation paths are invalid")
    request = registry.with_name(f"{registry.name}.request")
    rollback_request = registry.with_name(f"{registry.name}.rollback-request")
    body = {
        "version": 1,
        "activationId": normalized_activation_id,
        "coreId": core_id,
        "stagingCorePath": os.fspath(staging),
        "finalCorePath": os.fspath(final),
    }
    with _ACTIVATION_LOCK, _exclusive_activation_lock(registry):
        pointer = _pointer_from_body(
            _read_authenticated_record(
                registry,
                authentication_key,
                REGISTRY_AUTH_DOMAIN,
                expected_keys={
                    "version",
                    "generation",
                    "coreId",
                    "activeCorePath",
                    "retainedCorePath",
                    "retainedCoreId",
                    "activationId",
                },
            )
        )
        if pointer.retained_core_path is not None:
            raise TransferError("a retained rollback Core must be resolved before activation")
        if rollback_request.exists():
            raise TransferError("a retained-Core rollback is already scheduled")
        _verify_manifest_core_id(staging, core_id)
        verifier(staging)
        if request.exists():
            existing = _read_authenticated_record(
                request,
                authentication_key,
                ACTIVATION_REQUEST_AUTH_DOMAIN,
                expected_keys=set(body),
            )
            if existing != body:
                raise TransferError("another Core activation is already scheduled")
        else:
            _write_authenticated_record(
                request,
                body,
                authentication_key,
                ACTIVATION_REQUEST_AUTH_DOMAIN,
            )
    return ScheduledActivation(
        activation_id=normalized_activation_id,
        core_id=core_id,
        staging_core_path=staging,
        final_core_path=final,
        request_path=request,
    )


def consume_scheduled_core_activation(
    registry_path: Path,
    *,
    authentication_key: bytes,
    verifier: CoreVerifier,
    boundary_hook: BoundaryHook | None = None,
) -> ActivationResult | None:
    """Consume an authenticated pending request during pre-resource startup."""
    _validate_authentication_key(authentication_key)
    registry = registry_path.expanduser().resolve(strict=True)
    request = registry.with_name(f"{registry.name}.request")
    if not request.exists():
        return None
    body = _read_authenticated_record(
        request,
        authentication_key,
        ACTIVATION_REQUEST_AUTH_DOMAIN,
        expected_keys={
            "version",
            "activationId",
            "coreId",
            "stagingCorePath",
            "finalCorePath",
        },
    )
    if body.get("version") != 1 or any(
        not isinstance(body.get(field), str) or not body[field]
        for field in ("activationId", "coreId", "stagingCorePath", "finalCorePath")
    ):
        raise TransferError("scheduled Core activation is invalid")
    result = activate_staged_core(
        Path(cast(str, body["stagingCorePath"])),
        Path(cast(str, body["finalCorePath"])),
        registry,
        authentication_key=authentication_key,
        core_id=cast(str, body["coreId"]),
        activation_id=cast(str, body["activationId"]),
        verifier=verifier,
        boundary_hook=boundary_hook,
    )
    _boundary(boundary_hook, "activation-request:after_activation")
    request.unlink(missing_ok=True)
    _fsync_directory(request.parent)
    _boundary(boundary_hook, "activation-request:after_delete")
    return result


def schedule_retained_core_rollback(
    registry_path: Path,
    *,
    authentication_key: bytes,
    rollback_id: str,
) -> ScheduledRollback:
    """Durably schedule retained-Core rollback for the next startup."""
    _validate_authentication_key(authentication_key)
    try:
        normalized_rollback_id = str(UUID(rollback_id))
    except (ValueError, AttributeError) as exc:
        raise TransferError("rollback ID is invalid") from exc
    registry = registry_path.expanduser().resolve(strict=True)
    request = registry.with_name(f"{registry.name}.rollback-request")
    activation_request = registry.with_name(f"{registry.name}.request")
    body = {"version": 1, "rollbackId": normalized_rollback_id}
    with _ACTIVATION_LOCK, _exclusive_activation_lock(registry):
        pointer = _pointer_from_body(
            _read_authenticated_record(
                registry,
                authentication_key,
                REGISTRY_AUTH_DOMAIN,
                expected_keys={
                    "version",
                    "generation",
                    "coreId",
                    "activeCorePath",
                    "retainedCorePath",
                    "retainedCoreId",
                    "activationId",
                },
            )
        )
        if pointer.retained_core_path is None:
            raise TransferError("active Core pointer has no retained rollback Core")
        if activation_request.exists():
            raise TransferError("a Core activation is already scheduled")
        if request.exists():
            existing = _read_authenticated_record(
                request,
                authentication_key,
                ROLLBACK_REQUEST_AUTH_DOMAIN,
                expected_keys=set(body),
            )
            if existing != body:
                raise TransferError("another retained-Core rollback is already scheduled")
        else:
            _write_authenticated_record(
                request,
                body,
                authentication_key,
                ROLLBACK_REQUEST_AUTH_DOMAIN,
            )
    return ScheduledRollback(rollback_id=normalized_rollback_id, request_path=request)


def consume_scheduled_core_rollback(
    registry_path: Path,
    *,
    authentication_key: bytes,
    verifier: CoreVerifier,
    boundary_hook: BoundaryHook | None = None,
) -> ActivationResult | None:
    """Consume an authenticated rollback request during pre-resource startup."""
    _validate_authentication_key(authentication_key)
    registry = registry_path.expanduser().resolve(strict=True)
    scheduled = read_scheduled_core_rollback(
        registry,
        authentication_key=authentication_key,
    )
    if scheduled is None:
        return None
    result = rollback_to_retained_core(
        registry,
        authentication_key=authentication_key,
        rollback_id=scheduled.rollback_id,
        verifier=verifier,
        boundary_hook=boundary_hook,
    )
    _boundary(boundary_hook, "rollback-request:after_rollback")
    scheduled.request_path.unlink(missing_ok=True)
    _fsync_directory(scheduled.request_path.parent)
    _boundary(boundary_hook, "rollback-request:after_delete")
    return result


def read_scheduled_core_rollback(
    registry_path: Path,
    *,
    authentication_key: bytes,
) -> ScheduledRollback | None:
    _validate_authentication_key(authentication_key)
    registry = registry_path.expanduser().resolve(strict=True)
    request = registry.with_name(f"{registry.name}.rollback-request")
    if not request.exists():
        return None
    body = _read_authenticated_record(
        request,
        authentication_key,
        ROLLBACK_REQUEST_AUTH_DOMAIN,
        expected_keys={"version", "rollbackId"},
    )
    rollback_id = body.get("rollbackId")
    if body.get("version") != 1 or not isinstance(rollback_id, str):
        raise TransferError("scheduled retained-Core rollback is invalid")
    try:
        normalized = str(UUID(rollback_id))
    except ValueError as exc:
        raise TransferError("scheduled retained-Core rollback is invalid") from exc
    return ScheduledRollback(rollback_id=normalized, request_path=request)


def _pointer_body(
    *,
    generation: int,
    core_id: str,
    active_core_path: Path,
    retained_core_path: Path | None,
    retained_core_id: str | None,
    activation_id: str,
) -> dict[str, object]:
    return {
        "version": 1,
        "generation": generation,
        "coreId": core_id,
        "activeCorePath": os.fspath(active_core_path),
        "retainedCorePath": (
            os.fspath(retained_core_path) if retained_core_path is not None else None
        ),
        "retainedCoreId": retained_core_id,
        "activationId": activation_id,
    }


def _pointer_from_body(body: dict[str, object]) -> ActiveCorePointer:
    if body.get("version") != 1:
        raise TransferError("active Core pointer version is invalid")
    generation = body.get("generation")
    core_id = body.get("coreId")
    active_path = body.get("activeCorePath")
    retained_path = body.get("retainedCorePath")
    retained_core_id = body.get("retainedCoreId")
    activation_id = body.get("activationId")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation <= 0
        or not isinstance(core_id, str)
        or not isinstance(active_path, str)
        or not active_path
        or (retained_path is not None and (not isinstance(retained_path, str) or not retained_path))
        or (retained_core_id is not None and not isinstance(retained_core_id, str))
        or (retained_path is None) != (retained_core_id is None)
        or not isinstance(activation_id, str)
    ):
        raise TransferError("active Core pointer shape is invalid")
    _validate_core_identity(core_id)
    if retained_core_id is not None:
        _validate_core_identity(retained_core_id)
    try:
        normalized_activation_id = str(UUID(activation_id))
    except ValueError as exc:
        raise TransferError("active Core pointer activation ID is invalid") from exc
    active = Path(active_path).expanduser().resolve(strict=True)
    if not active.is_dir():
        raise TransferError("active Core pointer target is unavailable")
    _verify_manifest_core_id(active, core_id)
    retained = (
        Path(cast(str, retained_path)).expanduser().resolve(strict=True)
        if retained_path is not None
        else None
    )
    if retained is not None and not retained.is_dir():
        raise TransferError("retained Core pointer target is unavailable")
    if retained is not None and retained_core_id is not None:
        _verify_manifest_core_id(retained, retained_core_id)
    return ActiveCorePointer(
        generation=generation,
        core_id=core_id,
        active_core_path=active,
        retained_core_path=retained,
        retained_core_id=retained_core_id,
        activation_id=normalized_activation_id,
    )


def _verify_manifest_core_id(core_path: Path, expected_core_id: str) -> None:
    manifest_path = core_path / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise TransferError("active Core pointer target has no regular manifest")
    try:
        if manifest_path.stat().st_size > 8 * 1024 * 1024:
            raise TransferError("active Core pointer manifest exceeds its bound")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferError("active Core pointer manifest is invalid") from exc
    if not isinstance(payload, dict) or payload.get("core_id") != expected_core_id:
        raise TransferError("active Core pointer identity does not match its manifest")


def _load_activation_journal(
    journal: Path,
    *,
    authentication_key: bytes,
) -> dict[str, object] | None:
    if not journal.exists():
        return None
    body = _read_authenticated_record(
        journal,
        authentication_key,
        ACTIVATION_AUTH_DOMAIN,
        expected_keys={
            "version",
            "activationId",
            "targetGeneration",
            "coreId",
            "stagingCorePath",
            "finalCorePath",
            "retainedCorePath",
            "retainedCoreId",
        },
    )
    if body.get("version") != 1:
        raise TransferError("Core activation journal version is invalid")
    try:
        UUID(cast(str, body["activationId"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise TransferError("Core activation journal is invalid") from exc
    generation = body.get("targetGeneration")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation <= 1
        or any(
            not isinstance(body.get(field), str) or not body[field]
            for field in (
                "coreId",
                "stagingCorePath",
                "finalCorePath",
                "retainedCorePath",
                "retainedCoreId",
            )
        )
    ):
        raise TransferError("Core activation journal is invalid")
    _validate_core_identity(cast(str, body["coreId"]))
    _validate_core_identity(cast(str, body["retainedCoreId"]))
    return body


def _finalize_activation_recovery(
    *,
    journal: Path,
    completion: Path,
    pointer: ActiveCorePointer,
    authentication_key: bytes,
    boundary_hook: BoundaryHook | None,
) -> ActivationResult:
    result = _write_pointer_completion(
        completion=completion,
        pointer=pointer,
        authentication_key=authentication_key,
        boundary_hook=boundary_hook,
        boundary_name="activation:after_completion",
    )
    journal.unlink(missing_ok=True)
    _fsync_directory(journal.parent)
    _boundary(boundary_hook, "activation:after_journal_cleanup")
    return result


def _write_pointer_completion(
    *,
    completion: Path,
    pointer: ActiveCorePointer,
    authentication_key: bytes,
    boundary_hook: BoundaryHook | None,
    boundary_name: str,
) -> ActivationResult:
    body = {
        "version": 1,
        "activationId": pointer.activation_id,
        "generation": pointer.generation,
        "coreId": pointer.core_id,
        "activeCorePath": os.fspath(pointer.active_core_path),
        "retainedCorePath": (
            os.fspath(pointer.retained_core_path)
            if pointer.retained_core_path is not None
            else None
        ),
        "retainedCoreId": pointer.retained_core_id,
    }
    if completion.exists():
        existing = _read_authenticated_record(
            completion,
            authentication_key,
            COMPLETION_AUTH_DOMAIN,
            expected_keys=set(body),
        )
        if existing != body:
            _write_authenticated_record(
                completion,
                body,
                authentication_key,
                COMPLETION_AUTH_DOMAIN,
            )
    else:
        _write_authenticated_record(
            completion,
            body,
            authentication_key,
            COMPLETION_AUTH_DOMAIN,
        )
    _boundary(boundary_hook, boundary_name)
    return ActivationResult(pointer=pointer, completion_path=completion)


def _validate_authentication_key(authentication_key: bytes) -> None:
    if len(authentication_key) < 32:
        raise TransferError("active Core registry authentication key is invalid")


def _validate_core_identity(core_id: str) -> None:
    try:
        UUID(core_id)
    except (ValueError, AttributeError) as exc:
        raise TransferError("Core ID is invalid") from exc


def _authenticated_payload(
    body: dict[str, object],
    authentication_key: bytes,
    domain: bytes,
) -> dict[str, object]:
    encoded = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    authentication = hmac.new(authentication_key, domain + encoded, hashlib.sha256).hexdigest()
    return {**body, "authentication": authentication}


def _write_authenticated_record(
    path: Path,
    body: dict[str, object],
    authentication_key: bytes,
    domain: bytes,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    partial.unlink(missing_ok=True)
    payload = _authenticated_payload(body, authentication_key, domain)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    descriptor = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        verified = _read_authenticated_record(
            partial,
            authentication_key,
            domain,
            expected_keys=set(body),
        )
        if verified != body:
            raise TransferError("authenticated registry record failed verification")
        _replace_path(partial, path)
        _fsync_directory(path.parent)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _read_authenticated_record(
    path: Path,
    authentication_key: bytes,
    domain: bytes,
    *,
    expected_keys: set[str],
) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferError("authenticated registry record is unreadable") from exc
    if not isinstance(payload, dict) or set(payload) != expected_keys | {"authentication"}:
        raise TransferError("authenticated registry record shape is invalid")
    authentication = payload.pop("authentication", None)
    if not isinstance(authentication, str) or len(authentication) != 64:
        raise TransferError("authenticated registry record tag is invalid")
    body = cast(dict[str, object], payload)
    expected = cast(str, _authenticated_payload(body, authentication_key, domain)["authentication"])
    if not hmac.compare_digest(authentication, expected):
        raise TransferError("authenticated registry record tag is invalid")
    return body


@contextmanager
def _exclusive_activation_lock(registry_path: Path) -> Iterator[None]:
    lock_path = registry_path.with_name(f".{registry_path.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise TransferError("active Core registry is being updated") from exc
        yield
    finally:
        os.close(descriptor)


def _sync_tree(root: Path) -> None:
    directories: list[Path] = []
    for current, directory_names, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.append(current_path)
        for name in tuple(directory_names):
            child = current_path / name
            if child.is_symlink():
                raise TransferError("staged Core contains a symbolic-link directory")
        for name in filenames:
            child = current_path / name
            if child.is_symlink() or not child.is_file():
                raise TransferError("staged Core contains a non-regular file")
            _sync_file(child)
    for directory in reversed(directories):
        _fsync_directory(directory)


def publish_single_file(
    destination: Path,
    final_name: str,
    *,
    producer: FileProducer,
    verifier: FileVerifier,
    cancel_requested: CancelCheck | None = None,
    boundary_hook: BoundaryHook | None = None,
) -> PublicationResult:
    root = destination.expanduser().resolve(strict=True)
    _validate_leaf_name(final_name)
    final_path = root / final_name
    partial_path = root / f"{final_name}.partial"
    if final_path.exists() or partial_path.exists():
        raise TransferError("transfer publication target already exists")

    committed = False
    try:
        _boundary(boundary_hook, "single:before_write")
        _check_cancel(cancel_requested)
        producer(partial_path)
        if not partial_path.is_file():
            raise TransferError("transfer producer did not create the partial artifact")
        _boundary(boundary_hook, "single:after_write")
        _check_cancel(cancel_requested)
        _sync_file(partial_path)
        _boundary(boundary_hook, "single:after_file_fsync")
        verifier(partial_path)
        _boundary(boundary_hook, "single:after_verify")
        _check_cancel(cancel_requested)
        _fsync_directory(root)
        _boundary(boundary_hook, "single:before_rename")
        _replace_path(partial_path, final_path)
        committed = True
        _boundary(boundary_hook, "single:after_rename")
        _fsync_directory(root)
        _boundary(boundary_hook, "single:after_parent_fsync")
        return PublicationResult(
            path=final_path,
            mode=PublicationMode.SINGLE_FILE,
            bytes_published=final_path.stat().st_size,
            volumes=(),
        )
    except BaseException:
        if not committed:
            partial_path.unlink(missing_ok=True)
        raise


def publish_multipart(
    destination: Path,
    set_name: str,
    *,
    volume_producers: Sequence[FileProducer],
    controller_producer: ControllerProducer,
    volume_verifier: FileVerifier,
    controller_verifier: FileVerifier,
    part_limit_bytes: int,
    cancel_requested: CancelCheck | None = None,
    boundary_hook: BoundaryHook | None = None,
) -> PublicationResult:
    root = destination.expanduser().resolve(strict=True)
    _validate_leaf_name(set_name)
    if len(volume_producers) <= 1 or len(volume_producers) > (2**32 - 1):
        raise TransferError("multipart publication requires two or more volumes")
    if part_limit_bytes <= ARCHIVE_FRAME_RESERVE_BYTES:
        raise TransferError("multipart part limit is too small")
    partial_directory = root / f"{set_name}.partial"
    final_directory = root / set_name
    if partial_directory.exists() or final_directory.exists():
        raise TransferError("transfer publication target already exists")

    committed = False
    volumes: list[PublishedVolume] = []
    try:
        partial_directory.mkdir()
        _fsync_directory(root)
        _boundary(boundary_hook, "multipart:after_partial_directory")
        for ordinal, producer in enumerate(volume_producers, start=1):
            _check_cancel(cancel_requested)
            stem = f"volume-{ordinal:04d}.anima-part"
            partial_part = partial_directory / f"{stem}.partial"
            final_part = partial_directory / stem
            _boundary(boundary_hook, f"multipart:part:{ordinal}:before_write")
            producer(partial_part)
            if not partial_part.is_file():
                raise TransferError("transfer producer did not create a partial volume")
            length = partial_part.stat().st_size
            if length > part_limit_bytes:
                raise TransferError("transfer volume exceeds the destination file limit")
            _boundary(boundary_hook, f"multipart:part:{ordinal}:after_write")
            _check_cancel(cancel_requested)
            _sync_file(partial_part)
            _boundary(boundary_hook, f"multipart:part:{ordinal}:after_file_fsync")
            volume_verifier(partial_part)
            digest = _sha256_file(partial_part)
            _boundary(boundary_hook, f"multipart:part:{ordinal}:after_verify")
            _replace_path(partial_part, final_part)
            _boundary(boundary_hook, f"multipart:part:{ordinal}:after_rename")
            _fsync_directory(partial_directory)
            volumes.append(
                PublishedVolume(
                    ordinal=ordinal,
                    filename=stem,
                    length=length,
                    sha256=digest,
                )
            )

        _check_cancel(cancel_requested)
        controller_partial = partial_directory / "core.anima.partial"
        controller_final = partial_directory / "core.anima"
        _boundary(boundary_hook, "multipart:controller:before_write")
        controller_producer(controller_partial, tuple(volumes))
        if not controller_partial.is_file():
            raise TransferError("transfer producer did not create the controller")
        _boundary(boundary_hook, "multipart:controller:after_write")
        _sync_file(controller_partial)
        controller_verifier(controller_partial)
        _boundary(boundary_hook, "multipart:controller:after_verify")
        _check_cancel(cancel_requested)
        _replace_path(controller_partial, controller_final)
        _boundary(boundary_hook, "multipart:controller:after_rename")
        _fsync_directory(partial_directory)
        _boundary(boundary_hook, "multipart:before_directory_rename")
        _replace_path(partial_directory, final_directory)
        committed = True
        _boundary(boundary_hook, "multipart:after_directory_rename")
        _fsync_directory(root)
        _boundary(boundary_hook, "multipart:after_parent_fsync")
        controller_bytes = (final_directory / "core.anima").stat().st_size
        return PublicationResult(
            path=final_directory,
            mode=PublicationMode.MULTIPART,
            bytes_published=controller_bytes + sum(volume.length for volume in volumes),
            volumes=tuple(volumes),
        )
    except BaseException:
        if not committed:
            shutil.rmtree(partial_directory, ignore_errors=True)
        raise


def _probe_writable_atomic_directory(destination: Path) -> None:
    token = uuid4().hex
    source = destination / f".anima-transfer-probe-{token}.partial"
    target = destination / f".anima-transfer-probe-{token}"
    directory_source = destination / f".anima-transfer-dir-probe-{token}.partial"
    directory_target = destination / f".anima-transfer-dir-probe-{token}"
    try:
        with source.open("xb") as handle:
            handle.write(b"anima-transfer-probe")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_path(source, target)
        directory_source.mkdir()
        _replace_path(directory_source, directory_target)
        _fsync_directory(destination)
    except OSError as exc:
        raise TransferError("transfer destination is not writable with atomic rename") from exc
    finally:
        source.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        shutil.rmtree(directory_source, ignore_errors=True)
        shutil.rmtree(directory_target, ignore_errors=True)


def _filesystem_name(destination: Path) -> str | None:
    system = platform.system()
    command: list[str] | None = None
    if system == "Linux":
        command = ["stat", "-f", "-c", "%T", os.fspath(destination)]
    elif system == "Darwin":
        command = ["stat", "-f", "%T", os.fspath(destination)]
    if command is None:
        return None
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _sync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # Windows publication renames use MoveFileExW with WRITE_THROUGH below.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_path(source: Path, destination: Path) -> None:
    if os.name != "nt":
        os.replace(source, destination)
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    move_file.restype = wintypes.BOOL
    movefile_replace_existing = 0x00000001
    movefile_write_through = 0x00000008
    if not move_file(
        os.fspath(source),
        os.fspath(destination),
        movefile_replace_existing | movefile_write_through,
    ):
        raise OSError(ctypes.get_last_error(), "MoveFileExW durable rename failed")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_BUFFER_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_leaf_name(value: str) -> None:
    if not value or value in {".", ".."} or Path(value).name != value or "\x00" in value:
        raise TransferError("transfer publication name is unsafe")


def _check_cancel(cancel_requested: CancelCheck | None) -> None:
    if cancel_requested is not None and cancel_requested():
        raise TransferCancelled("transfer was cancelled")


def _boundary(hook: BoundaryHook | None, name: str) -> None:
    if hook is not None:
        hook(name)
