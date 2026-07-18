"""Run and record the CoreFS V1 full-catalog durable publication benchmark."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import ntpath
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple

MAX_CATALOG_PLAINTEXT_BYTES = 16 * 1024 * 1024
REFERENCE_WARMUPS = 30
REFERENCE_SAMPLES = 200
MAX_REFERENCE_SAMPLES = 999 - 2 - REFERENCE_WARMUPS
REFERENCE_DURABLE_WRITE_SAMPLES = 200
REFERENCE_DURABLE_WRITE_WARMUPS = 30
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT = (
    REPO_ROOT / "docs" / "benchmarks" / "portable-core-filesystem" / "catalog-reference-v1.json"
)
REFERENCE_TARGET_RELATIVE = Path("animaOS") / "benchmarks" / "corefs-catalog-reference-v1"
TARGET_SENTINEL_NAME = ".anima-corefs-catalog-benchmark-target-v1"
TARGET_SENTINEL_CONTENT = b"animaOS CoreFS catalog benchmark target v1\n"
EXPECTED_FIXTURE_MANIFEST_FINGERPRINTS = {
    "medium": "d1f8817ba635359cc10208d86b79652dc0e2180c2514f1e1d0634a96ebcb40c4",
    "maximum-live": "1c37d0254fbb9852b5789fa39811f0e1a23a4a3ae440b20c9c478fbf8bf9f7b5",
    "serialized-limit": "26c1c693e8b564e6a971c0af6b62b9b223612bea8bc7c0fe71388abfb06fbd87",
}

FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
CLOUD_OR_REPARSE_ATTRIBUTES = (
    FILE_ATTRIBUTE_REPARSE_POINT
    | FILE_ATTRIBUTE_OFFLINE
    | FILE_ATTRIBUTE_RECALL_ON_OPEN
    | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


class ReferenceTargetError(RuntimeError):
    """The requested reference target does not meet the storage contract."""


class ReportValidationError(RuntimeError):
    """The Rust runner returned an incomplete or inconsistent report."""


class MaximumLiveSizeGateError(ReportValidationError):
    """The maximum-live fixture exceeded the declared V1 support envelope."""


class ReferenceVolumeFacts(NamedTuple):
    volume_root: Path
    drive_type: str
    filesystem: str
    synchronized_roots: tuple[Path, ...]


class WriteCacheEvidence(NamedTuple):
    write_cache_type: str
    write_cache_enabled: bool | None
    write_cache_changeable: str
    write_through_supported: bool
    flush_cache_supported: bool
    user_defined_power_protection: bool
    nv_cache_enabled: bool


class ReferenceHostFacts(NamedTuple):
    volume: ReferenceVolumeFacts
    os_caption: str
    os_version: str
    os_architecture: str
    cpu_name: str
    cpu_architecture_codes: tuple[int, ...]
    physical_cores: int
    logical_processors: int
    ram_bytes: int
    partition_disk_number: int
    disk_number: int
    physical_device_id: str
    disk_model: str
    disk_serial: str
    physical_model: str
    physical_serial: str
    disk_bus_type: str
    physical_bus_type: str
    physical_media_type: str
    disk_health_status: str
    disk_operational_status: str
    physical_health_status: str
    physical_operational_status: str
    disk_is_offline: bool
    disk_is_read_only: bool
    disk_location: str
    physical_location: str
    hardware_power_protection: str
    write_cache: WriteCacheEvidence


class ReferencePathEvidence(NamedTuple):
    canonical_path: Path
    volume_serial: int
    file_id: int
    attributes: int


class HeldBenchmarkBinary(NamedTuple):
    evidence: ReferencePathEvidence
    sha256: str
    hash_probe: Callable[[], str]


class HeldReferenceTargetChain(NamedTuple):
    paths: tuple[Path, ...]
    evidence: tuple[ReferencePathEvidence, ...]


class BenchmarkBuildEvidence(NamedTuple):
    binary: Path
    command: tuple[str, ...]
    target_directory: Path
    cargo_lock_sha256: str
    rustc: str
    sanitized_environment_removed: tuple[str, ...]


POWERSHELL_LIVE_PROFILE = r"""
$ErrorActionPreference = 'Stop'
$driveLetter = $env:ANIMA_CORE_FS_BENCHMARK_DRIVE
if ($driveLetter -notmatch '^[A-Z]$') { throw 'invalid benchmark drive letter' }
$os = Get-CimInstance Win32_OperatingSystem
$cpus = @(Get-CimInstance Win32_Processor)
$partition = Get-Partition -DriveLetter $driveLetter
$disk = $partition | Get-Disk
$physical = Get-PhysicalDisk | Where-Object { [string]$_.DeviceId -eq [string]$disk.Number }
if (@($partition).Count -ne 1 -or @($disk).Count -ne 1 -or @($physical).Count -ne 1) {
    throw 'target volume did not map to exactly one partition, disk, and physical disk'
}
$payload = [ordered]@{
    schemaVersion = 1
    os = [ordered]@{
        caption = [string]$os.Caption
        version = [string]$os.Version
        architecture = [string]$os.OSArchitecture
        totalVisibleMemoryKiB = [uint64]$os.TotalVisibleMemorySize
    }
    cpu = [ordered]@{
        name = (($cpus | ForEach-Object { ([string]$_.Name).Trim() }) -join '; ')
        architectureCodes = @($cpus | ForEach-Object { [int]$_.Architecture })
        physicalCores = [int](($cpus | Measure-Object -Property NumberOfCores -Sum).Sum)
        logicalProcessors = [int](($cpus | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
    }
    partition = [ordered]@{
        driveLetter = [string]$partition.DriveLetter
        diskNumber = [int]$partition.DiskNumber
    }
    disk = [ordered]@{
        number = [int]$disk.Number
        friendlyName = [string]$disk.FriendlyName
        serialNumber = [string]$disk.SerialNumber
        busType = [string]$disk.BusType
        healthStatus = [string]$disk.HealthStatus
        operationalStatus = [string]$disk.OperationalStatus
        isOffline = [bool]$disk.IsOffline
        isReadOnly = [bool]$disk.IsReadOnly
        location = [string]$disk.Location
    }
    physicalDisk = [ordered]@{
        deviceId = [string]$physical.DeviceId
        friendlyName = [string]$physical.FriendlyName
        serialNumber = [string]$physical.SerialNumber
        busType = [string]$physical.BusType
        mediaType = [string]$physical.MediaType
        healthStatus = [string]$physical.HealthStatus
        operationalStatus = [string]$physical.OperationalStatus
        isPowerProtected = $physical.IsPowerProtected
        physicalLocation = [string]$physical.PhysicalLocation
    }
}
$payload | ConvertTo-Json -Compress -Depth 6
"""


def _is_within(path: Path, root: Path) -> bool:
    path_value = os.path.normcase(str(path.resolve(strict=False)))
    root_value = os.path.normcase(str(root.resolve(strict=False)))
    try:
        return os.path.commonpath([path_value, root_value]) == root_value
    except ValueError:
        return False


def loads_strict_json(payload: str) -> Any:
    """Decode JSON while rejecting ambiguity and values outside RFC 8259."""

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReportValidationError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    def reject_non_finite(value: str) -> Any:
        raise ReportValidationError(f"non-finite JSON number is not allowed: {value}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
    except json.JSONDecodeError as error:
        raise ReportValidationError("invalid JSON") from error


def probe_registered_synchronized_roots() -> tuple[Path, ...]:
    """Read user-registered sync roots, including all OneDrive accounts."""

    if os.name != "nt":
        raise OSError("registered sync-root probing requires Windows")
    import winreg

    roots: list[Path] = []

    def add_value(value: object) -> None:
        if isinstance(value, str) and value.strip():
            roots.append(Path(os.path.expandvars(value.strip())))

    accounts_path = r"Software\Microsoft\OneDrive\Accounts"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, accounts_path) as accounts:
            index = 0
            while True:
                try:
                    account_name = winreg.EnumKey(accounts, index)
                except OSError as error:
                    if getattr(error, "winerror", None) == 259:
                        break
                    raise
                index += 1
                with winreg.OpenKey(accounts, account_name) as account:
                    try:
                        add_value(winreg.QueryValueEx(account, "UserFolder")[0])
                    except FileNotFoundError:
                        continue
    except FileNotFoundError:
        pass

    manager_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, manager_path) as manager:
            index = 0
            while True:
                try:
                    provider_name = winreg.EnumKey(manager, index)
                except OSError as error:
                    if getattr(error, "winerror", None) == 259:
                        break
                    raise
                index += 1
                try:
                    with winreg.OpenKey(manager, provider_name + r"\UserSyncRoots") as provider:
                        value_index = 0
                        while True:
                            try:
                                _name, value, _kind = winreg.EnumValue(provider, value_index)
                            except OSError as error:
                                if getattr(error, "winerror", None) == 259:
                                    break
                                raise
                            value_index += 1
                            add_value(value)
                except FileNotFoundError:
                    continue
    except FileNotFoundError:
        pass
    return tuple(roots)


def collect_synchronized_roots(
    *,
    environ: Mapping[str, str] = os.environ,
    registry_probe: Callable[[], tuple[Path, ...]] = probe_registered_synchronized_roots,
) -> tuple[Path, ...]:
    try:
        discovered = list(registry_probe())
    except OSError as error:
        raise ReferenceTargetError(
            f"registered sync-root registry probe failed: {error}"
        ) from error
    for variable in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        value = environ.get(variable)
        if value:
            discovered.append(Path(value))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in discovered:
        key = os.path.normcase(str(path.expanduser().resolve(strict=False)))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique)


def _validate_reference_volume(target: Path, facts: ReferenceVolumeFacts) -> ReferenceVolumeFacts:
    resolved = target.expanduser().resolve(strict=False)
    if facts.drive_type.casefold() != "fixed":
        raise ReferenceTargetError(
            f"reference target must be on a fixed local drive, got {facts.drive_type}"
        )
    if facts.filesystem.casefold() != "ntfs":
        raise ReferenceTargetError(f"reference target must use NTFS, got {facts.filesystem}")
    for synchronized_root in facts.synchronized_roots:
        if _is_within(resolved, synchronized_root):
            raise ReferenceTargetError(
                f"reference target is inside synchronized storage: {synchronized_root}"
            )
    return facts


def probe_reference_volume(target: Path) -> ReferenceVolumeFacts:
    if os.name != "nt":
        raise ReferenceTargetError("reference mode requires Windows 11 x64")
    existing = target.expanduser().resolve(strict=False)
    while not existing.exists():
        parent = existing.parent
        if parent == existing:
            raise ReferenceTargetError("cannot resolve reference target volume")
        existing = parent

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetVolumePathNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumePathNameW.restype = wintypes.BOOL
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    kernel32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumeInformationW.restype = wintypes.BOOL
    volume_buffer = ctypes.create_unicode_buffer(261)
    if not kernel32.GetVolumePathNameW(str(existing), volume_buffer, len(volume_buffer)):
        raise ReferenceTargetError(
            f"GetVolumePathNameW failed with error {ctypes.get_last_error()}"
        )
    volume_root = volume_buffer.value
    drive_type_code = int(kernel32.GetDriveTypeW(volume_root))
    drive_types = {
        0: "unknown",
        1: "invalid",
        2: "removable",
        3: "fixed",
        4: "network",
        5: "optical",
        6: "ramdisk",
    }

    filesystem_buffer = ctypes.create_unicode_buffer(261)
    if not kernel32.GetVolumeInformationW(
        volume_root,
        None,
        0,
        None,
        None,
        None,
        filesystem_buffer,
        len(filesystem_buffer),
    ):
        raise ReferenceTargetError(
            f"GetVolumeInformationW failed with error {ctypes.get_last_error()}"
        )
    return ReferenceVolumeFacts(
        volume_root=Path(volume_root),
        drive_type=drive_types.get(drive_type_code, "unknown"),
        filesystem=filesystem_buffer.value,
        synchronized_roots=collect_synchronized_roots(),
    )


def probe_local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if os.name != "nt" or not value:
        raise ReferenceTargetError("cannot resolve the Windows LOCALAPPDATA root")
    return Path(value)


def probe_reference_path(path: Path) -> ReferencePathEvidence:
    """Open a path without following its final reparse point and return handle identity."""

    if os.name != "nt":
        raise OSError("reference path identity probing requires Windows")

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(
        str(path),
        0,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), f"cannot open path identity: {path}")
    try:
        information = ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise OSError(ctypes.get_last_error(), f"cannot read path identity: {path}")
        capacity = 32768
        buffer = ctypes.create_unicode_buffer(capacity)
        length = int(kernel32.GetFinalPathNameByHandleW(handle, buffer, capacity, 0))
        if length == 0 or length >= capacity:
            raise OSError(ctypes.get_last_error(), f"cannot canonicalize path handle: {path}")
        canonical = buffer.value
        if canonical.startswith("\\\\?\\UNC\\"):
            canonical = "\\\\" + canonical[8:]
        elif canonical.startswith("\\\\?\\"):
            canonical = canonical[4:]
        file_id = (int(information.file_index_high) << 32) | int(information.file_index_low)
        return ReferencePathEvidence(
            canonical_path=Path(canonical),
            volume_serial=int(information.volume_serial_number),
            file_id=file_id,
            attributes=int(information.attributes),
        )
    finally:
        kernel32.CloseHandle(handle)


def _same_resolved_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def validate_reference_target_location(
    target: Path,
    artifact: Path,
    local_app_data_root: Path,
    synchronized_roots: Sequence[Path],
) -> Path:
    resolved = target.expanduser().resolve(strict=False)
    local = local_app_data_root.expanduser().resolve(strict=False)
    expected = (local / REFERENCE_TARGET_RELATIVE).resolve(strict=False)
    if not _same_resolved_path(resolved, expected):
        raise ReferenceTargetError(
            f"reference target must be the dedicated benchmark directory: {expected}; "
            f"refusing dangerous or arbitrary target {resolved}"
        )
    artifact_path = artifact.expanduser().resolve(strict=False)
    if _is_within(artifact_path, resolved) or _same_resolved_path(artifact_path, resolved):
        raise ReferenceTargetError("benchmark artifact cannot be inside the benchmark target")
    for synchronized_root in synchronized_roots:
        if _is_within(resolved, synchronized_root):
            raise ReferenceTargetError(
                f"reference target is inside synchronized storage: {synchronized_root}"
            )
    return resolved


def _validated_path_evidence(
    path: Path,
    path_probe: Callable[[Path], ReferencePathEvidence],
) -> ReferencePathEvidence:
    try:
        evidence = path_probe(path)
    except OSError as error:
        raise ReferenceTargetError(f"reference path probe failed for {path}: {error}") from error
    if not _same_resolved_path(evidence.canonical_path, path):
        raise ReferenceTargetError(f"path handle canonicalization changed for {path}")
    if evidence.attributes & CLOUD_OR_REPARSE_ATTRIBUTES:
        raise ReferenceTargetError(
            f"refusing reparse, offline, or Cloud Files reference path: {path}"
        )
    return evidence


def _same_identity(left: ReferencePathEvidence, right: ReferencePathEvidence) -> bool:
    return (
        _same_resolved_path(left.canonical_path, right.canonical_path)
        and left.volume_serial == right.volume_serial
        and left.file_id == right.file_id
    )


def prepare_reference_target(
    target: Path,
    artifact: Path,
    *,
    local_app_data_probe: Callable[[], Path] = probe_local_app_data,
    sync_roots_probe: Callable[[], tuple[Path, ...]] = collect_synchronized_roots,
    path_probe: Callable[[Path], ReferencePathEvidence] = probe_reference_path,
) -> Path:
    """Validate and create a fresh dedicated target without deleting existing data."""

    try:
        local = local_app_data_probe().expanduser().resolve(strict=False)
        synchronized_roots = sync_roots_probe()
    except ReferenceTargetError:
        raise
    except OSError as error:
        raise ReferenceTargetError(f"reference location probe failed: {error}") from error
    resolved = validate_reference_target_location(target, artifact, local, synchronized_roots)

    relative_parts = resolved.relative_to(local).parts
    candidate = local
    for part in ("", *relative_parts):
        if part:
            candidate /= part
        if os.path.lexists(candidate):
            _validated_path_evidence(candidate, path_probe)
            if _same_resolved_path(candidate, resolved):
                raise ReferenceTargetError(
                    "reference target already exists; archive it manually after verifying the "
                    "exact canonical path, then rerun"
                )

    try:
        resolved.mkdir(parents=True, exist_ok=False)
        sentinel = resolved / TARGET_SENTINEL_NAME
        with sentinel.open("xb") as handle:
            handle.write(TARGET_SENTINEL_CONTENT)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise ReferenceTargetError(f"cannot create benchmark target sentinel: {error}") from error
    _validated_path_evidence(resolved, path_probe)
    _validated_path_evidence(sentinel, path_probe)
    return resolved


def _strict_object(value: object, expected_keys: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReferenceTargetError(f"{context} must be an object")
    actual_keys = set(value)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ReferenceTargetError(
            f"{context} fields are incomplete or unexpected; missing={missing}, extra={extra}"
        )
    return value


def _strict_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceTargetError(f"{context} must be a non-empty string")
    return value.strip()


def _strict_int(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReferenceTargetError(f"{context} must be an integer")
    return value


def _strict_bool(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise ReferenceTargetError(f"{context} must be boolean")
    return value


def _strict_int_list(value: object, context: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ReferenceTargetError(f"{context} must be a non-empty integer list")
    return tuple(_strict_int(item, context) for item in value)


def _normalize_serial(value: str) -> str:
    return re.sub(r"[\s.]", "", value).casefold()


def probe_storage_write_cache(disk_number: int) -> WriteCacheEvidence:
    if os.name != "nt":
        raise ReferenceTargetError("storage write-cache probing requires Windows")

    class StoragePropertyQuery(ctypes.Structure):
        _fields_ = [
            ("property_id", wintypes.DWORD),
            ("query_type", wintypes.DWORD),
            ("additional_parameters", ctypes.c_ubyte * 1),
        ]

    class StorageWriteCacheProperty(ctypes.Structure):
        _fields_ = [
            ("version", wintypes.DWORD),
            ("size", wintypes.DWORD),
            ("write_cache_type", wintypes.DWORD),
            ("write_cache_enabled", wintypes.DWORD),
            ("write_cache_changeable", wintypes.DWORD),
            ("write_through_supported", wintypes.DWORD),
            ("flush_cache_supported", wintypes.BOOLEAN),
            ("user_defined_power_protection", wintypes.BOOLEAN),
            ("nv_cache_enabled", wintypes.BOOLEAN),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(
        rf"\\.\PhysicalDrive{disk_number}",
        0,
        0x00000001 | 0x00000002,
        None,
        3,
        0,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ReferenceTargetError(
            f"cannot open mapped physical disk for durability proof: {ctypes.get_last_error()}"
        )
    try:
        query = StoragePropertyQuery(4, 0, (ctypes.c_ubyte * 1)(0))
        result = StorageWriteCacheProperty()
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(
            handle,
            0x002D1400,
            ctypes.byref(query),
            ctypes.sizeof(query),
            ctypes.byref(result),
            ctypes.sizeof(result),
            ctypes.byref(returned),
            None,
        ):
            raise ReferenceTargetError(
                "mapped physical disk did not expose write-cache durability properties "
                f"(error {ctypes.get_last_error()})"
            )
        if returned.value < ctypes.sizeof(result) or result.size < ctypes.sizeof(result):
            raise ReferenceTargetError("physical disk write-cache property was truncated")
    finally:
        kernel32.CloseHandle(handle)

    cache_types = {0: "unknown", 1: "none", 2: "write-back", 3: "write-through"}
    cache_enabled = {0: None, 1: False, 2: True}
    cache_changeable = {0: "unknown", 1: "not-changeable", 2: "changeable"}
    write_through = {0: False, 1: False, 2: True}
    if (
        result.write_cache_type not in cache_types
        or result.write_cache_enabled not in cache_enabled
        or result.write_cache_changeable not in cache_changeable
        or result.write_through_supported not in write_through
    ):
        raise ReferenceTargetError("physical disk returned unknown write-cache enum values")
    return WriteCacheEvidence(
        write_cache_type=cache_types[result.write_cache_type],
        write_cache_enabled=cache_enabled[result.write_cache_enabled],
        write_cache_changeable=cache_changeable[result.write_cache_changeable],
        write_through_supported=write_through[result.write_through_supported],
        flush_cache_supported=bool(result.flush_cache_supported),
        user_defined_power_protection=bool(result.user_defined_power_protection),
        nv_cache_enabled=bool(result.nv_cache_enabled),
    )


def probe_live_reference_host(
    target: Path,
    *,
    volume_probe: Callable[[Path], ReferenceVolumeFacts] = probe_reference_volume,
    cache_probe: Callable[[int], WriteCacheEvidence] = probe_storage_write_cache,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ReferenceHostFacts:
    if os.name != "nt":
        raise ReferenceTargetError("reference mode requires Windows 11 x64")
    return _probe_live_reference_host_from_sources(
        target,
        volume_probe=volume_probe,
        cache_probe=cache_probe,
        runner=runner,
    )


def _windows_path(value: str | os.PathLike[str]) -> PureWindowsPath:
    return PureWindowsPath(str(value))


def _windows_drive_letter(value: str | os.PathLike[str]) -> str:
    return _windows_path(value).drive.rstrip(":").upper()


def _same_windows_path(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    return ntpath.normcase(ntpath.normpath(str(left))) == ntpath.normcase(
        ntpath.normpath(str(right))
    )


def _probe_live_reference_host_from_sources(
    target: Path,
    *,
    volume_probe: Callable[[Path], ReferenceVolumeFacts],
    cache_probe: Callable[[int], WriteCacheEvidence],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> ReferenceHostFacts:
    volume = volume_probe(target)
    drive = _windows_drive_letter(volume.volume_root)
    if len(drive) != 1 or not drive.isascii() or not drive.isalpha():
        raise ReferenceTargetError("reference target volume has no local drive letter")
    environment = os.environ.copy()
    environment["ANIMA_CORE_FS_BENCHMARK_DRIVE"] = drive
    try:
        completed = runner(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                POWERSHELL_LIVE_PROFILE,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReferenceTargetError(f"live Windows hardware probe failed: {error}") from error
    try:
        raw = loads_strict_json(completed.stdout)
    except (ReportValidationError, TypeError) as error:
        raise ReferenceTargetError("live Windows hardware probe returned invalid JSON") from error
    payload = _strict_object(
        raw,
        {"schemaVersion", "os", "cpu", "partition", "disk", "physicalDisk"},
        "live hardware probe",
    )
    if type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1:
        raise ReferenceTargetError("live hardware probe schemaVersion must be 1")
    os_data = _strict_object(
        payload["os"],
        {"caption", "version", "architecture", "totalVisibleMemoryKiB"},
        "live OS evidence",
    )
    cpu = _strict_object(
        payload["cpu"],
        {"name", "architectureCodes", "physicalCores", "logicalProcessors"},
        "live CPU evidence",
    )
    partition = _strict_object(
        payload["partition"], {"driveLetter", "diskNumber"}, "partition mapping"
    )
    disk = _strict_object(
        payload["disk"],
        {
            "number",
            "friendlyName",
            "serialNumber",
            "busType",
            "healthStatus",
            "operationalStatus",
            "isOffline",
            "isReadOnly",
            "location",
        },
        "disk mapping",
    )
    physical = _strict_object(
        payload["physicalDisk"],
        {
            "deviceId",
            "friendlyName",
            "serialNumber",
            "busType",
            "mediaType",
            "healthStatus",
            "operationalStatus",
            "isPowerProtected",
            "physicalLocation",
        },
        "physical disk mapping",
    )
    disk_number = _strict_int(disk["number"], "disk number")
    if _strict_string(partition["driveLetter"], "partition drive letter").upper() != drive:
        raise ReferenceTargetError("partition mapping does not match the target volume")
    power_protected = physical["isPowerProtected"]
    if power_protected is not None and not isinstance(power_protected, bool):
        raise ReferenceTargetError("physical disk power-protection evidence is invalid")
    hardware_power_protection = (
        "reported-true"
        if power_protected is True
        else "reported-false"
        if power_protected is False
        else "unknown-not-reported"
    )
    return ReferenceHostFacts(
        volume=volume,
        os_caption=_strict_string(os_data["caption"], "OS caption"),
        os_version=_strict_string(os_data["version"], "OS version"),
        os_architecture=_strict_string(os_data["architecture"], "OS architecture"),
        cpu_name=_strict_string(cpu["name"], "CPU name"),
        cpu_architecture_codes=_strict_int_list(cpu["architectureCodes"], "CPU architecture code"),
        physical_cores=_strict_int(cpu["physicalCores"], "physical core count"),
        logical_processors=_strict_int(cpu["logicalProcessors"], "logical processor count"),
        ram_bytes=_strict_int(os_data["totalVisibleMemoryKiB"], "visible memory") * 1024,
        partition_disk_number=_strict_int(partition["diskNumber"], "partition disk number"),
        disk_number=disk_number,
        physical_device_id=_strict_string(physical["deviceId"], "physical device ID"),
        disk_model=_strict_string(disk["friendlyName"], "disk model"),
        disk_serial=_strict_string(disk["serialNumber"], "disk serial"),
        physical_model=_strict_string(physical["friendlyName"], "physical disk model"),
        physical_serial=_strict_string(physical["serialNumber"], "physical disk serial"),
        disk_bus_type=_strict_string(disk["busType"], "disk bus type"),
        physical_bus_type=_strict_string(physical["busType"], "physical disk bus type"),
        physical_media_type=_strict_string(physical["mediaType"], "physical disk media type"),
        disk_health_status=_strict_string(disk["healthStatus"], "disk health status"),
        disk_operational_status=_strict_string(
            disk["operationalStatus"], "disk operational status"
        ),
        physical_health_status=_strict_string(
            physical["healthStatus"], "physical disk health status"
        ),
        physical_operational_status=_strict_string(
            physical["operationalStatus"], "physical disk operational status"
        ),
        disk_is_offline=_strict_bool(disk["isOffline"], "disk offline state"),
        disk_is_read_only=_strict_bool(disk["isReadOnly"], "disk read-only state"),
        disk_location=_strict_string(disk["location"], "disk location"),
        physical_location=_strict_string(physical["physicalLocation"], "physical disk location"),
        hardware_power_protection=hardware_power_protection,
        write_cache=cache_probe(disk_number),
    )


def validate_reference_profile(target: Path, facts: ReferenceHostFacts) -> ReferenceHostFacts:
    _validate_reference_volume(target, facts.volume)
    try:
        version = tuple(int(part) for part in facts.os_version.split("."))
    except ValueError as error:
        raise ReferenceTargetError("Windows 11 version evidence is invalid") from error
    if (
        "windows 11" not in facts.os_caption.casefold()
        or len(version) < 3
        or version[0:2] != (10, 0)
        or version[2] < 22_000
    ):
        raise ReferenceTargetError("reference profile requires live Windows 11 evidence")
    if facts.os_architecture.casefold() not in {"64-bit", "x64", "amd64"} or any(
        code != 9 for code in facts.cpu_architecture_codes
    ):
        raise ReferenceTargetError("reference profile requires x64 architecture")
    if facts.physical_cores < 4:
        raise ReferenceTargetError("reference profile requires at least 4 physical cores")
    if facts.ram_bytes < 16 * 1024**3:
        raise ReferenceTargetError("reference profile requires at least 16 GiB RAM")
    if not (
        facts.partition_disk_number == facts.disk_number
        and facts.physical_device_id == str(facts.disk_number)
        and _normalize_serial(facts.disk_serial) == _normalize_serial(facts.physical_serial)
        and facts.disk_model.casefold() == facts.physical_model.casefold()
    ):
        raise ReferenceTargetError("target-to-physical-disk mapping is inconsistent")
    if facts.disk_bus_type.casefold() != "nvme" or facts.physical_bus_type.casefold() != "nvme":
        raise ReferenceTargetError("reference storage must map to NVMe")
    if facts.physical_media_type.casefold() != "ssd":
        raise ReferenceTargetError("reference storage must map to an SSD")
    if not (
        facts.disk_location.casefold().startswith("integrated")
        and facts.physical_location.casefold().startswith("integrated")
    ):
        raise ReferenceTargetError("reference storage must be internal")
    if (
        facts.disk_health_status.casefold() != "healthy"
        or facts.physical_health_status.casefold() != "healthy"
    ):
        raise ReferenceTargetError("reference storage must be healthy")
    if (
        facts.disk_operational_status.casefold() != "online"
        or facts.physical_operational_status.casefold() not in {"ok", "online"}
        or facts.disk_is_offline
        or facts.disk_is_read_only
    ):
        raise ReferenceTargetError("reference storage must be online")
    if not (facts.write_cache.write_through_supported and facts.write_cache.flush_cache_supported):
        raise ReferenceTargetError(
            "mapped storage cannot prove write-cache durability through flush and write-through support"
        )
    return facts


def validate_reference_run_counts(warmups: int, samples: int) -> None:
    if warmups != REFERENCE_WARMUPS:
        raise ReferenceTargetError("reference mode requires exactly 30 warmups")
    if samples < REFERENCE_SAMPLES:
        raise ReferenceTargetError("reference mode requires at least 200 measured commits")
    if samples > MAX_REFERENCE_SAMPLES:
        raise ReferenceTargetError(
            f"reference mode requires at most {MAX_REFERENCE_SAMPLES} measured commits"
        )


def percentile_nearest_rank(samples: Sequence[float], percentile: float) -> float:
    if not samples:
        raise ValueError("percentile requires at least one sample")
    if not 0.0 <= percentile <= 1.0:
        raise ValueError("percentile must be between zero and one")
    ordered = sorted(samples)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _flush_file_buffers(descriptor: int) -> None:
    if os.name != "nt":
        raise ReferenceTargetError("FlushFileBuffers evidence requires Windows")
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = [wintypes.HANDLE]
    flush_file_buffers.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(descriptor)
    if not flush_file_buffers(wintypes.HANDLE(handle)):
        raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")


def measure_durable_write_4k(
    target: Path,
    *,
    warmups: int = REFERENCE_DURABLE_WRITE_WARMUPS,
    samples: int = REFERENCE_DURABLE_WRITE_SAMPLES,
) -> dict[str, float | int | str]:
    probe_dir = target / "durable-write-4k"
    probe_dir.mkdir(parents=True, exist_ok=False)
    payload = bytes(index % 251 for index in range(4096))
    timings_ms: list[float] = []
    flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    active_probe: Path | None = None
    try:
        for index in range(warmups + samples):
            probe = probe_dir / f"probe-{index:04}.bin"
            active_probe = probe
            started = time.perf_counter_ns()
            descriptor = os.open(probe, flags, 0o600)
            try:
                written = os.write(descriptor, payload)
                if written != len(payload):
                    raise OSError(f"short durable-write probe: {written} bytes")
                _flush_file_buffers(descriptor)
            finally:
                os.close(descriptor)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
            probe.unlink()
            active_probe = None
            if index >= warmups:
                timings_ms.append(elapsed_ms)
    finally:
        if active_probe is not None:
            active_probe.unlink(missing_ok=True)
        try:
            probe_dir.rmdir()
        except OSError as error:
            raise ReferenceTargetError(
                f"durable-write probe directory was not empty after exact cleanup: {error}"
            ) from error
    return {
        "warmupCount": warmups,
        "sampleCount": samples,
        "p50Ms": percentile_nearest_rank(timings_ms, 0.50),
        "p95Ms": percentile_nearest_rank(timings_ms, 0.95),
        "p99Ms": percentile_nearest_rank(timings_ms, 0.99),
        "flushMethod": "FlushFileBuffers",
    }


def require_supported_maximum_live_size(serialized_size: int) -> None:
    if serialized_size > MAX_CATALOG_PLAINTEXT_BYTES:
        raise MaximumLiveSizeGateError(
            f"maximum-live fixture exceeds 16 MiB: {serialized_size} bytes"
        )


PUBLICATION_PATH = [
    "serialize",
    "encrypt",
    "temporary-file-write",
    "durable-flush",
    "atomic-rename",
    "directory-durability",
    "fs-head-write-flush",
    "commit-lock",
]
EXCLUDED_STORAGE_CLASSES = [
    "OneDrive-synchronized",
    "network",
    "removable",
    "RAM-disk",
    "write-cache-without-durability",
]


def _report_object(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportValidationError(f"{context} must be an object")
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise ReportValidationError(
            f"{context} fields are invalid (missing={missing}, extra={extra})"
        )
    return value


def _report_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportValidationError(f"{context} must be a non-empty string")
    return value


def _report_int(value: Any, context: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ReportValidationError(f"{context} must be an integer >= {minimum}")
    return value


def _report_number(value: Any, context: str, *, minimum: float = 0.0) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < minimum
    ):
        raise ReportValidationError(f"{context} must be a finite number >= {minimum}")
    return float(value)


def _report_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise ReportValidationError(f"{context} must be boolean")
    return value


def _size_range(value: Any, context: str) -> tuple[int, int]:
    size_range = _report_object(value, {"min", "max"}, context)
    minimum = _report_int(size_range["min"], f"{context}.min", minimum=1)
    maximum = _report_int(size_range["max"], f"{context}.max", minimum=1)
    if minimum > maximum:
        raise ReportValidationError(f"{context}.min cannot exceed max")
    return minimum, maximum


def _duration_percentiles(value: Any, context: str) -> tuple[float, float, float]:
    distribution = _report_object(value, {"p50", "p95", "p99"}, context)
    values = tuple(
        _report_number(distribution[key], f"{context}.{key}") for key in ("p50", "p95", "p99")
    )
    if values != tuple(sorted(values)):
        raise ReportValidationError(f"{context} percentiles must be monotonic")
    return values


def _validate_fixture(
    fixture: Any,
    *,
    expected_counts: tuple[int, int, int],
    warmups: int,
    samples: int,
) -> dict[str, Any]:
    record = _report_object(
        fixture,
        {
            "name",
            "liveCount",
            "tombstoneCount",
            "totalCount",
            "serializedSizeBytes",
            "warmupSerializedSizeBytes",
            "measuredSerializedSizeBytes",
            "fixtureManifestSha256",
            "productionSerializationsPerCommit",
            "warmupCommits",
            "sampleCount",
            "finalHeadGeneration",
            "finalCatalogCount",
            "bytesWritten",
            "totalBytesWritten",
            "commitMs",
            "lockHoldMs",
            "publicationPath",
        },
        "fixture",
    )
    name = _report_string(record["name"], "fixture.name")
    actual_counts = tuple(
        _report_int(record[key], f"{name}.{key}")
        for key in ("liveCount", "tombstoneCount", "totalCount")
    )
    if actual_counts != expected_counts:
        raise ReportValidationError(f"{name} fixture counts are not deterministic")
    fixture_warmups = _report_int(record["warmupCommits"], f"{name}.warmupCommits")
    fixture_samples = _report_int(record["sampleCount"], f"{name}.sampleCount")
    if fixture_warmups != warmups or fixture_samples != samples:
        raise ReportValidationError(f"{name} run counts contradict the report")
    expected_generation = 2 + warmups + samples
    final_head_generation = _report_int(
        record["finalHeadGeneration"], f"{name}.finalHeadGeneration", minimum=1
    )
    final_catalog_count = _report_int(
        record["finalCatalogCount"], f"{name}.finalCatalogCount", minimum=1
    )
    if final_head_generation != expected_generation or final_catalog_count != expected_generation:
        raise ReportValidationError(
            f"{name} final catalog count must match authoritative HEAD generation"
        )
    serialized_size = _report_int(
        record["serializedSizeBytes"], f"{name}.serializedSizeBytes", minimum=1
    )
    require_supported_maximum_live_size(serialized_size)
    warmup_range = _size_range(
        record["warmupSerializedSizeBytes"], f"{name}.warmupSerializedSizeBytes"
    )
    measured_range = _size_range(
        record["measuredSerializedSizeBytes"], f"{name}.measuredSerializedSizeBytes"
    )
    if serialized_size != measured_range[1]:
        raise ReportValidationError(f"{name}.serializedSizeBytes must equal the measured maximum")
    if (
        warmup_range[1] > MAX_CATALOG_PLAINTEXT_BYTES
        or measured_range[1] > MAX_CATALOG_PLAINTEXT_BYTES
    ):
        raise MaximumLiveSizeGateError(f"{name} exceeds 16 MiB")
    fingerprint = _report_string(record["fixtureManifestSha256"], f"{name}.fixtureManifestSha256")
    if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise ReportValidationError(f"{name}.fixtureManifestSha256 must be lowercase SHA-256")
    if fingerprint != EXPECTED_FIXTURE_MANIFEST_FINGERPRINTS.get(name):
        raise ReportValidationError(
            f"{name}.fixtureManifestSha256 is not the source-controlled manifest"
        )
    if (
        _report_int(
            record["productionSerializationsPerCommit"],
            f"{name}.productionSerializationsPerCommit",
            minimum=1,
        )
        != 1
    ):
        raise ReportValidationError(f"{name}.productionSerializationsPerCommit must be exactly one")
    bytes_written = _report_int(record["bytesWritten"], f"{name}.bytesWritten", minimum=1)
    total_bytes = _report_int(record["totalBytesWritten"], f"{name}.totalBytesWritten", minimum=1)
    if not serialized_size * samples <= total_bytes <= bytes_written * samples:
        raise ReportValidationError(f"{name}.totalBytesWritten is contradictory")
    commit = _duration_percentiles(record["commitMs"], f"{name}.commitMs")
    lock = _duration_percentiles(record["lockHoldMs"], f"{name}.lockHoldMs")
    if any(
        lock_value > commit_value for lock_value, commit_value in zip(lock, commit, strict=True)
    ):
        raise ReportValidationError(f"{name}.lockHoldMs cannot exceed commitMs")
    if record["publicationPath"] != PUBLICATION_PATH:
        raise ReportValidationError(f"{name}.publicationPath is not the production path")
    return record


def _validate_profile(value: Any) -> dict[str, Any]:
    profile = _report_object(
        value,
        {
            "mode",
            "target",
            "architecture",
            "hostEvidence",
            "storageEvidence",
            "durabilityEvidence",
            "durableWrite4KiBP95Ms",
            "durableWrite4KiB",
            "excludedStorageClasses",
        },
        "profile",
    )
    if profile["mode"] != "reference":
        raise ReportValidationError("profile.mode must be reference")
    target = _report_string(profile["target"], "profile.target")
    windows_target = _windows_path(target)
    if not windows_target.is_absolute():
        raise ReportValidationError("profile.target must be absolute")
    if _report_string(profile["architecture"], "profile.architecture").casefold() not in {
        "64-bit",
        "x64",
        "amd64",
    }:
        raise ReportValidationError("profile.architecture must be x64")
    host = _report_object(
        profile["hostEvidence"],
        {
            "source",
            "osCaption",
            "osVersion",
            "cpu",
            "cpuArchitectureCodes",
            "physicalCores",
            "logicalProcessors",
            "ramBytes",
            "ramGiB",
        },
        "profile.hostEvidence",
    )
    if host["source"] != "live-cim":
        raise ReportValidationError("host evidence must come from live CIM")
    if "windows 11" not in _report_string(host["osCaption"], "host.osCaption").casefold():
        raise ReportValidationError("host evidence must report Windows 11")
    host_version = _report_string(host["osVersion"], "host.osVersion")
    try:
        parsed_version = tuple(int(part) for part in host_version.split("."))
    except ValueError as error:
        raise ReportValidationError("host OS version evidence is invalid") from error
    if len(parsed_version) < 3 or parsed_version[0:2] != (10, 0) or parsed_version[2] < 22_000:
        raise ReportValidationError("host OS version is not Windows 11")
    _report_string(host["cpu"], "host.cpu")
    architecture_codes = host["cpuArchitectureCodes"]
    if (
        not isinstance(architecture_codes, list)
        or not architecture_codes
        or any(type(code) is not int or code != 9 for code in architecture_codes)
    ):
        raise ReportValidationError("host CPU architecture evidence must be x64")
    physical_cores = _report_int(host["physicalCores"], "host.physicalCores")
    logical_processors = _report_int(host["logicalProcessors"], "host.logicalProcessors")
    ram_bytes = _report_int(host["ramBytes"], "host.ramBytes")
    ram_gib = _report_number(host["ramGiB"], "host.ramGiB")
    if physical_cores < 4 or logical_processors < physical_cores:
        raise ReportValidationError("host evidence does not meet the CPU floor")
    if ram_bytes < 16 * 1024**3 or not math.isclose(
        ram_gib, round(ram_bytes / 1024**3, 2), abs_tol=0.005
    ):
        raise ReportValidationError("host RAM evidence is contradictory")
    storage = _report_object(
        profile["storageEvidence"],
        {
            "source",
            "volumeRoot",
            "driveType",
            "filesystem",
            "partitionDiskNumber",
            "diskNumber",
            "physicalDeviceId",
            "model",
            "serialNumber",
            "busType",
            "mediaType",
            "healthStatus",
            "operationalStatus",
            "physicalLocation",
            "mappingVerified",
            "internal",
        },
        "profile.storageEvidence",
    )
    if storage["source"] != "live-cim-volume-disk-physical-disk-mapping":
        raise ReportValidationError("storage evidence source is invalid")
    disk_number = _report_int(storage["diskNumber"], "storage.diskNumber")
    if (
        _report_int(storage["partitionDiskNumber"], "storage.partitionDiskNumber") != disk_number
        or _report_string(storage["physicalDeviceId"], "storage.physicalDeviceId")
        != str(disk_number)
        or not _report_bool(storage["mappingVerified"], "storage.mappingVerified")
    ):
        raise ReportValidationError("storage mapping evidence is contradictory")
    expected_storage = {
        "driveType": "fixed",
        "filesystem": "ntfs",
        "busType": "nvme",
        "mediaType": "ssd",
        "healthStatus": "healthy",
    }
    for key, expected in expected_storage.items():
        if _report_string(storage[key], f"storage.{key}").casefold() != expected:
            raise ReportValidationError(f"storage.{key} is not reference-grade")
    if _report_string(storage["operationalStatus"], "storage.operationalStatus").casefold() not in {
        "ok",
        "online",
    }:
        raise ReportValidationError("storage.operationalStatus is not reference-grade")
    for key in ("volumeRoot", "model", "serialNumber", "physicalLocation"):
        _report_string(storage[key], f"storage.{key}")
    if _windows_path(storage["volumeRoot"]).drive.casefold() != windows_target.drive.casefold():
        raise ReportValidationError("storage volume contradicts the benchmark target")
    if not _report_bool(storage["internal"], "storage.internal"):
        raise ReportValidationError("storage must be internal")
    durability = _report_object(
        profile["durabilityEvidence"],
        {
            "source",
            "hardwarePowerProtection",
            "writeCacheType",
            "writeCacheEnabled",
            "writeThroughSupported",
            "flushCacheSupported",
            "userDefinedPowerProtection",
            "nvCacheEnabled",
            "softwareFlushMethod",
            "publicationUsesWriteThroughAndDirectorySync",
            "acceptableProperty",
        },
        "profile.durabilityEvidence",
    )
    if durability["source"] != "live-storage-write-cache-property-and-publication-path":
        raise ReportValidationError("durability evidence source is invalid")
    if durability["hardwarePowerProtection"] not in {
        "reported-true",
        "reported-false",
        "unknown-not-reported",
    }:
        raise ReportValidationError("hardware power protection evidence is invalid")
    _report_string(durability["writeCacheType"], "durability.writeCacheType")
    if durability["writeCacheEnabled"] is not None:
        _report_bool(durability["writeCacheEnabled"], "durability.writeCacheEnabled")
    for key in (
        "writeThroughSupported",
        "flushCacheSupported",
        "userDefinedPowerProtection",
        "nvCacheEnabled",
        "publicationUsesWriteThroughAndDirectorySync",
    ):
        _report_bool(durability[key], f"durability.{key}")
    if not (
        durability["writeThroughSupported"]
        and durability["flushCacheSupported"]
        and durability["publicationUsesWriteThroughAndDirectorySync"]
        and durability["softwareFlushMethod"] == "FlushFileBuffers"
        and durability["acceptableProperty"] == "flush-and-write-through-supported"
    ):
        raise ReportValidationError("durability evidence is insufficient")
    durable = _report_object(
        profile["durableWrite4KiB"],
        {"warmupCount", "sampleCount", "p50Ms", "p95Ms", "p99Ms", "flushMethod"},
        "profile.durableWrite4KiB",
    )
    durable_warmups = _report_int(durable["warmupCount"], "durableWrite4KiB.warmupCount")
    durable_samples = _report_int(durable["sampleCount"], "durableWrite4KiB.sampleCount")
    if (
        durable_warmups != REFERENCE_DURABLE_WRITE_WARMUPS
        or durable_samples != REFERENCE_DURABLE_WRITE_SAMPLES
        or durable["flushMethod"] != "FlushFileBuffers"
    ):
        raise ReportValidationError("durable 4-KiB probe configuration is invalid")
    durable_percentiles = tuple(
        _report_number(durable[key], f"durableWrite4KiB.{key}")
        for key in ("p50Ms", "p95Ms", "p99Ms")
    )
    if durable_percentiles != tuple(sorted(durable_percentiles)):
        raise ReportValidationError("durable 4-KiB percentiles must be monotonic")
    recorded_p95 = _report_number(profile["durableWrite4KiBP95Ms"], "profile.durableWrite4KiBP95Ms")
    if recorded_p95 != durable_percentiles[1]:
        raise ReportValidationError("durable 4-KiB p95 fields contradict each other")
    if profile["excludedStorageClasses"] != EXCLUDED_STORAGE_CLASSES:
        raise ReportValidationError("excluded storage classes are incomplete")
    return profile


def calculate_gates(report: dict[str, Any]) -> dict[str, bool]:
    fixtures = {fixture["name"]: fixture for fixture in report["fixtures"]}
    medium = fixtures["medium"]
    maximum = fixtures["maximum-live"]
    serialized_limit = fixtures.get("serialized-limit")
    gates = {
        "durableWrite4KiBP95Le5Ms": report["profile"]["durableWrite4KiBP95Ms"] <= 5.0,
        "mediumP95Le100Ms": medium["commitMs"]["p95"] <= 100.0,
        "maximumLiveSerializedSizeLe16MiB": maximum["serializedSizeBytes"]
        <= MAX_CATALOG_PLAINTEXT_BYTES,
        "maximumLiveP95Le250Ms": maximum["commitMs"]["p95"] <= 250.0,
        "serializedLimitP95Le250Ms": serialized_limit is None
        or serialized_limit["commitMs"]["p95"] <= 250.0,
    }
    gates["allPassed"] = all(gates.values())
    return gates


def benchmark_build_record(build: BenchmarkBuildEvidence, source_commit: str) -> dict[str, Any]:
    return {
        "sourceCommit": source_commit,
        "command": list(build.command),
        "targetDirectory": str(build.target_directory),
        "cargoLockSha256": build.cargo_lock_sha256,
        "rustc": build.rustc,
        "sanitizedEnvironmentRemoved": list(build.sanitized_environment_removed),
        "forcedEnvironment": {"CARGO_INCREMENTAL": "0"},
        "preservedForAudit": True,
    }


def validate_and_finalize_report(
    report: dict[str, Any],
    *,
    expected_source_commit: str,
    expected_binary_path: Path,
    expected_binary_sha256: str,
    expected_binary_volume_serial: int,
    expected_binary_file_id: int,
    expected_reference_target: Path,
    expected_benchmark_build: Mapping[str, Any],
) -> dict[str, Any]:
    record = _report_object(
        report,
        {
            "schemaVersion",
            "generatedAt",
            "sourceCommit",
            "benchmarkBuild",
            "benchmarkBinary",
            "benchmarkCommand",
            "warmupCommits",
            "measuredCommits",
            "fixtures",
            "profile",
            "versions",
            "gates",
        },
        "report",
    )
    if type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1:
        raise ReportValidationError("schemaVersion must be 1")
    generated_at = _report_string(record["generatedAt"], "generatedAt")
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReportValidationError("generatedAt must be an ISO-8601 timestamp") from error
    if not generated_at.endswith("Z") or timestamp.tzinfo is None:
        raise ReportValidationError("generatedAt must be an absolute UTC timestamp")
    source_commit = _report_string(record["sourceCommit"], "sourceCommit")
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ReportValidationError("sourceCommit must be a lowercase Git commit hash")
    if source_commit != expected_source_commit:
        raise ReportValidationError("sourceCommit does not match the benchmark source")
    build = _report_object(
        record["benchmarkBuild"],
        {
            "sourceCommit",
            "command",
            "targetDirectory",
            "cargoLockSha256",
            "rustc",
            "sanitizedEnvironmentRemoved",
            "forcedEnvironment",
            "preservedForAudit",
        },
        "benchmarkBuild",
    )
    _report_string(build["sourceCommit"], "benchmarkBuild.sourceCommit")
    _report_string(build["targetDirectory"], "benchmarkBuild.targetDirectory")
    cargo_lock_sha256 = _report_string(build["cargoLockSha256"], "benchmarkBuild.cargoLockSha256")
    if re.fullmatch(r"[0-9a-f]{64}", cargo_lock_sha256) is None:
        raise ReportValidationError("benchmarkBuild.cargoLockSha256 must be lowercase SHA-256")
    _report_string(build["rustc"], "benchmarkBuild.rustc")
    if not isinstance(build["command"], list) or not all(
        isinstance(value, str) and value for value in build["command"]
    ):
        raise ReportValidationError("benchmarkBuild.command must be an argv string array")
    if not isinstance(build["sanitizedEnvironmentRemoved"], list) or not all(
        isinstance(value, str) and value for value in build["sanitizedEnvironmentRemoved"]
    ):
        raise ReportValidationError(
            "benchmarkBuild.sanitizedEnvironmentRemoved must be a string array"
        )
    forced = _report_object(
        build["forcedEnvironment"], {"CARGO_INCREMENTAL"}, "benchmarkBuild.forcedEnvironment"
    )
    if forced != {"CARGO_INCREMENTAL": "0"}:
        raise ReportValidationError("benchmarkBuild.forcedEnvironment is invalid")
    _report_bool(build["preservedForAudit"], "benchmarkBuild.preservedForAudit")
    if build != dict(expected_benchmark_build):
        raise ReportValidationError("benchmarkBuild does not match the executed private build")
    binary = _report_object(
        record["benchmarkBinary"],
        {"path", "sha256", "volumeSerial", "fileId"},
        "benchmarkBinary",
    )
    binary_path = _report_string(binary["path"], "benchmarkBinary.path")
    binary_sha256 = _report_string(binary["sha256"], "benchmarkBinary.sha256")
    if not _same_windows_path(binary_path, expected_binary_path):
        raise ReportValidationError("benchmarkBinary.path does not match the executed binary")
    if (
        re.fullmatch(r"[0-9a-f]{64}", binary_sha256) is None
        or binary_sha256 != expected_binary_sha256
    ):
        raise ReportValidationError("benchmarkBinary.sha256 does not match the executed binary")
    if (
        _report_int(binary["volumeSerial"], "benchmarkBinary.volumeSerial", minimum=0)
        != expected_binary_volume_serial
        or _report_int(binary["fileId"], "benchmarkBinary.fileId", minimum=0)
        != expected_binary_file_id
    ):
        raise ReportValidationError("benchmarkBinary identity does not match the executed binary")
    warmups = _report_int(record["warmupCommits"], "warmupCommits")
    samples = _report_int(record["measuredCommits"], "measuredCommits")
    if (
        warmups != REFERENCE_WARMUPS
        or samples < REFERENCE_SAMPLES
        or samples > MAX_REFERENCE_SAMPLES
    ):
        raise ReportValidationError(
            "reference report requires exactly 30 warmups and 200 to 967 samples"
        )
    profile = _validate_profile(record["profile"])
    profile_target = _report_string(profile["target"], "profile.target")
    if not _same_windows_path(profile_target, expected_reference_target):
        raise ReportValidationError(
            "profile.target is not the independently derived reference target"
        )
    command = record["benchmarkCommand"]
    expected_command = [
        str(expected_binary_path),
        "--target",
        str(expected_reference_target),
        "--warmups",
        str(warmups),
        "--samples",
        str(samples),
    ]
    if command != expected_command:
        raise ReportValidationError(
            "benchmarkCommand must be the exact executed binary argv and run parameters"
        )
    fixtures_value = record["fixtures"]
    if not isinstance(fixtures_value, list):
        raise ReportValidationError("fixtures must be a list")
    names = [
        fixture.get("name") if isinstance(fixture, dict) else None for fixture in fixtures_value
    ]
    if len(names) != len(set(names)):
        raise ReportValidationError("fixture names must be unique")
    if "medium" not in names or "maximum-live" not in names:
        raise ReportValidationError("medium and maximum-live fixtures are required")
    raw_by_name = dict(zip(names, fixtures_value, strict=True))
    maximum_size = _report_int(
        raw_by_name["maximum-live"].get("serializedSizeBytes"),
        "maximum-live.serializedSizeBytes",
        minimum=1,
    )
    require_supported_maximum_live_size(maximum_size)
    expected_names = {"medium", "maximum-live"}
    if maximum_size < MAX_CATALOG_PLAINTEXT_BYTES:
        expected_names.add("serialized-limit")
    if set(names) != expected_names:
        raise ReportValidationError(
            f"fixture set is invalid for maximum-live size: {sorted(names)}"
        )
    expected_counts = {
        "medium": (5_000, 500, 5_500),
        "maximum-live": (25_000, 2_500, 27_500),
        "serialized-limit": (25_000, 0, 25_000),
    }
    fixtures: dict[str, dict[str, Any]] = {}
    for name in names:
        fixtures[name] = _validate_fixture(
            raw_by_name[name],
            expected_counts=expected_counts[name],
            warmups=warmups,
            samples=samples,
        )
    serialized_limit = fixtures.get("serialized-limit")
    if serialized_limit is not None:
        size = serialized_limit["serializedSizeBytes"]
        warmup_range = serialized_limit["warmupSerializedSizeBytes"]
        measured_range = serialized_limit["measuredSerializedSizeBytes"]
        if not (
            size == MAX_CATALOG_PLAINTEXT_BYTES
            and warmup_range == {"min": size, "max": size}
            and measured_range == {"min": size, "max": size}
        ):
            raise ReportValidationError(
                "serialized-limit fixture must be exactly 16 MiB for every generation"
            )
    versions = _report_object(
        record["versions"],
        {"animaCorefs", "rust", "aesGcm", "argon2", "serdeJson"},
        "versions",
    )
    for key, value in versions.items():
        _report_string(value, f"versions.{key}")
    gates = _report_object(
        record["gates"],
        {
            "durableWrite4KiBP95Le5Ms",
            "mediumP95Le100Ms",
            "maximumLiveSerializedSizeLe16MiB",
            "maximumLiveP95Le250Ms",
            "serializedLimitP95Le250Ms",
            "allPassed",
        },
        "gates",
    )
    for key, value in gates.items():
        _report_bool(value, f"gates.{key}")
    if gates != calculate_gates(record):
        raise ReportValidationError("gates contradict the recorded measurements")
    return record


def _locked_version(package_name: str) -> str:
    lock = tomllib.loads((REPO_ROOT / "Cargo.lock").read_text(encoding="utf-8"))
    matches = [
        package["version"]
        for package in lock.get("package", [])
        if package.get("name") == package_name
    ]
    if len(matches) != 1:
        raise ReportValidationError(f"expected one locked {package_name} package")
    return str(matches[0])


def collect_versions() -> dict[str, str]:
    manifest = tomllib.loads(
        (REPO_ROOT / "packages" / "anima-corefs" / "Cargo.toml").read_text(encoding="utf-8")
    )
    rust = subprocess.run(
        ["rustc", "+1.75.0", "--version"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "animaCorefs": str(manifest["package"]["version"]),
        "rust": rust,
        "aesGcm": _locked_version("aes-gcm"),
        "argon2": _locked_version("argon2"),
        "serdeJson": _locked_version("serde_json"),
    }


def source_commit_for_benchmark() -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ReportValidationError("Git did not return a canonical source commit")
    dirty = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise ReportValidationError(
            "reference benchmark source must be committed and the worktree must be clean"
        )
    return commit


def _sha256_regular_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_committed_cargo_lock(source_commit: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ReportValidationError("benchmark source commit is invalid")
    completed = subprocess.run(
        ["git", "cat-file", "blob", f"{source_commit}:Cargo.lock"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    if not isinstance(completed.stdout, bytes):
        raise ReportValidationError("committed Cargo.lock probe did not return bytes")
    return hashlib.sha256(completed.stdout).hexdigest()


def _sanitized_cargo_environment() -> tuple[dict[str, str], tuple[str, ...]]:
    environment = os.environ.copy()
    exact = {
        "CARGO_BUILD_TARGET",
        "CARGO_ENCODED_RUSTFLAGS",
        "CARGO_TARGET_DIR",
        "RUSTC",
        "RUSTC_WRAPPER",
        "RUSTC_WORKSPACE_WRAPPER",
        "RUSTDOCFLAGS",
        "RUSTFLAGS",
        "RUSTUP_TOOLCHAIN",
    }
    removed = sorted(
        key
        for key in environment
        if key in exact or key.startswith("CARGO_PROFILE_") or key.startswith("CARGO_TARGET_")
    )
    for key in removed:
        environment.pop(key, None)
    environment["CARGO_INCREMENTAL"] = "0"
    return environment, tuple(removed)


def build_rust_benchmark_binary(source_commit: str) -> BenchmarkBuildEvidence:
    target_directory = Path(tempfile.mkdtemp(prefix="anima-corefs-catalog-build-")).resolve()
    environment, removed = _sanitized_cargo_environment()
    command = (
        "cargo",
        "+1.75.0",
        "build",
        "--release",
        "--locked",
        "-p",
        "anima-corefs",
        "--bin",
        "catalog_benchmark",
        "--target-dir",
        str(target_directory),
    )
    subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        check=True,
        env=environment,
    )
    rustc = subprocess.run(
        ["rustc", "+1.75.0", "--version", "--verbose"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    suffix = ".exe" if os.name == "nt" else ""
    binary = (target_directory / "release" / f"catalog_benchmark{suffix}").resolve(strict=False)
    if not binary.is_file():
        raise ReportValidationError(f"built benchmark binary is missing: {binary}")
    return BenchmarkBuildEvidence(
        binary=binary,
        command=command,
        target_directory=target_directory,
        cargo_lock_sha256=_sha256_committed_cargo_lock(source_commit),
        rustc=rustc,
        sanitized_environment_removed=removed,
    )


def _open_locked_binary_handle(path: Path) -> tuple[Any, Any]:
    """Open the exact Windows binary for read while denying writes and replacement."""

    if os.name != "nt":
        raise ReportValidationError("reference benchmark binary locking requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ only: deny writes, delete, and replacement
        None,
        3,  # OPEN_EXISTING
        0x00200000 | 0x08000000,  # OPEN_REPARSE_POINT | SEQUENTIAL_SCAN
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ReportValidationError(
            f"cannot hold benchmark binary against mutation: {ctypes.get_last_error()}"
        )
    return kernel32, handle


def _open_handle_evidence(kernel32: Any, handle: Any) -> ReferencePathEvidence:
    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    information = ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        raise ReportValidationError(
            f"cannot read held benchmark binary identity: {ctypes.get_last_error()}"
        )
    capacity = 32768
    buffer = ctypes.create_unicode_buffer(capacity)
    length = int(kernel32.GetFinalPathNameByHandleW(handle, buffer, capacity, 0))
    if length == 0 or length >= capacity:
        raise ReportValidationError(
            f"cannot canonicalize held benchmark binary: {ctypes.get_last_error()}"
        )
    canonical = buffer.value
    if canonical.startswith("\\\\?\\UNC\\"):
        canonical = "\\\\" + canonical[8:]
    elif canonical.startswith("\\\\?\\"):
        canonical = canonical[4:]
    file_id = (int(information.file_index_high) << 32) | int(information.file_index_low)
    return ReferencePathEvidence(
        canonical_path=Path(canonical),
        volume_serial=int(information.volume_serial_number),
        file_id=file_id,
        attributes=int(information.attributes),
    )


def _sha256_open_windows_handle(kernel32: Any, handle: Any) -> str:
    kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    kernel32.SetFilePointerEx.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    if not kernel32.SetFilePointerEx(handle, 0, None, 0):
        raise ReportValidationError("cannot seek held benchmark binary")
    digest = hashlib.sha256()
    buffer = ctypes.create_string_buffer(1024 * 1024)
    while True:
        count = wintypes.DWORD()
        if not kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
            raise ReportValidationError(
                f"cannot hash held benchmark binary: {ctypes.get_last_error()}"
            )
        if count.value == 0:
            break
        digest.update(buffer.raw[: count.value])
    if not kernel32.SetFilePointerEx(handle, 0, None, 0):
        raise ReportValidationError("cannot rewind held benchmark binary")
    return digest.hexdigest()


def verify_benchmark_binary_after_run(
    path: Path,
    initial_evidence: ReferencePathEvidence,
    initial_sha256: str,
    *,
    path_probe: Callable[[Path], ReferencePathEvidence] = probe_reference_path,
    hash_probe: Callable[[], str],
) -> None:
    try:
        current_evidence = path_probe(path)
    except OSError as error:
        raise ReportValidationError(
            f"cannot revalidate benchmark binary identity: {error}"
        ) from error
    if not _same_identity(initial_evidence, current_evidence):
        raise ReportValidationError("benchmark binary identity changed during execution")
    if current_evidence.attributes & CLOUD_OR_REPARSE_ATTRIBUTES:
        raise ReportValidationError("benchmark binary became a reparse or cloud-backed path")
    if hash_probe() != initial_sha256:
        raise ReportValidationError("benchmark binary content changed during execution")


@contextmanager
def hold_benchmark_binary(path: Path):
    kernel32, handle = _open_locked_binary_handle(path)
    try:
        evidence = _open_handle_evidence(kernel32, handle)
        if not _same_resolved_path(evidence.canonical_path, path):
            raise ReportValidationError("held benchmark binary canonical path changed")
        if evidence.attributes & CLOUD_OR_REPARSE_ATTRIBUTES:
            raise ReportValidationError("benchmark binary must be a regular local file")
        digest = _sha256_open_windows_handle(kernel32, handle)
        yield HeldBenchmarkBinary(
            evidence=evidence,
            sha256=digest,
            hash_probe=lambda: _sha256_open_windows_handle(kernel32, handle),
        )
    finally:
        kernel32.CloseHandle(handle)


def verify_reference_target_chain(
    held: HeldReferenceTargetChain,
    *,
    path_probe: Callable[[Path], ReferencePathEvidence] = probe_reference_path,
) -> None:
    for path, expected in zip(held.paths, held.evidence, strict=True):
        try:
            current = path_probe(path)
        except OSError as error:
            raise ReferenceTargetError(
                f"cannot revalidate held reference path {path}: {error}"
            ) from error
        if not _same_identity(expected, current):
            raise ReferenceTargetError(
                f"reference target path identity changed during execution: {path}"
            )
        if current.attributes & CLOUD_OR_REPARSE_ATTRIBUTES:
            raise ReferenceTargetError(
                f"reference target path became reparse, offline, or cloud-backed: {path}"
            )


@contextmanager
def hold_reference_target_chain(local_app_data_root: Path, target: Path):
    """Pin the LocalAppData-to-target directory chain against rename/replacement."""

    local = local_app_data_root.resolve(strict=True)
    resolved_target = target.resolve(strict=True)
    try:
        relative_parts = resolved_target.relative_to(local).parts
    except ValueError as error:
        raise ReferenceTargetError("reference target is outside LocalAppData") from error
    paths = [local]
    candidate = local
    for part in relative_parts:
        candidate /= part
        paths.append(candidate)

    if os.name != "nt":
        raise ReferenceTargetError("reference target handle pinning requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handles: list[Any] = []
    evidence: list[ReferencePathEvidence] = []
    try:
        for path in paths:
            handle = kernel32.CreateFileW(
                str(path),
                0x80000000,  # GENERIC_READ
                0x00000001 | 0x00000002,  # share read/write, but deny delete/rename
                None,
                3,
                0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
                None,
            )
            if handle == ctypes.c_void_p(-1).value:
                raise ReferenceTargetError(
                    f"cannot pin reference target path {path}: {ctypes.get_last_error()}"
                )
            handles.append(handle)
            current = _open_handle_evidence(kernel32, handle)
            if not _same_resolved_path(current.canonical_path, path):
                raise ReferenceTargetError(f"held reference path canonicalization changed: {path}")
            if current.attributes & CLOUD_OR_REPARSE_ATTRIBUTES:
                raise ReferenceTargetError(
                    f"held reference path is reparse, offline, or cloud-backed: {path}"
                )
            evidence.append(current)
        held = HeldReferenceTargetChain(tuple(paths), tuple(evidence))
        verify_reference_target_chain(held)
        yield held
        verify_reference_target_chain(held)
    finally:
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)


def run_rust_benchmark(
    binary: Path, target: Path, *, warmups: int, samples: int
) -> tuple[dict[str, Any], list[str]]:
    command = [
        str(binary),
        "--target",
        str(target),
        "--warmups",
        str(warmups),
        "--samples",
        str(samples),
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    decoded = loads_strict_json(completed.stdout)
    if not isinstance(decoded, dict):
        raise ReportValidationError("Rust benchmark did not emit one JSON object")
    return decoded, command


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", action="store_true", required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--warmups", type=int, default=REFERENCE_WARMUPS)
    parser.add_argument("--samples", type=int, default=REFERENCE_SAMPLES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    target = args.target.expanduser().resolve(strict=False)
    artifact = args.artifact.expanduser().resolve(strict=False)
    validate_reference_run_counts(args.warmups, args.samples)
    source_commit = source_commit_for_benchmark()
    local_app_data = probe_local_app_data().expanduser().resolve(strict=True)
    target = prepare_reference_target(
        target,
        artifact,
        local_app_data_probe=lambda: local_app_data,
    )
    with hold_reference_target_chain(local_app_data, target) as held_target:
        facts = validate_reference_profile(target, probe_live_reference_host(target))
        durable_write = measure_durable_write_4k(target)
        if float(durable_write["p95Ms"]) > 5.0:
            raise ReferenceTargetError(
                f"4-KiB durable-write p95 exceeds 5 ms: {durable_write['p95Ms']:.3f} ms"
            )
        build = build_rust_benchmark_binary(source_commit)
        binary = build.binary
        with hold_benchmark_binary(binary) as held_binary:
            if source_commit_for_benchmark() != source_commit:
                raise ReportValidationError("benchmark source commit changed during the build")
            verify_reference_target_chain(held_target)
            rust_report, command = run_rust_benchmark(
                binary, target, warmups=args.warmups, samples=args.samples
            )
            verify_benchmark_binary_after_run(
                binary,
                held_binary.evidence,
                held_binary.sha256,
                hash_probe=held_binary.hash_probe,
            )
            verify_reference_target_chain(held_target)
            if source_commit_for_benchmark() != source_commit:
                raise ReportValidationError("benchmark source changed during execution")
            build_record = benchmark_build_record(build, source_commit)
            report = {
                **rust_report,
                "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "sourceCommit": source_commit,
                "benchmarkBuild": build_record,
                "benchmarkBinary": {
                    "path": str(binary),
                    "sha256": held_binary.sha256,
                    "volumeSerial": held_binary.evidence.volume_serial,
                    "fileId": held_binary.evidence.file_id,
                },
                "benchmarkCommand": command,
                "profile": {
                    "mode": "reference",
                    "target": str(target),
                    "architecture": "x64",
                    "hostEvidence": {
                        "source": "live-cim",
                        "osCaption": facts.os_caption,
                        "osVersion": facts.os_version,
                        "cpu": facts.cpu_name,
                        "cpuArchitectureCodes": list(facts.cpu_architecture_codes),
                        "physicalCores": facts.physical_cores,
                        "logicalProcessors": facts.logical_processors,
                        "ramBytes": facts.ram_bytes,
                        "ramGiB": round(facts.ram_bytes / 1024**3, 2),
                    },
                    "storageEvidence": {
                        "source": "live-cim-volume-disk-physical-disk-mapping",
                        "volumeRoot": str(facts.volume.volume_root),
                        "driveType": facts.volume.drive_type,
                        "filesystem": facts.volume.filesystem,
                        "partitionDiskNumber": facts.partition_disk_number,
                        "diskNumber": facts.disk_number,
                        "physicalDeviceId": facts.physical_device_id,
                        "model": facts.physical_model,
                        "serialNumber": facts.physical_serial,
                        "busType": facts.physical_bus_type,
                        "mediaType": facts.physical_media_type,
                        "healthStatus": facts.physical_health_status,
                        "operationalStatus": facts.physical_operational_status,
                        "physicalLocation": facts.physical_location,
                        "mappingVerified": True,
                        "internal": True,
                    },
                    "durabilityEvidence": {
                        "source": "live-storage-write-cache-property-and-publication-path",
                        "hardwarePowerProtection": facts.hardware_power_protection,
                        "writeCacheType": facts.write_cache.write_cache_type,
                        "writeCacheEnabled": facts.write_cache.write_cache_enabled,
                        "writeThroughSupported": facts.write_cache.write_through_supported,
                        "flushCacheSupported": facts.write_cache.flush_cache_supported,
                        "userDefinedPowerProtection": (
                            facts.write_cache.user_defined_power_protection
                        ),
                        "nvCacheEnabled": facts.write_cache.nv_cache_enabled,
                        "softwareFlushMethod": "FlushFileBuffers",
                        "publicationUsesWriteThroughAndDirectorySync": True,
                        "acceptableProperty": "flush-and-write-through-supported",
                    },
                    "durableWrite4KiBP95Ms": durable_write["p95Ms"],
                    "durableWrite4KiB": durable_write,
                    "excludedStorageClasses": EXCLUDED_STORAGE_CLASSES,
                },
                "versions": collect_versions(),
            }
            report["gates"] = calculate_gates(report)
            if source_commit_for_benchmark() != source_commit:
                raise ReportValidationError("benchmark source changed before artifact publication")
            verify_reference_target_chain(held_target)
            validate_and_finalize_report(
                report,
                expected_source_commit=source_commit,
                expected_binary_path=binary,
                expected_binary_sha256=held_binary.sha256,
                expected_binary_volume_serial=held_binary.evidence.volume_serial,
                expected_binary_file_id=held_binary.evidence.file_id,
                expected_reference_target=target,
                expected_benchmark_build=build_record,
            )
            write_json_atomic(artifact, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["gates"]["allPassed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReferenceTargetError, ReportValidationError, subprocess.CalledProcessError) as error:
        print(f"catalog benchmark failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
