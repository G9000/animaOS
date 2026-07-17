"""Run and record the CoreFS V1 full-catalog durable publication benchmark."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Sequence
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


class ReferenceTargetFacts(NamedTuple):
    drive_type: str
    filesystem: str
    synchronized_roots: tuple[Path, ...]


def _is_within(path: Path, root: Path) -> bool:
    path_value = os.path.normcase(str(path.resolve(strict=False)))
    root_value = os.path.normcase(str(root.resolve(strict=False)))
    try:
        return os.path.commonpath([path_value, root_value]) == root_value
    except ValueError:
        return False


def validate_reference_target(target: Path, facts: ReferenceTargetFacts) -> ReferenceTargetFacts:
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


def probe_reference_target(target: Path) -> ReferenceTargetFacts:
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
    return ReferenceTargetFacts(
        drive_type=drive_types.get(drive_type_code, "unknown"),
        filesystem=filesystem_buffer.value,
        synchronized_roots=tuple(sync_roots),
    )


def percentile_nearest_rank(samples: Sequence[float], percentile: float) -> float:
    if not samples:
        raise ValueError("percentile requires at least one sample")
    if not 0.0 <= percentile <= 1.0:
        raise ValueError("percentile must be between zero and one")
    ordered = sorted(samples)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def measure_durable_write_4k(
    target: Path,
    *,
    warmups: int = REFERENCE_DURABLE_WRITE_WARMUPS,
    samples: int = REFERENCE_DURABLE_WRITE_SAMPLES,
) -> dict[str, float | int]:
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
                os.fsync(descriptor)
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
    }


def require_supported_maximum_live_size(serialized_size: int) -> None:
    if serialized_size > MAX_CATALOG_PLAINTEXT_BYTES:
        raise MaximumLiveSizeGateError(
            f"maximum-live fixture exceeds 16 MiB: {serialized_size} bytes"
        )


def _fixture_by_name(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    fixtures = report.get("fixtures")
    if not isinstance(fixtures, list):
        raise ReportValidationError("fixtures must be a list")
    return next(
        (
            fixture
            for fixture in fixtures
            if isinstance(fixture, dict) and fixture.get("name") == name
        ),
        None,
    )


def _required_number(value: dict[str, Any], key: str, context: str) -> float:
    result = value.get(key)
    if not isinstance(result, (int, float)) or isinstance(result, bool):
        raise ReportValidationError(f"{context}.{key} must be numeric")
    return float(result)


def validate_and_finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schemaVersion") != 1:
        raise ReportValidationError("schemaVersion must be 1")
    if int(report.get("warmupCommits", -1)) < REFERENCE_WARMUPS:
        raise ReportValidationError("reference report requires at least 30 warm-up commits")
    if int(report.get("measuredCommits", -1)) < REFERENCE_SAMPLES:
        raise ReportValidationError("reference report requires at least 200 measured commits")

    medium = _fixture_by_name(report, "medium")
    maximum = _fixture_by_name(report, "maximum-live")
    if medium is None or maximum is None:
        raise ReportValidationError("medium and maximum-live fixtures are required")
    expected_counts = {
        "medium": (5_000, 500, 5_500),
        "maximum-live": (25_000, 2_500, 27_500),
    }
    for name, fixture in (("medium", medium), ("maximum-live", maximum)):
        actual = (
            fixture.get("liveCount"),
            fixture.get("tombstoneCount"),
            fixture.get("totalCount"),
        )
        if actual != expected_counts[name]:
            raise ReportValidationError(f"{name} fixture counts are not deterministic")
        for metric in ("commitMs", "lockHoldMs"):
            values = fixture.get(metric)
            if not isinstance(values, dict):
                raise ReportValidationError(f"{name}.{metric} must be an object")
            for percentile in ("p50", "p95", "p99"):
                _required_number(values, percentile, f"{name}.{metric}")
        _required_number(fixture, "serializedSizeBytes", name)
        _required_number(fixture, "bytesWritten", name)

    maximum_size = int(_required_number(maximum, "serializedSizeBytes", "maximum-live"))
    require_supported_maximum_live_size(maximum_size)
    serialized_limit = _fixture_by_name(report, "serialized-limit")
    if maximum_size < MAX_CATALOG_PLAINTEXT_BYTES:
        if serialized_limit is None:
            raise ReportValidationError(
                "serialized-limit fixture is required when maximum-live is below 16 MiB"
            )
        if serialized_limit.get("serializedSizeBytes") != MAX_CATALOG_PLAINTEXT_BYTES:
            raise ReportValidationError("serialized-limit fixture must be exactly 16 MiB")
        if int(serialized_limit.get("liveCount", MAX_CATALOG_PLAINTEXT_BYTES)) > 25_000:
            raise ReportValidationError("serialized-limit fixture exceeds 25,000 live entries")
        serialized_limit_passed = (
            _required_number(
                serialized_limit.get("commitMs", {}),
                "p95",
                "serialized-limit.commitMs",
            )
            <= 250.0
        )
    elif serialized_limit is not None:
        raise ReportValidationError(
            "serialized-limit fixture must be omitted when maximum-live is exactly 16 MiB"
        )
    else:
        serialized_limit_passed = True

    profile = report.get("profile")
    if not isinstance(profile, dict):
        raise ReportValidationError("profile must be an object")
    durable_write_p95 = _required_number(profile, "durableWrite4KiBP95Ms", "profile")
    gates = {
        "durableWrite4KiBP95Le5Ms": durable_write_p95 <= 5.0,
        "mediumP95Le100Ms": _required_number(medium.get("commitMs", {}), "p95", "medium.commitMs")
        <= 100.0,
        "maximumLiveSerializedSizeLe16MiB": maximum_size <= MAX_CATALOG_PLAINTEXT_BYTES,
        "maximumLiveP95Le250Ms": _required_number(
            maximum.get("commitMs", {}), "p95", "maximum-live.commitMs"
        )
        <= 250.0,
        "serializedLimitP95Le250Ms": serialized_limit_passed,
    }
    gates["allPassed"] = all(gates.values())
    report["gates"] = gates
    return report


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
    parser.add_argument("--os-label", required=True)
    parser.add_argument("--cpu", required=True)
    parser.add_argument("--physical-cores", type=int, required=True)
    parser.add_argument("--ram-gib", type=float, required=True)
    parser.add_argument("--storage", required=True)
    parser.add_argument("--warmups", type=int, default=REFERENCE_WARMUPS)
    parser.add_argument("--samples", type=int, default=REFERENCE_SAMPLES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    target = args.target.expanduser().resolve(strict=False)
    facts = validate_reference_target(target, probe_reference_target(target))
    if args.physical_cores < 4:
        raise ReferenceTargetError("reference profile requires at least 4 physical cores")
    if args.ram_gib < 16.0:
        raise ReferenceTargetError("reference profile requires at least 16 GiB RAM")
    if args.warmups < REFERENCE_WARMUPS or args.samples < REFERENCE_SAMPLES:
        raise ReferenceTargetError(
            "reference mode requires at least 30 warmups and 200 measured commits"
        )
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
            "os": args.os_label,
            "architecture": platform.machine(),
            "cpu": args.cpu,
            "physicalCores": args.physical_cores,
            "ramGiB": args.ram_gib,
            "storage": args.storage,
            "filesystem": facts.filesystem,
            "driveType": facts.drive_type,
            "target": str(target),
            "durableWrite4KiBP95Ms": durable_write["p95Ms"],
            "durableWrite4KiB": durable_write,
            "excludedStorageClasses": [
                "OneDrive-synchronized",
                "network",
                "removable",
                "RAM-disk",
                "write-cache-without-durability",
            ],
        },
        "versions": collect_versions(),
    }
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
