from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "apps" / "server" / "scripts" / "benchmark_corefs_catalog.py"
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
REFERENCE_TARGET = Path(
    r"C:\Users\test\AppData\Local\animaOS\benchmarks\corefs-catalog-reference-v1"
)
SOURCE_COMMIT = "b" * 40
BINARY_PATH = Path(r"C:\repo\target\release\catalog_benchmark.exe")
BINARY_SHA256 = "c" * 64
EXPECTED_FIXTURE_FINGERPRINTS = {
    "medium": "2c55af03723c2f10f40790b894c421fde0f9cd10f9b2355f128aad66d7cfc1d5",
    "maximum-live": "95e6a054dee2fa09b48743429c37f1e4a54ba32008136755393e0341913b2857",
    "serialized-limit": "f24864db4cefda29bf71975cd71da21c0564ec44eee9e59dada04aba82c20df4",
}


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


def volume_facts(module, *, drive_type: str = "fixed", filesystem: str = "NTFS"):
    return module.ReferenceVolumeFacts(
        volume_root=Path("C:/"),
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


def probe_live_facts(module, payload: dict[str, object]):
    completed = SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return completed

    facts = module.probe_live_reference_host(
        Path("C:/benchmarks/corefs-catalog-reference-v1"),
        volume_probe=lambda _target: volume_facts(module),
        cache_probe=lambda _disk_number: cache_facts(module),
        runner=runner,
    )
    assert calls and calls[0][1]["ANIMA_CORE_FS_BENCHMARK_DRIVE"] == "C"
    return facts


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
            "--clean-target",
        ]
    )
    assert args.reference is True

    with pytest.raises(SystemExit):
        benchmark.parse_args(
            [
                "--reference",
                "--target",
                r"C:\benchmarks\corefs-catalog-reference-v1",
                "--clean-target",
                "--cpu",
                "spoofed",
            ]
        )


def test_reference_warmups_are_exact_and_samples_are_a_floor() -> None:
    benchmark = load_benchmark_module()
    benchmark.validate_reference_run_counts(30, 200)
    benchmark.validate_reference_run_counts(30, 201)

    for warmups in (29, 31):
        with pytest.raises(benchmark.ReferenceTargetError, match="exactly 30"):
            benchmark.validate_reference_run_counts(warmups, 200)
    with pytest.raises(benchmark.ReferenceTargetError, match="at least 200"):
        benchmark.validate_reference_run_counts(30, 199)


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
        "fixtureSha256": EXPECTED_FIXTURE_FINGERPRINTS.get(name, "0" * 64),
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
        "benchmarkBinary": {
            "path": str(BINARY_PATH),
            "sha256": BINARY_SHA256,
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


@pytest.mark.parametrize(
    "missing_path",
    [
        "versions",
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
        "fixtures.0.fixtureSha256",
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
        "missing-command-target",
        "duplicate-command-target",
        "extra-command-argument",
        "stale-source",
        "stale-binary",
        "wrong-binary-path",
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
        fixtures[0]["fixtureSha256"] = "d" * 64
    elif mutation == "duplicate-hash":
        fixtures[1]["fixtureSha256"] = fixtures[0]["fixtureSha256"]
    elif mutation == "wrong-command-target":
        command[2] = str(REFERENCE_TARGET.parent / "other")
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
        clean_target=True,
        local_app_data_probe=lambda: local,
        sync_roots_probe=lambda: (),
        path_probe=safe_path_probe(benchmark),
    )

    sentinel = target / benchmark.TARGET_SENTINEL_NAME
    assert sentinel.read_bytes() == benchmark.TARGET_SENTINEL_CONTENT


@pytest.mark.parametrize("sentinel", [None, b"wrong-owner-or-version\n"])
def test_cleanup_rejects_missing_or_mismatched_sentinel(
    tmp_path: Path, sentinel: bytes | None
) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    target = reference_target(local)
    target.mkdir(parents=True)
    if sentinel is not None:
        (target / benchmark.TARGET_SENTINEL_NAME).write_bytes(sentinel)

    with pytest.raises(benchmark.ReferenceTargetError, match="sentinel"):
        benchmark.prepare_reference_target(
            target,
            REPO_ROOT / "docs" / "benchmarks" / "artifact.json",
            clean_target=True,
            local_app_data_probe=lambda: local,
            sync_roots_probe=lambda: (),
            path_probe=safe_path_probe(benchmark),
        )
    assert target.is_dir()


@pytest.mark.parametrize(
    "attribute",
    [
        0x00000400,  # FILE_ATTRIBUTE_REPARSE_POINT
        0x00001000,  # FILE_ATTRIBUTE_OFFLINE
        0x00040000,  # FILE_ATTRIBUTE_RECALL_ON_OPEN
        0x00400000,  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    ],
)
def test_cleanup_rejects_reparse_offline_or_cloud_files_tree_entries(
    tmp_path: Path, attribute: int
) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    target = reference_target(local)
    child = target / "fixture"
    child.mkdir(parents=True)
    (target / benchmark.TARGET_SENTINEL_NAME).write_bytes(benchmark.TARGET_SENTINEL_CONTENT)

    with pytest.raises(benchmark.ReferenceTargetError, match=r"reparse|offline|Cloud Files"):
        benchmark.prepare_reference_target(
            target,
            REPO_ROOT / "docs" / "benchmarks" / "artifact.json",
            clean_target=True,
            local_app_data_probe=lambda: local,
            sync_roots_probe=lambda: (),
            path_probe=safe_path_probe(benchmark, attributes={child: attribute}),
        )
    assert target.is_dir() and child.is_dir()


def test_cleanup_fails_closed_when_target_identity_changes_before_delete(tmp_path: Path) -> None:
    benchmark = load_benchmark_module()
    local = tmp_path / "LocalAppData"
    target = reference_target(local)
    target.mkdir(parents=True)
    (target / benchmark.TARGET_SENTINEL_NAME).write_bytes(benchmark.TARGET_SENTINEL_CONTENT)
    safe_probe = safe_path_probe(benchmark)
    target_calls = 0

    def swapped_probe(path: Path):
        nonlocal target_calls
        evidence = safe_probe(path)
        if path.resolve() != target.resolve():
            return evidence
        target_calls += 1
        return evidence._replace(file_id=1 if target_calls == 1 else 2)

    with pytest.raises(benchmark.ReferenceTargetError, match=r"replaced|identity"):
        benchmark.prepare_reference_target(
            target,
            REPO_ROOT / "docs" / "benchmarks" / "artifact.json",
            clean_target=True,
            local_app_data_probe=lambda: local,
            sync_roots_probe=lambda: (),
            path_probe=swapped_probe,
        )
    assert target.is_dir()


def test_cleanup_fails_closed_when_path_probe_fails(tmp_path: Path) -> None:
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
            clean_target=True,
            local_app_data_probe=lambda: local,
            sync_roots_probe=lambda: (),
            path_probe=failed_probe,
        )


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
