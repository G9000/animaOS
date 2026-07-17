from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "apps" / "server" / "scripts" / "benchmark_corefs_catalog.py"


def load_benchmark_module():
    assert SCRIPT_PATH.is_file(), "catalog benchmark feature is missing"
    spec = importlib.util.spec_from_file_location("benchmark_corefs_catalog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("drive_type", ["network", "removable", "ramdisk", "unknown"])
def test_reference_target_fails_closed_for_non_fixed_storage(
    drive_type: str, tmp_path: Path
) -> None:
    benchmark = load_benchmark_module()
    facts = benchmark.ReferenceTargetFacts(
        drive_type=drive_type,
        filesystem="NTFS",
        synchronized_roots=(),
    )

    with pytest.raises(benchmark.ReferenceTargetError, match="fixed local drive"):
        benchmark.validate_reference_target(tmp_path, facts)


def test_reference_target_rejects_onedrive_and_non_ntfs(tmp_path: Path) -> None:
    benchmark = load_benchmark_module()
    onedrive = tmp_path / "OneDrive"
    target = onedrive / "benchmark"
    facts = benchmark.ReferenceTargetFacts(
        drive_type="fixed",
        filesystem="NTFS",
        synchronized_roots=(onedrive,),
    )
    with pytest.raises(benchmark.ReferenceTargetError, match="synchronized"):
        benchmark.validate_reference_target(target, facts)

    facts = benchmark.ReferenceTargetFacts(
        drive_type="fixed",
        filesystem="ReFS",
        synchronized_roots=(),
    )
    with pytest.raises(benchmark.ReferenceTargetError, match="NTFS"):
        benchmark.validate_reference_target(tmp_path, facts)


def test_reference_target_accepts_only_fixed_ntfs_outside_sync_roots(tmp_path: Path) -> None:
    benchmark = load_benchmark_module()
    facts = benchmark.ReferenceTargetFacts(
        drive_type="fixed",
        filesystem="NTFS",
        synchronized_roots=(tmp_path / "sync",),
    )

    assert benchmark.validate_reference_target(tmp_path / "local", facts).filesystem == "NTFS"


def test_report_validation_enforces_conditional_size_fixture_and_schema() -> None:
    benchmark = load_benchmark_module()
    report = {
        "schemaVersion": 1,
        "profile": {
            "mode": "reference",
            "os": "Windows 11 Pro 10.0.26200 x64",
            "cpu": "AMD Ryzen 9 9900X",
            "physicalCores": 12,
            "ramGiB": 61.61,
            "storage": "WD_BLACK SN850X NVMe",
            "filesystem": "NTFS",
            "target": r"C:\benchmark",
            "durableWrite4KiBP95Ms": 1.0,
        },
        "versions": {
            "animaCorefs": "0.1.0",
            "rust": "1.75.0",
            "aesGcm": "0.10",
            "argon2": "0.5",
            "serdeJson": "1.0",
        },
        "warmupCommits": 30,
        "measuredCommits": 200,
        "fixtures": [
            {
                "name": "medium",
                "liveCount": 5_000,
                "tombstoneCount": 500,
                "totalCount": 5_500,
                "serializedSizeBytes": 4_000_000,
                "bytesWritten": 4_000_200,
                "commitMs": {"p50": 20.0, "p95": 40.0, "p99": 50.0},
                "lockHoldMs": {"p50": 19.0, "p95": 39.0, "p99": 49.0},
            },
            {
                "name": "maximum-live",
                "liveCount": 25_000,
                "tombstoneCount": 2_500,
                "totalCount": 27_500,
                "serializedSizeBytes": 10_000_000,
                "bytesWritten": 10_000_200,
                "commitMs": {"p50": 100.0, "p95": 200.0, "p99": 220.0},
                "lockHoldMs": {"p50": 99.0, "p95": 199.0, "p99": 219.0},
            },
        ],
        "gates": {},
    }

    with pytest.raises(benchmark.ReportValidationError, match="serialized-limit"):
        benchmark.validate_and_finalize_report(report)

    report["fixtures"].append(
        {
            "name": "serialized-limit",
            "liveCount": 25_000,
            "tombstoneCount": 0,
            "totalCount": 25_000,
            "serializedSizeBytes": 16 * 1024 * 1024,
            "bytesWritten": 16 * 1024 * 1024 + 200,
            "commitMs": {"p50": 150.0, "p95": 240.0, "p99": 245.0},
            "lockHoldMs": {"p50": 149.0, "p95": 239.0, "p99": 244.0},
        }
    )
    finalized = benchmark.validate_and_finalize_report(report)
    assert finalized["gates"] == {
        "durableWrite4KiBP95Le5Ms": True,
        "mediumP95Le100Ms": True,
        "maximumLiveSerializedSizeLe16MiB": True,
        "maximumLiveP95Le250Ms": True,
        "serializedLimitP95Le250Ms": True,
        "allPassed": True,
    }


def test_oversized_maximum_live_fixture_blocks_before_timing_evidence() -> None:
    benchmark = load_benchmark_module()
    with pytest.raises(benchmark.MaximumLiveSizeGateError, match="exceeds 16 MiB"):
        benchmark.require_supported_maximum_live_size(16 * 1024 * 1024 + 1)
