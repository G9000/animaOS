from __future__ import annotations

import hashlib
import math
import os
import platform
import shutil
import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

ARCHIVE_FRAME_RESERVE_BYTES = 1024 * 1024
CAPACITY_MARGIN_BYTES = 64 * 1024 * 1024
DEFAULT_PART_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
HASH_BUFFER_BYTES = 1024 * 1024
FAT32_MAX_FILE_BYTES = (4 * 1024 * 1024 * 1024) - 1
FAT16_MAX_FILE_BYTES = (2 * 1024 * 1024 * 1024) - 1


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


BoundaryHook = Callable[[str], None]
CancelCheck = Callable[[], bool]
FileProducer = Callable[[Path], None]
FileVerifier = Callable[[Path], None]
ControllerProducer = Callable[[Path, tuple[PublishedVolume, ...]], None]


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
    token = uuid.uuid4().hex
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
