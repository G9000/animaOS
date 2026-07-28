from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "apps" / "server" / "scripts" / "benchmark_corefs_catalog.py"
OBJECT_LEASE_DIAGNOSTIC_SOURCE = (
    REPO_ROOT
    / "packages"
    / "anima-corefs"
    / "src"
    / "bin"
    / "object_lease_diagnostic.rs"
)
OBJECT_LEASE_BENCHMARK_SOURCE = (
    REPO_ROOT / "packages" / "anima-corefs" / "src" / "benchmark.rs"
)
CORE_FS_PROVENANCE_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "corefs-provenance.yml"
)
PUBLICATION_PATH = [
    "commit-lock",
    "serialize",
    "encrypt",
    "temporary-file-write",
    "durable-flush",
    "atomic-rename",
    "directory-durability",
    "fs-head-write-flush",
]
REFERENCE_TARGET = Path(
    r"C:\Users\test\AppData\Local\animaOS\benchmarks\corefs-catalog-reference-v1"
)
SOURCE_COMMIT = "b" * 40
BINARY_PATH = Path(r"C:\repo\target\release\catalog_benchmark.exe")
BINARY_SHA256 = "c" * 64
BINARY_VOLUME_SERIAL = 17
BINARY_FILE_ID = 23
BUILD_TARGET = Path(r"C:\Temp\anima-corefs-catalog-build-test")
CARGO_LOCK_SHA256 = "e" * 64
EXPECTED_FIXTURE_MANIFEST_FINGERPRINTS = {
    "medium": "d1f8817ba635359cc10208d86b79652dc0e2180c2514f1e1d0634a96ebcb40c4",
    "maximum-live": "1c37d0254fbb9852b5789fa39811f0e1a23a4a3ae440b20c9c478fbf8bf9f7b5",
    "serialized-limit": "26c1c693e8b564e6a971c0af6b62b9b223612bea8bc7c0fe71388abfb06fbd87",
}

OBJECT_LEASE_DIAGNOSTIC_KEYS = {
    "schemaVersion",
    "platform",
    "hardware",
    "os",
    "filesystem",
    "build",
    "objectCount",
    "warmups",
    "samples",
    "safeOpen",
    "lease",
    "resources",
    "teardown",
    "correctness",
    "residueCount",
}


def assert_closed_object_lease_diagnostic_schema(report: dict[str, object]) -> None:
    def exact_type(value: object, expected: type[object]) -> None:
        assert type(value) is expected

    def numeric(value: object) -> None:
        assert type(value) in (int, float)

    assert set(report) == OBJECT_LEASE_DIAGNOSTIC_KEYS
    exact_type(report["schemaVersion"], int)
    assert report["platform"] in {"windows", "macos"}
    exact_type(report["platform"], str)
    exact_type(report["objectCount"], int)
    exact_type(report["warmups"], int)
    exact_type(report["samples"], int)
    exact_type(report["residueCount"], int)
    assert set(report["hardware"]) == {"architecture", "logicalProcessors"}
    exact_type(report["hardware"]["architecture"], str)
    exact_type(report["hardware"]["logicalProcessors"], int)
    assert set(report["os"]) == {"family", "version"}
    exact_type(report["os"]["family"], str)
    exact_type(report["os"]["version"], str)
    assert set(report["filesystem"]) == {"name", "target"}
    exact_type(report["filesystem"]["name"], str)
    exact_type(report["filesystem"]["target"], str)
    assert set(report["safeOpen"]) == {"p50Ms", "p95Ms", "p99Ms"}
    for value in report["safeOpen"].values():
        numeric(value)
    assert set(report["lease"]) == {
        "p50Ms",
        "p95Ms",
        "p99Ms",
        "safeOpenCount",
        "metadataQueryCount",
        "fenceCount",
    }
    for key in ("p50Ms", "p95Ms", "p99Ms"):
        numeric(report["lease"][key])
    for key in ("safeOpenCount", "metadataQueryCount", "fenceCount"):
        exact_type(report["lease"][key], int)
    assert set(report["resources"]) == {
        "liveEntryPermits",
        "liveLeasePermits",
        "liveMonitorResources",
        "postTeardownEntryPermits",
        "postTeardownLeasePermits",
        "postTeardownMonitorResources",
        "descriptorDelta",
    }
    for value in report["resources"].values():
        exact_type(value, int)
    assert set(report["teardown"]) == {
        "targetMs",
        "elapsedMs",
        "completionConfirmed",
        "targetMet",
    }
    assert report["teardown"]["targetMs"] is None or type(
        report["teardown"]["targetMs"]
    ) is int
    assert report["teardown"]["elapsedMs"] is None or type(
        report["teardown"]["elapsedMs"]
    ) in (int, float)
    exact_type(report["teardown"]["completionConfirmed"], bool)
    assert report["teardown"]["targetMet"] is None or type(
        report["teardown"]["targetMet"]
    ) is bool
    assert set(report["correctness"]) == {
        "orderedBoundaryProven",
        "mutationMatrixPassed",
        "teardownPassed",
    }
    for value in report["correctness"].values():
        exact_type(value, bool)
    assert set(report["build"]) == {
        "architecture",
        "argv",
        "cargoLock",
        "crateVersion",
        "debugAssertions",
        "executable",
        "nativeTestContract",
        "output",
        "source",
        "target",
    }
    assert set(report["build"]["source"]) == {"repositoryRoot", "commit", "clean"}
    assert set(report["build"]["executable"]) == {
        "canonicalPath",
        "sha256",
        "volumeSerial",
        "fileId",
    }
    assert set(report["build"]["cargoLock"]) == {
        "canonicalPath",
        "workingSha256",
        "committedSha256",
        "matchesCommit",
    }
    assert set(report["build"]["target"]) == {
        "canonicalPath",
        "volumeSerial",
        "fileId",
    }
    exact_type(report["build"]["argv"], list)
    assert all(type(argument) is str for argument in report["build"]["argv"])
    assert set(report["build"]["output"]) == {
        "canonicalPath",
        "volumeSerial",
        "fileId",
    }
    assert set(report["build"]["nativeTestContract"]) == {
        "sourceCommit",
        "requiredTests",
    }
    exact_type(report["build"]["architecture"], str)
    exact_type(report["build"]["crateVersion"], str)
    exact_type(report["build"]["debugAssertions"], bool)
    exact_type(report["build"]["source"]["repositoryRoot"], str)
    exact_type(report["build"]["source"]["commit"], str)
    exact_type(report["build"]["source"]["clean"], bool)
    exact_type(report["build"]["executable"]["canonicalPath"], str)
    exact_type(report["build"]["executable"]["sha256"], str)
    exact_type(report["build"]["executable"]["volumeSerial"], int)
    exact_type(report["build"]["executable"]["fileId"], int)
    exact_type(report["build"]["cargoLock"]["canonicalPath"], str)
    exact_type(report["build"]["cargoLock"]["workingSha256"], str)
    exact_type(report["build"]["cargoLock"]["committedSha256"], str)
    exact_type(report["build"]["cargoLock"]["matchesCommit"], bool)
    exact_type(report["build"]["target"]["canonicalPath"], str)
    exact_type(report["build"]["target"]["volumeSerial"], int)
    exact_type(report["build"]["target"]["fileId"], int)
    exact_type(report["build"]["output"]["canonicalPath"], str)
    exact_type(report["build"]["output"]["volumeSerial"], int)
    exact_type(report["build"]["output"]["fileId"], int)
    exact_type(report["build"]["nativeTestContract"]["sourceCommit"], str)
    exact_type(report["build"]["nativeTestContract"]["requiredTests"], list)
    assert all(
        type(name) is str
        for name in report["build"]["nativeTestContract"]["requiredTests"]
    )


def assert_object_lease_diagnostic_semantics(report: dict[str, object]) -> None:
    resources = report["resources"]
    teardown = report["teardown"]
    correctness = report["correctness"]
    resource_contract = (
        teardown["completionConfirmed"]
        and resources["postTeardownEntryPermits"] == 0
        and resources["postTeardownLeasePermits"] == 0
        and resources["postTeardownMonitorResources"] == 0
        and resources["descriptorDelta"] == 0
        and report["residueCount"] == 0
    )
    if report["platform"] == "windows":
        assert teardown["targetMs"] == 2000
        assert isinstance(teardown["elapsedMs"], (int, float))
        assert teardown["targetMet"] is True
        assert resources["liveMonitorResources"] == 3
        assert correctness["teardownPassed"] is (
            resource_contract and teardown["targetMet"]
        )
    elif report["platform"] == "macos":
        assert teardown["targetMs"] is None
        assert teardown["elapsedMs"] is None
        assert teardown["targetMet"] is None
        assert correctness["teardownPassed"] is resource_contract
    assert report["lease"]["safeOpenCount"] == 0
    assert report["lease"]["metadataQueryCount"] == report["objectCount"]
    assert report["lease"]["fenceCount"] == 2
    assert correctness["orderedBoundaryProven"] is True
    assert correctness["mutationMatrixPassed"] is True
    build = report["build"]
    source = build["source"]
    executable = build["executable"]
    cargo_lock = build["cargoLock"]
    target = build["target"]
    output = build["output"]
    argv = build["argv"]

    def normalized_path(value: str) -> str:
        without_extended_prefix = (
            value[4:] if value.startswith("\\\\?\\") else value
        )
        return os.path.normcase(os.path.normpath(without_extended_prefix))

    def is_hex(value: str, length: int) -> bool:
        return len(value) == length and all(
            character in "0123456789abcdefABCDEF" for character in value
        )

    def resolved_argv_path(value: str) -> str:
        if report["platform"] == "windows":
            path = PureWindowsPath(value)
            if not path.is_absolute():
                path = PureWindowsPath(source["repositoryRoot"]) / path
        else:
            path = PurePosixPath(value)
            if not path.is_absolute():
                path = PurePosixPath(source["repositoryRoot"]) / path
        return normalized_path(str(path))

    assert source["clean"] is True
    assert is_hex(source["commit"], 40)
    assert source["commit"] == build["nativeTestContract"]["sourceCommit"]
    assert is_hex(executable["sha256"], 64)
    assert is_hex(cargo_lock["workingSha256"], 64)
    assert is_hex(cargo_lock["committedSha256"], 64)
    assert cargo_lock["matchesCommit"] is True
    assert normalized_path(cargo_lock["canonicalPath"]) == normalized_path(
        str(Path(source["repositoryRoot"]) / "Cargo.lock")
    )
    assert len(argv) == 12
    assert resolved_argv_path(argv[0]) == normalized_path(
        executable["canonicalPath"]
    )
    assert argv[1] == "--target"
    assert normalized_path(argv[2]) == normalized_path(target["canonicalPath"])
    assert argv[3:10] == [
        "--objects",
        str(report["objectCount"]),
        "--warmups",
        str(report["warmups"]),
        "--samples",
        str(report["samples"]),
        "--mutation-matrix",
    ]
    assert argv[10] == "--output"
    assert normalized_path(argv[11]) == normalized_path(output["canonicalPath"])
    assert normalized_path(report["filesystem"]["target"]) == normalized_path(
        target["canonicalPath"]
    )


def test_object_lease_diagnostic_source_locks_the_closed_cli_contract() -> None:
    assert OBJECT_LEASE_DIAGNOSTIC_SOURCE.is_file()
    source = OBJECT_LEASE_DIAGNOSTIC_SOURCE.read_text(encoding="utf-8")
    for flag in (
        "--target",
        "--objects",
        "--warmups",
        "--samples",
        "--mutation-matrix",
        "--output",
    ):
        assert flag in source

    report = {
        "schemaVersion": 1,
        "platform": "windows",
        "hardware": {"architecture": "x86_64", "logicalProcessors": 24},
        "os": {"family": "windows", "version": "10.0.26200"},
        "filesystem": {"name": "NTFS", "target": r"C:\diagnostic-target"},
        "build": {
            "architecture": "x86_64",
            "argv": [
                r"target\release\object_lease_diagnostic.exe",
                "--target",
                r"C:\diagnostic-target",
                "--objects",
                "2500",
                "--warmups",
                "30",
                "--samples",
                "200",
                "--mutation-matrix",
                "--output",
                r"C:\diagnostic.json",
            ],
            "cargoLock": {
                "canonicalPath": r"C:\repo\Cargo.lock",
                "workingSha256": "a" * 64,
                "committedSha256": "d" * 64,
                "matchesCommit": True,
            },
            "crateVersion": "0.1.0",
            "debugAssertions": False,
            "executable": {
                "canonicalPath": r"C:\repo\target\release\object_lease_diagnostic.exe",
                "sha256": "b" * 64,
                "volumeSerial": 17,
                "fileId": 23,
            },
            "nativeTestContract": {
                "sourceCommit": "c" * 40,
                "requiredTests": ["windows_event_flood_is_constant_space_and_terminal"],
            },
            "source": {
                "repositoryRoot": r"C:\repo",
                "commit": "c" * 40,
                "clean": True,
            },
            "target": {
                "canonicalPath": r"C:\diagnostic-target",
                "volumeSerial": 17,
                "fileId": 22,
            },
            "output": {
                "canonicalPath": r"C:\diagnostic.json",
                "volumeSerial": 17,
                "fileId": 24,
            },
        },
        "objectCount": 2500,
        "warmups": 30,
        "samples": 200,
        "safeOpen": {"p50Ms": 1.0, "p95Ms": 2.0, "p99Ms": 3.0},
        "lease": {
            "p50Ms": 1.0,
            "p95Ms": 2.0,
            "p99Ms": 3.0,
            "safeOpenCount": 0,
            "metadataQueryCount": 2500,
            "fenceCount": 2,
        },
        "resources": {
            "liveEntryPermits": 2500,
            "liveLeasePermits": 1,
            "liveMonitorResources": 3,
            "postTeardownEntryPermits": 0,
            "postTeardownLeasePermits": 0,
            "postTeardownMonitorResources": 0,
            "descriptorDelta": 0,
        },
        "teardown": {
            "targetMs": 2000,
            "elapsedMs": 10.0,
            "completionConfirmed": True,
            "targetMet": True,
        },
        "correctness": {
            "orderedBoundaryProven": True,
            "mutationMatrixPassed": True,
            "teardownPassed": True,
        },
        "residueCount": 0,
    }
    assert_closed_object_lease_diagnostic_schema(report)
    assert_object_lease_diagnostic_semantics(report)

    macos_report = copy.deepcopy(report)
    macos_report["platform"] = "macos"
    macos_report["teardown"] = {
        "targetMs": None,
        "elapsedMs": None,
        "completionConfirmed": True,
        "targetMet": None,
    }
    assert_closed_object_lease_diagnostic_schema(macos_report)
    assert_object_lease_diagnostic_semantics(macos_report)

    for mutation in (
        lambda value: value.__setitem__("extra", True),
        lambda value: value.pop("platform"),
        lambda value: value["hardware"].__setitem__("extra", True),
        lambda value: value["os"].pop("version"),
        lambda value: value["filesystem"].__setitem__("target", 42),
        lambda value: value.__setitem__("platform", "linux"),
        lambda value: value["lease"].__setitem__("extra", True),
        lambda value: value["resources"].pop("descriptorDelta"),
        lambda value: value["build"]["source"].__setitem__("extra", True),
        lambda value: value["build"].__setitem__("argv", {}),
        lambda value: value["build"]["output"].__setitem__("extra", True),
        lambda value: value["build"]["nativeTestContract"].__setitem__(
            "requiredTests", [1]
        ),
    ):
        invalid = copy.deepcopy(report)
        mutation(invalid)
        with pytest.raises(AssertionError):
            assert_closed_object_lease_diagnostic_schema(invalid)

    for mutation in (
        lambda value: value["build"]["source"].__setitem__("clean", False),
        lambda value: value["build"]["source"].__setitem__("commit", "d" * 40),
        lambda value: value["build"]["executable"].__setitem__("sha256", "not-hex"),
        lambda value: value["build"]["cargoLock"].__setitem__(
            "matchesCommit", False
        ),
        lambda value: value["build"]["cargoLock"].__setitem__(
            "workingSha256", "not-hex"
        ),
        lambda value: value["build"]["argv"].__setitem__(4, "02500"),
        lambda value: value["build"]["argv"].__setitem__(5, "--samples"),
        lambda value: value["build"]["argv"].__setitem__(
            2, r"C:\different-target"
        ),
        lambda value: value["build"]["argv"].__setitem__(
            11, r"C:\different-output.json"
        ),
        lambda value: value["filesystem"].__setitem__(
            "target", r"C:\different-target"
        ),
    ):
        invalid = copy.deepcopy(report)
        mutation(invalid)
        with pytest.raises(AssertionError):
            assert_object_lease_diagnostic_semantics(invalid)


def test_object_lease_diagnostic_report_uses_typed_closed_sections() -> None:
    source = OBJECT_LEASE_BENCHMARK_SOURCE.read_text(encoding="utf-8")
    for dynamic_field in (
        "hardware: serde_json::Value",
        "os: serde_json::Value",
        "filesystem: serde_json::Value",
        "build: serde_json::Value",
    ):
        assert dynamic_field not in source


def test_object_lease_diagnostic_cfg_and_macos_fallback_ci_are_explicit() -> None:
    source = OBJECT_LEASE_BENCHMARK_SOURCE.read_text(encoding="utf-8")
    assert '#[cfg(windows)]\nuse std::fs::File;' in source
    assert 'use std::io::Cursor;\n#[cfg(windows)]\nuse std::io::Read;' in source
    assert '#[cfg(windows)]\nuse std::process::Command;' in source

    workflow = CORE_FS_PROVENANCE_WORKFLOW.read_text(encoding="utf-8")
    macos = workflow.split("  macos-native-lease:", 1)[1]
    assert "uses: actions/setup-python@v6" in macos
    assert 'python-version: "3.13"' in macos
    assert macos.count("if: steps.macos-backend.outputs.enabled == 'true'") == 2
    full_suite = macos.split("      - name: Test all CoreFS paths on macOS", 1)[1]
    full_suite = full_suite.split("      - name:", 1)[0]
    assert "if: steps.macos-backend.outputs.enabled" not in full_suite
    assert "cargo +1.75.0 test --locked -p anima-corefs" in full_suite

    clippy = macos.split("      - name: Enforce strict native Clippy", 1)[1]
    assert "if: steps.macos-backend.outputs.enabled" not in clippy
    assert "cargo +1.75.0 clippy --locked -p anima-corefs -p anima-core" in clippy
    standalone = workflow.split("  windows-native-lease:", 1)[0]
    assert "name: Test CoreFS on Rust 1.75" in standalone
    assert "cargo +1.75.0 test --locked -p anima-corefs" in standalone


def test_object_lease_diagnostic_fallbacks_are_clippy_clean_tail_expressions() -> None:
    source = OBJECT_LEASE_BENCHMARK_SOURCE.read_text(encoding="utf-8")
    assert "    Macos,\n" not in source

    diagnostic = source.split("pub fn run_object_lease_diagnostic(", 1)[1]
    diagnostic = diagnostic.split(
        "#[cfg(windows)]\nfn run_windows_object_lease_diagnostic",
        1,
    )[0]

    assert "return Err(BenchmarkError::BackendUnavailable" not in diagnostic
    assert diagnostic.count("Err(BenchmarkError::BackendUnavailable") == 2


def test_macos_fallback_clippy_cfg_gates_windows_only_test_imports() -> None:
    integration = (
        REPO_ROOT / "packages" / "anima-corefs" / "tests" / "catalog_benchmark.rs"
    ).read_text(encoding="utf-8")
    assert (
        "#[cfg(windows)]\nuse anima_corefs::benchmark::{\n"
        "    run_object_lease_diagnostic, ObjectLeaseDiagnosticConfig,"
    ) in integration

    unit = (
        REPO_ROOT
        / "packages"
        / "anima-corefs"
        / "src"
        / "transaction"
        / "object_lease_tests.rs"
    ).read_text(encoding="utf-8")
    assert (
        "#[cfg(windows)]\n"
        "use super::object_lease::ObjectLeaseDiagnosticObserver;"
    ) in unit


def test_macos_fallback_clippy_avoids_needless_unix_path_borrow() -> None:
    transaction = (
        REPO_ROOT / "packages" / "anima-corefs" / "tests" / "transaction.rs"
    ).read_text(encoding="utf-8")
    assert "fs::hard_link(&object_path, &stale_stage)" not in transaction


def test_macos_fallback_clippy_cfg_gates_windows_session_lease_seam() -> None:
    cache = (
        REPO_ROOT
        / "packages"
        / "anima-corefs"
        / "src"
        / "transaction"
        / "cache.rs"
    ).read_text(encoding="utf-8")
    assert (
        '#[cfg(all(feature = "session-test-seams", windows))]\n'
        "    pub(super) fn with_session_test_object_lease("
    ) in cache


def test_reopened_pcf_002_plan_markers_remain_pending() -> None:
    lease_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-23-corefs-object-validation-lease.md"
    ).read_text(encoding="utf-8")
    plan_header = lease_plan.split("---", 1)[0]
    assert "the plan is complete." not in plan_header
    assert (
        "- [ ] **Step 6: Reapply the final ticket state after reopened validation**"
        in lease_plan
    )

    umbrella_plan = (
        REPO_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-07-12-portable-core-filesystem.md"
    ).read_text(encoding="utf-8")
    task_two_header = umbrella_plan.split("## Task 2:", 1)[1].split("**Files:**", 1)[0]
    normalized_task_two_header = " ".join(task_two_header.split())
    assert "**Completed:**" not in task_two_header
    assert "**Status:** Reopened for PR #125 validation and closeout." in task_two_header
    assert "PCF-003 remains dependency-ineligible" in normalized_task_two_header


def test_windows_native_full_suite_serializes_catalog_diagnostics() -> None:
    workflow = CORE_FS_PROVENANCE_WORKFLOW.read_text(encoding="utf-8")
    windows = workflow.split("  windows-native-lease:", 1)[1].split("  macos-native-lease:", 1)[0]
    full_suite = windows.split("      - name: Test all CoreFS paths on Windows", 1)[1]
    full_suite = full_suite.split("      - name:", 1)[0]

    assert "cargo +1.75.0 test --locked -p anima-corefs" in full_suite
    assert "-- --test-threads=1" in full_suite


def load_benchmark_module():
    assert SCRIPT_PATH.is_file(), "catalog benchmark feature is missing"
    spec = importlib.util.spec_from_file_location("benchmark_corefs_catalog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def live_cim_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "os": {
            "caption": "Microsoft Windows 11 Pro",
            "version": "10.0.26200",
            "architecture": "64-bit",
            "totalVisibleMemoryKiB": 64_606_460,
        },
        "cpu": {
            "name": "AMD Ryzen 9 9900X 12-Core Processor",
            "architectureCodes": [9],
            "physicalCores": 12,
            "logicalProcessors": 24,
        },
        "partition": {"driveLetter": "C", "diskNumber": 0},
        "disk": {
            "number": 0,
            "friendlyName": "WD_BLACK SN850X 2000GB",
            "serialNumber": "SN850X-2TB",
            "busType": "NVMe",
            "healthStatus": "Healthy",
            "operationalStatus": "Online",
            "isOffline": False,
            "isReadOnly": False,
            "location": "Integrated : Bus 2 : Device 0 : Function 0 : Adapter 1",
        },
        "physicalDisk": {
            "deviceId": "0",
            "friendlyName": "WD_BLACK SN850X 2000GB",
            "serialNumber": "SN850X-2TB",
            "busType": "NVMe",
            "mediaType": "SSD",
            "healthStatus": "Healthy",
            "operationalStatus": "OK",
            "isPowerProtected": None,
            "physicalLocation": "Integrated : Bus 2 : Device 0 : Function 0 : Adapter 1",
        },
    }


def volume_facts(
    module,
    *,
    drive_type: str = "fixed",
    filesystem: str = "NTFS",
    volume_root=Path("C:/"),
):
    return module.ReferenceVolumeFacts(
        volume_root=volume_root,
        drive_type=drive_type,
        filesystem=filesystem,
        synchronized_roots=(Path("C:/Users/test/OneDrive"),),
    )


def cache_facts(module, *, write_through: bool = True, flush: bool = True):
    return module.WriteCacheEvidence(
        write_cache_type="write-back",
        write_cache_enabled=True,
        write_cache_changeable="unknown",
        write_through_supported=write_through,
        flush_cache_supported=flush,
        user_defined_power_protection=False,
        nv_cache_enabled=False,
    )


def probe_live_facts(module, payload: dict[str, object], *, volume_root=Path("C:/")):
    completed = SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return completed

    facts = module._probe_live_reference_host_from_sources(
        Path("C:/benchmarks/corefs-catalog-reference-v1"),
        volume_probe=lambda _target: volume_facts(module, volume_root=volume_root),
        cache_probe=lambda _disk_number: cache_facts(module),
        runner=runner,
    )
    assert calls and calls[0][1]["ANIMA_CORE_FS_BENCHMARK_DRIVE"] == "C"
    return facts


def test_injected_live_reference_probe_is_platform_independent(monkeypatch) -> None:
    benchmark = load_benchmark_module()
    monkeypatch.setattr(
        benchmark,
        "os",
        SimpleNamespace(name="posix", environ=os.environ),
    )

    facts = probe_live_facts(
        benchmark,
        live_cim_payload(),
        volume_root=PurePosixPath("C:/"),
    )

    assert facts.os_caption == "Microsoft Windows 11 Pro"
    assert facts.disk_number == 0


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("os", "caption"), "Microsoft Windows 10 Pro", "Windows 11"),
        (("os", "version"), "10.0.19045", "Windows 11"),
        (("os", "architecture"), "32-bit", "x64"),
        (("cpu", "architectureCodes"), [12], "x64"),
        (("cpu", "physicalCores"), 3, "physical cores"),
        (("os", "totalVisibleMemoryKiB"), 15 * 1024 * 1024, "16 GiB"),
        (("disk", "busType"), "SATA", "NVMe"),
        (("physicalDisk", "mediaType"), "HDD", "SSD"),
        (("disk", "number"), 1, "mapping"),
        (("physicalDisk", "deviceId"), "1", "mapping"),
        (("physicalDisk", "serialNumber"), "OTHER", "mapping"),
        (("partition", "driveLetter"), "D", "mapping"),
        (("disk", "friendlyName"), "OTHER", "mapping"),
        (("disk", "isOffline"), True, "online"),
        (("disk", "isReadOnly"), True, "online"),
        (("disk", "location"), "External : USB", "internal"),
    ],
)
def test_live_reference_profile_rejects_unproven_or_wrong_host_facts(
    path: tuple[str, str], value: object, message: str
) -> None:
    benchmark = load_benchmark_module()
    payload = live_cim_payload()
    nested = payload[path[0]]
    assert isinstance(nested, dict)
    nested[path[1]] = value

    with pytest.raises(benchmark.ReferenceTargetError, match=message):
        benchmark.validate_reference_profile(
            Path("C:/benchmarks/corefs-catalog-reference-v1"),
            probe_live_facts(benchmark, payload),
        )


def test_live_reference_probe_rejects_missing_physical_mapping() -> None:
    benchmark = load_benchmark_module()
    payload = live_cim_payload()
    payload["physicalDisk"] = None

    with pytest.raises(benchmark.ReferenceTargetError, match="physical disk"):
        probe_live_facts(benchmark, payload)


@pytest.mark.parametrize("drive_type", ["network", "removable", "ramdisk", "unknown"])
def test_reference_profile_rejects_non_fixed_volume_classes(drive_type: str) -> None:
    benchmark = load_benchmark_module()
    facts = probe_live_facts(benchmark, live_cim_payload())
    facts = facts._replace(volume=volume_facts(benchmark, drive_type=drive_type))

    with pytest.raises(benchmark.ReferenceTargetError, match="fixed local drive"):
        benchmark.validate_reference_profile(
            Path("C:/benchmarks/corefs-catalog-reference-v1"), facts
        )


def test_reference_profile_rejects_non_ntfs_and_synchronized_targets() -> None:
    benchmark = load_benchmark_module()
    facts = probe_live_facts(benchmark, live_cim_payload())
    facts = facts._replace(volume=volume_facts(benchmark, filesystem="ReFS"))
    with pytest.raises(benchmark.ReferenceTargetError, match="NTFS"):
        benchmark.validate_reference_profile(
            Path("C:/benchmarks/corefs-catalog-reference-v1"), facts
        )

    facts = facts._replace(
        volume=benchmark.ReferenceVolumeFacts(
            volume_root=Path("C:/"),
            drive_type="fixed",
            filesystem="NTFS",
            synchronized_roots=(Path("C:/benchmarks"),),
        )
    )
    with pytest.raises(benchmark.ReferenceTargetError, match="synchronized"):
        benchmark.validate_reference_profile(
            Path("C:/benchmarks/corefs-catalog-reference-v1"), facts
        )


@pytest.mark.parametrize(("write_through", "flush"), [(False, True), (True, False), (False, False)])
def test_reference_profile_rejects_unproven_write_cache_durability(
    write_through: bool, flush: bool
) -> None:
    benchmark = load_benchmark_module()
    facts = probe_live_facts(benchmark, live_cim_payload())
    facts = facts._replace(
        write_cache=cache_facts(benchmark, write_through=write_through, flush=flush)
    )

    with pytest.raises(benchmark.ReferenceTargetError, match="durability"):
        benchmark.validate_reference_profile(
            Path("C:/benchmarks/corefs-catalog-reference-v1"), facts
        )


def test_unknown_hardware_power_protection_is_recorded_not_invented() -> None:
    benchmark = load_benchmark_module()
    facts = probe_live_facts(benchmark, live_cim_payload())

    validated = benchmark.validate_reference_profile(
        Path("C:/benchmarks/corefs-catalog-reference-v1"), facts
    )

    assert validated.hardware_power_protection == "unknown-not-reported"
    assert validated.write_cache.write_through_supported is True
    assert validated.write_cache.flush_cache_supported is True


def test_removed_override_flags_cannot_spoof_live_profile() -> None:
    benchmark = load_benchmark_module()
    args = benchmark.parse_args(
        [
            "--reference",
            "--target",
            r"C:\benchmarks\corefs-catalog-reference-v1",
        ]
    )
    assert args.reference is True

    for removed_flag in ("--clean-target", "--cpu"):
        with pytest.raises(SystemExit):
            extra = [] if removed_flag == "--clean-target" else ["spoofed"]
            benchmark.parse_args(
                [
                    "--reference",
                    "--target",
                    r"C:\benchmarks\corefs-catalog-reference-v1",
                    removed_flag,
                    *extra,
                ]
            )


def test_reference_run_counts_stay_within_calibrated_generation_width() -> None:
    benchmark = load_benchmark_module()
    benchmark.validate_reference_run_counts(30, 200)
    benchmark.validate_reference_run_counts(30, 201)
    benchmark.validate_reference_run_counts(30, 967)

    for warmups in (29, 31):
        with pytest.raises(benchmark.ReferenceTargetError, match="exactly 30"):
            benchmark.validate_reference_run_counts(warmups, 200)
    with pytest.raises(benchmark.ReferenceTargetError, match="at least 200"):
        benchmark.validate_reference_run_counts(30, 199)
    with pytest.raises(benchmark.ReferenceTargetError, match="at most 967"):
        benchmark.validate_reference_run_counts(30, 968)


def fixture_record(
    name: str,
    live: int,
    tombstones: int,
    serialized_size: int,
    p95: float,
) -> dict[str, object]:
    return {
        "name": name,
        "liveCount": live,
        "tombstoneCount": tombstones,
        "totalCount": live + tombstones,
        "serializedSizeBytes": serialized_size,
        "warmupSerializedSizeBytes": {
            "min": serialized_size,
            "max": serialized_size,
        },
        "measuredSerializedSizeBytes": {
            "min": serialized_size,
            "max": serialized_size,
        },
        "fixtureManifestSha256": EXPECTED_FIXTURE_MANIFEST_FINGERPRINTS.get(name, "0" * 64),
        "productionSerializationsPerCommit": 1,
        "warmupCommits": 30,
        "sampleCount": 200,
        "finalHeadGeneration": 232,
        "finalCatalogCount": 232,
        "bytesWritten": serialized_size + 206,
        "totalBytesWritten": (serialized_size + 206) * 200,
        "commitMs": {"p50": p95 - 10.0, "p95": p95, "p99": p95 + 10.0},
        "lockHoldMs": {
            "p50": p95 - 11.0,
            "p95": p95 - 1.0,
            "p99": p95 + 9.0,
        },
        "publicationPath": PUBLICATION_PATH,
    }


def complete_report() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "generatedAt": "2026-07-17T20:00:00Z",
        "sourceCommit": SOURCE_COMMIT,
        "benchmarkBuild": {
            "sourceCommit": SOURCE_COMMIT,
            "command": [
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
                str(BUILD_TARGET),
            ],
            "targetDirectory": str(BUILD_TARGET),
            "cargoLockSha256": CARGO_LOCK_SHA256,
            "rustc": "rustc 1.75.0 (82e1608df 2023-12-21)",
            "sanitizedEnvironmentRemoved": ["RUSTFLAGS"],
            "forcedEnvironment": {"CARGO_INCREMENTAL": "0"},
            "preservedForAudit": True,
        },
        "benchmarkBinary": {
            "path": str(BINARY_PATH),
            "sha256": BINARY_SHA256,
            "volumeSerial": BINARY_VOLUME_SERIAL,
            "fileId": BINARY_FILE_ID,
        },
        "benchmarkCommand": [
            str(BINARY_PATH),
            "--target",
            str(REFERENCE_TARGET),
            "--warmups",
            "30",
            "--samples",
            "200",
        ],
        "warmupCommits": 30,
        "measuredCommits": 200,
        "fixtures": [
            fixture_record("medium", 5_000, 500, 1_500_001, 90.0),
            fixture_record("maximum-live", 25_000, 2_500, 7_500_001, 240.0),
            fixture_record("serialized-limit", 25_000, 0, 16 * 1024 * 1024, 245.0),
        ],
        "profile": {
            "mode": "reference",
            "target": str(REFERENCE_TARGET),
            "architecture": "AMD64",
            "hostEvidence": {
                "source": "live-cim",
                "osCaption": "Microsoft Windows 11 Pro",
                "osVersion": "10.0.26200",
                "cpu": "AMD Ryzen 9 9900X 12-Core Processor",
                "cpuArchitectureCodes": [9],
                "physicalCores": 12,
                "logicalProcessors": 24,
                "ramBytes": 66_156_001_280,
                "ramGiB": 61.61,
            },
            "storageEvidence": {
                "source": "live-cim-volume-disk-physical-disk-mapping",
                "volumeRoot": "C:\\",
                "driveType": "fixed",
                "filesystem": "NTFS",
                "partitionDiskNumber": 0,
                "diskNumber": 0,
                "physicalDeviceId": "0",
                "model": "WD_BLACK SN850X 2000GB",
                "serialNumber": "SN850X-2TB",
                "busType": "NVMe",
                "mediaType": "SSD",
                "healthStatus": "Healthy",
                "operationalStatus": "OK",
                "physicalLocation": "Integrated : Bus 2",
                "mappingVerified": True,
                "internal": True,
            },
            "durabilityEvidence": {
                "source": "live-storage-write-cache-property-and-publication-path",
                "hardwarePowerProtection": "unknown-not-reported",
                "writeCacheType": "write-back",
                "writeCacheEnabled": True,
                "writeThroughSupported": True,
                "flushCacheSupported": True,
                "userDefinedPowerProtection": False,
                "nvCacheEnabled": False,
                "softwareFlushMethod": "FlushFileBuffers",
                "publicationUsesWriteThroughAndDirectorySync": True,
                "acceptableProperty": "flush-and-write-through-supported",
            },
            "durableWrite4KiBP95Ms": 0.9,
            "durableWrite4KiB": {
                "warmupCount": 30,
                "sampleCount": 200,
                "p50Ms": 0.6,
                "p95Ms": 0.9,
                "p99Ms": 1.1,
                "flushMethod": "FlushFileBuffers",
            },
            "excludedStorageClasses": [
                "OneDrive-synchronized",
                "network",
                "removable",
                "RAM-disk",
                "write-cache-without-durability",
            ],
        },
        "versions": {
            "animaCorefs": "0.1.0",
            "rust": "rustc 1.75.0",
            "aesGcm": "0.10.3",
            "argon2": "0.5.3",
            "serdeJson": "1.0.150",
        },
        "gates": {
            "durableWrite4KiBP95Le5Ms": True,
            "mediumP95Le100Ms": True,
            "maximumLiveSerializedSizeLe16MiB": True,
            "maximumLiveP95Le250Ms": True,
            "serializedLimitP95Le250Ms": True,
            "allPassed": True,
        },
    }


def validate_report(benchmark, report: dict[str, object]):
    return benchmark.validate_and_finalize_report(
        report,
        expected_source_commit=SOURCE_COMMIT,
        expected_binary_path=BINARY_PATH,
        expected_binary_sha256=BINARY_SHA256,
        expected_binary_volume_serial=BINARY_VOLUME_SERIAL,
        expected_binary_file_id=BINARY_FILE_ID,
        expected_reference_target=REFERENCE_TARGET,
        expected_benchmark_build=complete_report()["benchmarkBuild"],
    )


def remove_path(payload: dict[str, object], path: str) -> None:
    current: object = payload
    parts = path.split(".")
    for part in parts[:-1]:
        if part.isdecimal():
            assert isinstance(current, list)
            current = current[int(part)]
        else:
            assert isinstance(current, dict)
            current = current[part]
    assert isinstance(current, dict)
    del current[parts[-1]]


def test_complete_reference_report_schema_is_accepted() -> None:
    benchmark = load_benchmark_module()
    report = complete_report()

    assert validate_report(benchmark, report) == report


def test_complete_reference_report_accepts_online_physical_storage_status() -> None:
    benchmark = load_benchmark_module()
    report = complete_report()
    report["profile"]["storageEvidence"]["operationalStatus"] = "Online"

    assert validate_report(benchmark, report) == report


def test_reference_report_rejects_samples_above_calibrated_generation_width() -> None:
    benchmark = load_benchmark_module()
    report = complete_report()
    report["measuredCommits"] = 968
    report["benchmarkCommand"][-1] = "968"
    for fixture in report["fixtures"]:
        fixture["sampleCount"] = 968
        fixture["finalHeadGeneration"] = 1_000
        fixture["finalCatalogCount"] = 1_000
        fixture["totalBytesWritten"] = fixture["bytesWritten"] * 968

    with pytest.raises(benchmark.ReportValidationError, match="200 to 967"):
        validate_report(benchmark, report)


def test_reference_report_validation_does_not_use_host_path_semantics(monkeypatch) -> None:
    benchmark = load_benchmark_module()
    report = complete_report()

    def reject_host_path(*_args, **_kwargs):
        raise AssertionError("recorded Windows paths must not use the host Path parser")

    monkeypatch.setattr(benchmark, "Path", reject_host_path)

    assert validate_report(benchmark, report) == report


@pytest.mark.parametrize(
    "missing_path",
    [
        "versions",
        "benchmarkBuild",
        "profile.architecture",
        "profile.target",
        "profile.hostEvidence",
        "profile.storageEvidence",
        "profile.durabilityEvidence",
        "profile.durableWrite4KiB.p50Ms",
        "profile.durableWrite4KiB.sampleCount",
        "fixtures.0.warmupCommits",
        "fixtures.0.sampleCount",
        "fixtures.0.finalHeadGeneration",
        "fixtures.0.finalCatalogCount",
        "fixtures.0.publicationPath",
        "fixtures.0.commitMs.p99",
        "fixtures.0.lockHoldMs.p50",
        "fixtures.0.bytesWritten",
        "fixtures.0.totalBytesWritten",
        "fixtures.0.liveCount",
        "fixtures.0.fixtureManifestSha256",
        "fixtures.0.serializedSizeBytes",
        "fixtures.0.measuredSerializedSizeBytes",
        "gates",
    ],
)
def test_strict_report_schema_rejects_every_missing_required_family(
    missing_path: str,
) -> None:
    benchmark = load_benchmark_module()
    report = complete_report()
    remove_path(report, missing_path)

    with pytest.raises(benchmark.ReportValidationError):
        validate_report(benchmark, report)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate-fixture",
        "extra-fixture",
        "wrong-sample-count",
        "wrong-final-catalog-count",
        "wrong-size-range",
        "old-host-version",
        "wrong-volume-root",
        "extra-nested-field",
        "contradictory-gate",
        "extra-top-level-field",
    ],
)
def test_strict_report_schema_rejects_extra_or_contradictory_records(
    mutation: str,
) -> None:
    benchmark = load_benchmark_module()
    report = complete_report()
    fixtures = report["fixtures"]
    assert isinstance(fixtures, list)
    if mutation == "duplicate-fixture":
        fixtures.append(copy.deepcopy(fixtures[0]))
    elif mutation == "extra-fixture":
        fixtures.append(fixture_record("unexpected", 1, 0, 1_000, 1.0))
    elif mutation == "wrong-sample-count":
        fixtures[0]["sampleCount"] = 199
    elif mutation == "wrong-final-catalog-count":
        fixtures[0]["finalCatalogCount"] = 231
    elif mutation == "wrong-size-range":
        fixtures[2]["measuredSerializedSizeBytes"]["min"] -= 1
    elif mutation == "old-host-version":
        report["profile"]["hostEvidence"]["osVersion"] = "10.0.19045"
    elif mutation == "wrong-volume-root":
        report["profile"]["storageEvidence"]["volumeRoot"] = "D:\\"
    elif mutation == "extra-nested-field":
        report["profile"]["hostEvidence"]["callerSupplied"] = True
    elif mutation == "contradictory-gate":
        report["gates"]["mediumP95Le100Ms"] = False
    elif mutation == "extra-top-level-field":
        report["callerSuppliedHardware"] = "spoofed"

    with pytest.raises(benchmark.ReportValidationError):
        validate_report(benchmark, report)


def test_oversized_maximum_live_fixture_blocks_before_timing_evidence() -> None:
    benchmark = load_benchmark_module()
    with pytest.raises(benchmark.MaximumLiveSizeGateError, match="exceeds 16 MiB"):
        benchmark.require_supported_maximum_live_size(16 * 1024 * 1024 + 1)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schemaVersion",), True),
        (("os", "totalVisibleMemoryKiB"), 64_606_460.0),
        (("cpu", "physicalCores"), 12.0),
        (("cpu", "logicalProcessors"), 24.0),
        (("partition", "diskNumber"), 0.0),
        (("disk", "number"), False),
    ],
)
def test_live_probe_integer_fields_reject_bool_and_float(
    path: tuple[str, ...], value: object
) -> None:
    benchmark = load_benchmark_module()
    payload = live_cim_payload()
    current: object = payload
    for part in path[:-1]:
        assert isinstance(current, dict)
        current = current[part]
    assert isinstance(current, dict)
    current[path[-1]] = value

    with pytest.raises(benchmark.ReferenceTargetError, match=r"integer|schemaVersion"):
        probe_live_facts(benchmark, payload)


@pytest.mark.parametrize(
    "payload",
    [
        '{"schemaVersion": 1, "schemaVersion": 1}',
        '{"value": NaN}',
        '{"value": Infinity}',
        '{"value": -Infinity}',
    ],
)
def test_strict_json_loader_rejects_duplicate_keys_and_non_finite_values(payload: str) -> None:
    benchmark = load_benchmark_module()
    with pytest.raises(benchmark.ReportValidationError):
        benchmark.loads_strict_json(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("schemaVersion", True),
        ("warmupCommits", 30.0),
        ("measuredCommits", 200.0),
        ("fixtures.0.sampleCount", 200.0),
        ("fixtures.0.finalHeadGeneration", 232.0),
        ("profile.durableWrite4KiB.sampleCount", 200.0),
        ("profile.durableWrite4KiB.p95Ms", float("nan")),
        ("fixtures.0.commitMs.p95", float("inf")),
    ],
)
def test_report_rejects_bool_float_integer_fields_and_non_finite_numbers(
    path: str, value: object
) -> None:
    benchmark = load_benchmark_module()
    report = complete_report()
    current: object = report
    parts = path.split(".")
    for part in parts[:-1]:
        if part.isdecimal():
            assert isinstance(current, list)
            current = current[int(part)]
        else:
            assert isinstance(current, dict)
            current = current[part]
    assert isinstance(current, dict)
    current[parts[-1]] = value

    with pytest.raises(benchmark.ReportValidationError):
        validate_report(benchmark, report)


@pytest.mark.parametrize(
    "mutation",
    [
        "arbitrary-hash",
        "duplicate-hash",
        "wrong-command-target",
        "joint-target-spoof",
        "missing-command-target",
        "duplicate-command-target",
        "extra-command-argument",
        "stale-source",
        "stale-binary",
        "wrong-binary-path",
        "wrong-binary-volume",
        "wrong-binary-file-id",
        "spoofed-build-command",
    ],
)
def test_report_is_bound_to_exact_fixtures_command_source_and_binary(mutation: str) -> None:
    benchmark = load_benchmark_module()
    report = complete_report()
    fixtures = report["fixtures"]
    command = report["benchmarkCommand"]
    assert isinstance(fixtures, list)
    assert isinstance(command, list)
    if mutation == "arbitrary-hash":
        fixtures[0]["fixtureManifestSha256"] = "d" * 64
    elif mutation == "duplicate-hash":
        fixtures[1]["fixtureManifestSha256"] = fixtures[0]["fixtureManifestSha256"]
    elif mutation == "wrong-command-target":
        command[2] = str(REFERENCE_TARGET.parent / "other")
    elif mutation == "joint-target-spoof":
        spoofed = REFERENCE_TARGET.parent / "other"
        command[2] = str(spoofed)
        report["profile"]["target"] = str(spoofed)
    elif mutation == "missing-command-target":
        del command[1:3]
    elif mutation == "duplicate-command-target":
        command.extend(["--target", str(REFERENCE_TARGET)])
    elif mutation == "extra-command-argument":
        command.append("--untrusted")
    elif mutation == "stale-source":
        report["sourceCommit"] = "a" * 40
    elif mutation == "stale-binary":
        report["benchmarkBinary"]["sha256"] = "d" * 64
    elif mutation == "wrong-binary-path":
        report["benchmarkBinary"]["path"] = str(BINARY_PATH.parent / "other.exe")
    elif mutation == "wrong-binary-volume":
        report["benchmarkBinary"]["volumeSerial"] = BINARY_VOLUME_SERIAL + 1
    elif mutation == "wrong-binary-file-id":
        report["benchmarkBinary"]["fileId"] = BINARY_FILE_ID + 1
    elif mutation == "spoofed-build-command":
        report["benchmarkBuild"]["command"].append("--features=untrusted")

    with pytest.raises(benchmark.ReportValidationError):
        validate_report(benchmark, report)


def reference_target(local_app_data: Path) -> Path:
    return local_app_data / "animaOS" / "benchmarks" / "corefs-catalog-reference-v1"


def safe_path_probe(benchmark, *, attributes: dict[Path, int] | None = None):
    configured = {path.resolve(): value for path, value in (attributes or {}).items()}

    def probe(path: Path):
        canonical = path.resolve(strict=True)
        return benchmark.ReferencePathEvidence(
            canonical_path=canonical,
            volume_serial=1,
            file_id=abs(hash(str(canonical).casefold())),
            attributes=configured.get(canonical, 0),
        )

    return probe


@pytest.mark.parametrize("danger", ["drive", "home", "repo", "local-app-data", "sibling"])
def test_reference_target_location_rejects_dangerous_or_arbitrary_paths(
    tmp_path: Path, danger: str
) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    target = reference_target(local)
    candidates = {
        "drive": Path(target.anchor or "/"),
        "home": Path.home(),
        "repo": REPO_ROOT,
        "local-app-data": local,
        "sibling": target.parent / "other-benchmark",
    }

    with pytest.raises(benchmark.ReferenceTargetError, match=r"dedicated|dangerous"):
        benchmark.validate_reference_target_location(
            candidates[danger],
            REPO_ROOT / "docs" / "benchmarks" / "artifact.json",
            local,
            (),
        )


def test_reference_target_rejects_artifact_parent_or_ancestor(tmp_path: Path) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    target = reference_target(local)

    with pytest.raises(benchmark.ReferenceTargetError, match="artifact"):
        benchmark.validate_reference_target_location(
            target,
            target / "catalog-reference-v1.json",
            local,
            (),
        )


def test_first_creation_writes_versioned_runner_sentinel(tmp_path: Path) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    local.mkdir()
    target = reference_target(local)

    benchmark.prepare_reference_target(
        target,
        REPO_ROOT / "docs" / "benchmarks" / "artifact.json",
        local_app_data_probe=lambda: local,
        sync_roots_probe=lambda: (),
        path_probe=safe_path_probe(benchmark),
    )

    sentinel = target / benchmark.TARGET_SENTINEL_NAME
    assert sentinel.read_bytes() == benchmark.TARGET_SENTINEL_CONTENT


@pytest.mark.parametrize(
    "sentinel",
    [None, b"wrong-owner-or-version\n", b"animaOS CoreFS catalog benchmark target v1\n"],
)
def test_existing_target_is_never_deleted_even_with_owned_sentinel(
    tmp_path: Path, sentinel: bytes | None
) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    target = reference_target(local)
    target.mkdir(parents=True)
    if sentinel is not None:
        (target / benchmark.TARGET_SENTINEL_NAME).write_bytes(sentinel)
    retained = target / "retained.txt"
    retained.write_text("must survive", encoding="utf-8")

    with pytest.raises(benchmark.ReferenceTargetError, match=r"already exists|archive"):
        benchmark.prepare_reference_target(
            target,
            REPO_ROOT / "docs" / "benchmarks" / "artifact.json",
            local_app_data_probe=lambda: local,
            sync_roots_probe=lambda: (),
            path_probe=safe_path_probe(benchmark),
        )
    assert target.is_dir() and retained.read_text(encoding="utf-8") == "must survive"


@pytest.mark.parametrize(
    "attribute",
    [
        0x00000400,  # FILE_ATTRIBUTE_REPARSE_POINT
        0x00001000,  # FILE_ATTRIBUTE_OFFLINE
        0x00040000,  # FILE_ATTRIBUTE_RECALL_ON_OPEN
        0x00400000,  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    ],
)
def test_creation_rejects_reparse_offline_or_cloud_files_ancestors(
    tmp_path: Path, attribute: int
) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    target = reference_target(local)
    ancestor = local / "animaOS"
    ancestor.mkdir(parents=True)

    with pytest.raises(benchmark.ReferenceTargetError, match=r"reparse|offline|Cloud Files"):
        benchmark.prepare_reference_target(
            target,
            REPO_ROOT / "docs" / "benchmarks" / "artifact.json",
            local_app_data_probe=lambda: local,
            sync_roots_probe=lambda: (),
            path_probe=safe_path_probe(benchmark, attributes={ancestor: attribute}),
        )
    assert not target.exists()


def test_creation_fails_closed_when_path_probe_fails(tmp_path: Path) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    target = reference_target(local)
    target.mkdir(parents=True)
    (target / benchmark.TARGET_SENTINEL_NAME).write_bytes(benchmark.TARGET_SENTINEL_CONTENT)

    def failed_probe(_path: Path):
        raise OSError("probe denied")

    with pytest.raises(benchmark.ReferenceTargetError, match="probe"):
        benchmark.prepare_reference_target(
            target,
            REPO_ROOT / "docs" / "benchmarks" / "artifact.json",
            local_app_data_probe=lambda: local,
            sync_roots_probe=lambda: (),
            path_probe=failed_probe,
        )


def test_post_run_binary_provenance_rejects_identity_or_hash_changes() -> None:
    benchmark = load_benchmark_module()
    initial = benchmark.ReferencePathEvidence(
        canonical_path=BINARY_PATH,
        volume_serial=BINARY_VOLUME_SERIAL,
        file_id=BINARY_FILE_ID,
        attributes=0,
    )

    benchmark.verify_benchmark_binary_after_run(
        BINARY_PATH,
        initial,
        BINARY_SHA256,
        path_probe=lambda _path: initial,
        hash_probe=lambda: BINARY_SHA256,
    )

    for current, digest in (
        (initial._replace(file_id=BINARY_FILE_ID + 1), BINARY_SHA256),
        (initial, "d" * 64),
    ):
        with pytest.raises(benchmark.ReportValidationError, match=r"identity|changed"):
            benchmark.verify_benchmark_binary_after_run(
                BINARY_PATH,
                initial,
                BINARY_SHA256,
                path_probe=lambda _path, value=current: value,
                hash_probe=lambda value=digest: value,
            )


@pytest.mark.skipif(os.name != "nt", reason="reference runner is Windows-only")
def test_held_binary_can_execute_and_remains_bound_to_the_same_file() -> None:
    benchmark = load_benchmark_module()
    binary = Path(sys.executable).resolve()

    with benchmark.hold_benchmark_binary(binary) as held:
        completed = subprocess.run(
            [str(binary), "-c", "print('held-binary-ok')"],
            check=True,
            capture_output=True,
            text=True,
        )
        benchmark.verify_benchmark_binary_after_run(
            binary,
            held.evidence,
            held.sha256,
            hash_probe=held.hash_probe,
        )

    assert completed.stdout.strip() == "held-binary-ok"


def test_reference_target_chain_revalidation_rejects_identity_changes() -> None:
    benchmark = load_benchmark_module()
    local = Path(r"C:\Users\test\AppData\Local")
    target = local / "animaOS" / "benchmarks" / "corefs-catalog-reference-v1"
    paths = (local, local / "animaOS", target)
    evidence = tuple(
        benchmark.ReferencePathEvidence(path, 7, index + 1, 0) for index, path in enumerate(paths)
    )
    held = benchmark.HeldReferenceTargetChain(paths, evidence)

    benchmark.verify_reference_target_chain(
        held,
        path_probe=lambda path: evidence[paths.index(path)],
    )
    with pytest.raises(benchmark.ReferenceTargetError, match="identity changed"):
        benchmark.verify_reference_target_chain(
            held,
            path_probe=lambda path: (
                evidence[paths.index(path)]._replace(file_id=99)
                if path == target
                else evidence[paths.index(path)]
            ),
        )


@pytest.mark.skipif(os.name != "nt", reason="reference runner is Windows-only")
def test_held_reference_target_chain_blocks_target_rename(tmp_path: Path) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    target = reference_target(local)
    target.mkdir(parents=True)

    with benchmark.hold_reference_target_chain(local, target) as held:
        benchmark.verify_reference_target_chain(held)
        with pytest.raises(OSError):
            target.rename(target.with_name("replaced"))

    assert target.is_dir()


def test_release_build_uses_committed_lockfile_and_sanitized_private_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    benchmark = load_benchmark_module()
    private_target = tmp_path / "private-cargo-target"
    monkeypatch.setattr(benchmark.tempfile, "mkdtemp", lambda **_kwargs: str(private_target))
    monkeypatch.setenv("RUSTFLAGS", "-C target-cpu=native")
    monkeypatch.setenv("CARGO_TARGET_DIR", str(tmp_path / "shared"))
    calls: list[tuple[list[str], dict[str, object]]] = []
    committed_lockfile = b"committed-lockfile-with-lf\n"

    def runner(command, **kwargs):
        command = list(command)
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(stdout=committed_lockfile, returncode=0)
        if "build" in command:
            suffix = ".exe" if os.name == "nt" else ""
            binary = private_target / "release" / f"catalog_benchmark{suffix}"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"private-build")
            return SimpleNamespace(stdout="", returncode=0)
        return SimpleNamespace(stdout="rustc 1.75.0\nhost: x86_64-pc-windows-msvc\n", returncode=0)

    monkeypatch.setattr(benchmark.subprocess, "run", runner)
    build = benchmark.build_rust_benchmark_binary(SOURCE_COMMIT)

    assert build.target_directory == private_target
    assert build.binary.parent == private_target / "release"
    assert build.command[-2:] == ("--target-dir", str(private_target))
    build_environment = calls[0][1]["env"]
    assert build_environment["CARGO_INCREMENTAL"] == "0"
    assert "RUSTFLAGS" not in build_environment
    assert "CARGO_TARGET_DIR" not in build_environment
    assert build.sanitized_environment_removed == ("CARGO_TARGET_DIR", "RUSTFLAGS")
    assert calls[-1][0] == ["git", "cat-file", "blob", f"{SOURCE_COMMIT}:Cargo.lock"]
    assert build.cargo_lock_sha256 == hashlib.sha256(committed_lockfile).hexdigest()


def test_registered_sync_roots_cover_multiple_accounts_and_alternate_providers() -> None:
    benchmark = load_benchmark_module()
    roots = benchmark.collect_synchronized_roots(
        environ={},
        registry_probe=lambda: (
            Path(r"C:\Users\test\OneDrive"),
            Path(r"C:\Users\test\OneDrive - Business A"),
            Path(r"D:\Alternate OneDrive Provider"),
        ),
    )
    assert roots == (
        Path(r"C:\Users\test\OneDrive"),
        Path(r"C:\Users\test\OneDrive - Business A"),
        Path(r"D:\Alternate OneDrive Provider"),
    )


def test_registered_roots_catch_sync_target_with_missing_or_stale_environment() -> None:
    benchmark = load_benchmark_module()
    registered = Path(r"C:\Users\test\OneDrive - Business A")
    roots = benchmark.collect_synchronized_roots(
        environ={"OneDrive": r"C:\stale\missing"},
        registry_probe=lambda: (registered,),
    )
    facts = benchmark.ReferenceVolumeFacts(
        volume_root=Path("C:/"),
        drive_type="fixed",
        filesystem="NTFS",
        synchronized_roots=roots,
    )
    with pytest.raises(benchmark.ReferenceTargetError, match="synchronized"):
        benchmark._validate_reference_volume(registered / "benchmark", facts)


def test_registered_sync_root_probe_failure_is_fail_closed() -> None:
    benchmark = load_benchmark_module()

    def failed_registry_probe():
        raise OSError("registry denied")

    with pytest.raises(benchmark.ReferenceTargetError, match=r"sync|registry"):
        benchmark.collect_synchronized_roots(environ={}, registry_probe=failed_registry_probe)
