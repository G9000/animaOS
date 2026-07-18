"""Run and record the CoreFS V1 full-catalog durable publication benchmark."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Sequence
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

MAX_CATALOG_PLAINTEXT_BYTES = 16 * 1024 * 1024
REFERENCE_WARMUPS = 30
REFERENCE_SAMPLES = 200
REFERENCE_DURABLE_WRITE_SAMPLES = 200
REFERENCE_DURABLE_WRITE_WARMUPS = 30
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT = (
    REPO_ROOT / "docs" / "benchmarks" / "portable-core-filesystem" / "catalog-reference-v1.json"
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
    sync_roots: list[Path] = []
    for variable in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        value = os.environ.get(variable)
        if value:
            sync_roots.append(Path(value))
    return ReferenceVolumeFacts(
        volume_root=Path(volume_root),
        drive_type=drive_types.get(drive_type_code, "unknown"),
        filesystem=filesystem_buffer.value,
        synchronized_roots=tuple(sync_roots),
    )


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
    volume = volume_probe(target)
    drive = volume.volume_root.drive.rstrip(":").upper()
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
        raw = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise ReferenceTargetError("live Windows hardware probe returned invalid JSON") from error
    payload = _strict_object(
        raw,
        {"schemaVersion", "os", "cpu", "partition", "disk", "physicalDisk"},
        "live hardware probe",
    )
    if payload["schemaVersion"] != 1:
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
    try:
        for index in range(warmups + samples):
            probe = probe_dir / f"probe-{index:04}.bin"
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
            if index >= warmups:
                timings_ms.append(elapsed_ms)
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
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
            "fixtureSha256",
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
    if record["warmupCommits"] != warmups or record["sampleCount"] != samples:
        raise ReportValidationError(f"{name} run counts contradict the report")
    expected_generation = 2 + warmups + samples
    if (
        record["finalHeadGeneration"] != expected_generation
        or record["finalCatalogCount"] != expected_generation
    ):
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
    fingerprint = _report_string(record["fixtureSha256"], f"{name}.fixtureSha256")
    if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise ReportValidationError(f"{name}.fixtureSha256 must be lowercase SHA-256")
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
    if not Path(target).is_absolute():
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
        "operationalStatus": "ok",
    }
    for key, expected in expected_storage.items():
        if _report_string(storage[key], f"storage.{key}").casefold() != expected:
            raise ReportValidationError(f"storage.{key} is not reference-grade")
    for key in ("volumeRoot", "model", "serialNumber", "physicalLocation"):
        _report_string(storage[key], f"storage.{key}")
    if os.path.normcase(Path(storage["volumeRoot"]).drive) != os.path.normcase(Path(target).drive):
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
    if (
        durable["warmupCount"] != REFERENCE_DURABLE_WRITE_WARMUPS
        or durable["sampleCount"] != REFERENCE_DURABLE_WRITE_SAMPLES
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


def validate_and_finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    record = _report_object(
        report,
        {
            "schemaVersion",
            "generatedAt",
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
    if record["schemaVersion"] != 1:
        raise ReportValidationError("schemaVersion must be 1")
    generated_at = _report_string(record["generatedAt"], "generatedAt")
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReportValidationError("generatedAt must be an ISO-8601 timestamp") from error
    if not generated_at.endswith("Z") or timestamp.tzinfo is None:
        raise ReportValidationError("generatedAt must be an absolute UTC timestamp")
    command = _report_string(record["benchmarkCommand"], "benchmarkCommand")
    warmups = _report_int(record["warmupCommits"], "warmupCommits")
    samples = _report_int(record["measuredCommits"], "measuredCommits")
    if warmups != REFERENCE_WARMUPS or samples < REFERENCE_SAMPLES:
        raise ReportValidationError(
            "reference report requires exactly 30 warmups and at least 200 samples"
        )
    if f"--warmups {warmups}" not in command or f"--samples {samples}" not in command:
        raise ReportValidationError("benchmarkCommand contradicts the run counts")
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
    _validate_profile(record["profile"])
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


def run_rust_benchmark(
    target: Path, *, warmups: int, samples: int
) -> tuple[dict[str, Any], list[str]]:
    command = [
        "cargo",
        "+1.75.0",
        "run",
        "--release",
        "--locked",
        "-p",
        "anima-corefs",
        "--bin",
        "catalog_benchmark",
        "--",
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
    try:
        return json.loads(completed.stdout), command
    except json.JSONDecodeError as error:
        raise ReportValidationError("Rust benchmark did not emit one JSON report") from error


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
    parser.add_argument("--clean-target", action="store_true", required=True)
    parser.add_argument("--warmups", type=int, default=REFERENCE_WARMUPS)
    parser.add_argument("--samples", type=int, default=REFERENCE_SAMPLES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    target = args.target.expanduser().resolve(strict=False)
    facts = validate_reference_profile(target, probe_live_reference_host(target))
    validate_reference_run_counts(args.warmups, args.samples)
    if target.exists():
        if not args.clean_target:
            raise ReferenceTargetError("existing target requires --clean-target")
        shutil.rmtree(target)
    target.mkdir(parents=True)

    durable_write = measure_durable_write_4k(target)
    if float(durable_write["p95Ms"]) > 5.0:
        raise ReferenceTargetError(
            f"4-KiB durable-write p95 exceeds 5 ms: {durable_write['p95Ms']:.3f} ms"
        )
    rust_report, command = run_rust_benchmark(target, warmups=args.warmups, samples=args.samples)
    report = {
        **rust_report,
        "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "benchmarkCommand": subprocess.list2cmdline(command),
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
                "userDefinedPowerProtection": (facts.write_cache.user_defined_power_protection),
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
    validate_and_finalize_report(report)
    write_json_atomic(args.artifact.resolve(strict=False), report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["gates"]["allPassed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReferenceTargetError, ReportValidationError, subprocess.CalledProcessError) as error:
        print(f"catalog benchmark failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
