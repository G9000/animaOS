from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from anima_server.services.corefs import transfer
from anima_server.services.corefs.transfer import (
    ARCHIVE_FRAME_RESERVE_BYTES,
    CAPACITY_MARGIN_BYTES,
    FAT32_MAX_FILE_BYTES,
    PublicationMode,
    TransferCancelled,
    TransferError,
    activate_staged_core,
    estimate_transfer,
    initialize_active_core_pointer,
    probe_import_staging,
    probe_local_destination,
    publish_multipart,
    publish_single_file,
    read_active_core_pointer,
    recover_active_core_activation,
    rollback_to_retained_core,
)


def _producer(value: bytes):
    def produce(path: Path) -> None:
        path.write_bytes(value)

    return produce


def _verifier(expected: bytes):
    def verify(path: Path) -> None:
        if path.read_bytes() != expected:
            raise TransferError("verification failed")

    return verify


def _controller(path: Path, volumes) -> None:
    path.write_text(
        json.dumps(
            {
                "volumes": [
                    {
                        "ordinal": volume.ordinal,
                        "filename": volume.filename,
                        "length": volume.length,
                        "sha256": volume.sha256,
                    }
                    for volume in volumes
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _controller_verifier(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if [entry["ordinal"] for entry in payload["volumes"]] != [1, 2]:
        raise TransferError("controller inventory is invalid")


def _core(path: Path, core_id: str, marker: str) -> None:
    path.mkdir()
    (path / "fs").mkdir()
    (path / "manifest.json").write_text(
        json.dumps({"core_id": core_id, "marker": marker}),
        encoding="utf-8",
    )
    (path / "fs" / "HEAD").write_bytes(b"authenticated head")


def _core_verifier(core_id: str):
    def verify(path: Path) -> None:
        payload = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if payload.get("core_id") != core_id or not (path / "fs" / "HEAD").is_file():
            raise TransferError("restored Core verification failed")

    return verify


def test_destination_probe_selects_single_or_fat32_multipart_without_residue(
    tmp_path: Path,
) -> None:
    estimate = estimate_transfer(selected_bytes=5 * 1024**3, record_count=4)
    available = estimate.required_capacity_bytes + CAPACITY_MARGIN_BYTES

    single = probe_local_destination(
        tmp_path,
        estimate,
        available_bytes=available,
        maximum_single_file_bytes=8 * 1024**3,
    )
    assert single.publication_mode is PublicationMode.SINGLE_FILE
    assert single.declared_volume_count == 1

    multipart = probe_local_destination(
        tmp_path,
        estimate,
        available_bytes=available,
        maximum_single_file_bytes=FAT32_MAX_FILE_BYTES,
    )
    assert multipart.publication_mode is PublicationMode.MULTIPART
    assert multipart.part_limit_bytes == 2 * 1024**3
    assert multipart.declared_volume_count == 3
    assert list(tmp_path.iterdir()) == []


def test_destination_probe_rejects_capacity_active_core_and_tiny_file_limit(
    tmp_path: Path,
) -> None:
    estimate = estimate_transfer(selected_bytes=1024, record_count=1)
    with pytest.raises(TransferError, match="insufficient capacity"):
        probe_local_destination(
            tmp_path,
            estimate,
            available_bytes=estimate.required_capacity_bytes - 1,
            maximum_single_file_bytes=None,
        )
    with pytest.raises(TransferError, match="active Core"):
        probe_local_destination(
            tmp_path,
            estimate,
            forbidden_roots=(tmp_path,),
            available_bytes=estimate.required_capacity_bytes,
        )

    large = estimate_transfer(
        selected_bytes=ARCHIVE_FRAME_RESERVE_BYTES * 2,
        record_count=1,
    )
    with pytest.raises(TransferError, match="too small"):
        probe_local_destination(
            tmp_path,
            large,
            available_bytes=large.required_capacity_bytes,
            maximum_single_file_bytes=ARCHIVE_FRAME_RESERVE_BYTES,
        )


def test_filesystem_limit_detection_is_closed_to_known_fat_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transfer, "_filesystem_name", lambda _path: "vfat")
    assert transfer.detect_maximum_single_file_bytes(tmp_path) == FAT32_MAX_FILE_BYTES
    monkeypatch.setattr(transfer, "_filesystem_name", lambda _path: "apfs")
    assert transfer.detect_maximum_single_file_bytes(tmp_path) is None


@pytest.mark.parametrize(
    "boundary",
    [
        "single:before_write",
        "single:after_write",
        "single:after_file_fsync",
        "single:after_verify",
        "single:before_rename",
    ],
)
def test_single_file_failure_before_commit_removes_partial_output(
    tmp_path: Path,
    boundary: str,
) -> None:
    def fail_at(name: str) -> None:
        if name == boundary:
            raise OSError("injected publication failure")

    with pytest.raises(OSError, match="injected"):
        publish_single_file(
            tmp_path,
            "core.anima",
            producer=_producer(b"authenticated archive"),
            verifier=_verifier(b"authenticated archive"),
            boundary_hook=fail_at,
        )
    assert not (tmp_path / "core.anima").exists()
    assert not (tmp_path / "core.anima.partial").exists()


@pytest.mark.parametrize("boundary", ["single:after_rename", "single:after_parent_fsync"])
def test_single_file_failure_after_commit_leaves_only_verified_final(
    tmp_path: Path,
    boundary: str,
) -> None:
    def fail_at(name: str) -> None:
        if name == boundary:
            raise OSError("simulated crash seam")

    with pytest.raises(OSError, match="crash seam"):
        publish_single_file(
            tmp_path,
            "core.anima",
            producer=_producer(b"authenticated archive"),
            verifier=_verifier(b"authenticated archive"),
            boundary_hook=fail_at,
        )
    assert (tmp_path / "core.anima").read_bytes() == b"authenticated archive"
    assert not (tmp_path / "core.anima.partial").exists()


def test_single_file_cancel_or_verify_failure_cleans_partial(tmp_path: Path) -> None:
    calls = 0

    def cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls == 2

    with pytest.raises(TransferCancelled):
        publish_single_file(
            tmp_path,
            "cancelled.anima",
            producer=_producer(b"incomplete"),
            verifier=_verifier(b"incomplete"),
            cancel_requested=cancel,
        )
    assert list(tmp_path.iterdir()) == []

    with pytest.raises(TransferError, match="verification failed"):
        publish_single_file(
            tmp_path,
            "invalid.anima",
            producer=_producer(b"invalid"),
            verifier=_verifier(b"expected"),
        )
    assert list(tmp_path.iterdir()) == []


def test_multipart_publishes_ordered_verified_volumes_and_controller_last(
    tmp_path: Path,
) -> None:
    observed_controller_boundary = False

    def inspect_boundary(name: str) -> None:
        nonlocal observed_controller_boundary
        if name == "multipart:controller:before_write":
            observed_controller_boundary = True
            partial = tmp_path / "portable-core.partial"
            assert sorted(path.name for path in partial.iterdir()) == [
                "volume-0001.anima-part",
                "volume-0002.anima-part",
            ]

    result = publish_multipart(
        tmp_path,
        "portable-core",
        volume_producers=(_producer(b"volume one"), _producer(b"volume two")),
        controller_producer=_controller,
        volume_verifier=lambda path: path.read_bytes(),
        controller_verifier=_controller_verifier,
        part_limit_bytes=ARCHIVE_FRAME_RESERVE_BYTES + 1,
        boundary_hook=inspect_boundary,
    )

    assert observed_controller_boundary
    assert result.mode is PublicationMode.MULTIPART
    assert [volume.ordinal for volume in result.volumes] == [1, 2]
    assert result.volumes[0].sha256 == hashlib.sha256(b"volume one").hexdigest()
    assert sorted(path.name for path in result.path.iterdir()) == [
        "core.anima",
        "volume-0001.anima-part",
        "volume-0002.anima-part",
    ]
    assert not (tmp_path / "portable-core.partial").exists()


@pytest.mark.parametrize(
    "boundary",
    [
        "multipart:after_partial_directory",
        "multipart:part:1:before_write",
        "multipart:part:1:after_write",
        "multipart:part:1:after_file_fsync",
        "multipart:part:1:after_verify",
        "multipart:part:1:after_rename",
        "multipart:part:2:before_write",
        "multipart:part:2:after_write",
        "multipart:part:2:after_file_fsync",
        "multipart:part:2:after_verify",
        "multipart:part:2:after_rename",
        "multipart:controller:before_write",
        "multipart:controller:after_write",
        "multipart:controller:after_verify",
        "multipart:controller:after_rename",
        "multipart:before_directory_rename",
    ],
)
def test_multipart_failure_before_final_commit_removes_partial_set(
    tmp_path: Path,
    boundary: str,
) -> None:
    def fail_at(name: str) -> None:
        if name == boundary:
            raise OSError("injected multipart failure")

    with pytest.raises(OSError, match="injected"):
        publish_multipart(
            tmp_path,
            "portable-core",
            volume_producers=(_producer(b"one"), _producer(b"two")),
            controller_producer=_controller,
            volume_verifier=lambda path: path.read_bytes(),
            controller_verifier=_controller_verifier,
            part_limit_bytes=ARCHIVE_FRAME_RESERVE_BYTES + 1,
            boundary_hook=fail_at,
        )
    assert not (tmp_path / "portable-core").exists()
    assert not (tmp_path / "portable-core.partial").exists()


@pytest.mark.parametrize(
    "boundary",
    ["multipart:after_directory_rename", "multipart:after_parent_fsync"],
)
def test_multipart_failure_after_commit_leaves_complete_controller_set(
    tmp_path: Path,
    boundary: str,
) -> None:
    def fail_at(name: str) -> None:
        if name == boundary:
            raise OSError("simulated multipart crash seam")

    with pytest.raises(OSError, match="crash seam"):
        publish_multipart(
            tmp_path,
            "portable-core",
            volume_producers=(_producer(b"one"), _producer(b"two")),
            controller_producer=_controller,
            volume_verifier=lambda path: path.read_bytes(),
            controller_verifier=_controller_verifier,
            part_limit_bytes=ARCHIVE_FRAME_RESERVE_BYTES + 1,
            boundary_hook=fail_at,
        )
    final = tmp_path / "portable-core"
    assert (final / "core.anima").is_file()
    assert (final / "volume-0001.anima-part").is_file()
    assert (final / "volume-0002.anima-part").is_file()
    assert not (tmp_path / "portable-core.partial").exists()


def test_multipart_rejects_oversized_part_and_unsafe_names(tmp_path: Path) -> None:
    with pytest.raises(TransferError, match="exceeds"):
        publish_multipart(
            tmp_path,
            "portable-core",
            volume_producers=(
                _producer(b"x" * (ARCHIVE_FRAME_RESERVE_BYTES + 2)),
                _producer(b"two"),
            ),
            controller_producer=_controller,
            volume_verifier=lambda path: path.read_bytes(),
            controller_verifier=_controller_verifier,
            part_limit_bytes=ARCHIVE_FRAME_RESERVE_BYTES + 1,
        )
    assert list(tmp_path.iterdir()) == []

    with pytest.raises(TransferError, match="unsafe"):
        publish_single_file(
            tmp_path,
            "../escape.anima",
            producer=_producer(b"no"),
            verifier=_verifier(b"no"),
        )


def test_import_capacity_requires_complete_same_volume_sibling_space(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    restored_bytes = 512 * 1024 * 1024
    required = restored_bytes + CAPACITY_MARGIN_BYTES
    probe = probe_import_staging(
        tmp_path,
        restored_core_bytes=restored_bytes,
        active_core_path=active,
        available_bytes=required,
    )
    assert probe.required_capacity_bytes == required
    assert probe.staging_parent == tmp_path.resolve()

    with pytest.raises(TransferError, match="same-volume staging capacity"):
        probe_import_staging(
            tmp_path,
            restored_core_bytes=restored_bytes,
            active_core_path=active,
            available_bytes=required - 1,
        )
    with pytest.raises(TransferError, match="inside the active Core"):
        probe_import_staging(
            active,
            restored_core_bytes=restored_bytes,
            active_core_path=active,
            available_bytes=required,
        )


def test_authenticated_active_core_pointer_rejects_tampering(tmp_path: Path) -> None:
    core_id = str(uuid4())
    key = b"t" * 32
    active = tmp_path / "cores" / "active"
    active.parent.mkdir()
    _core(active, core_id, "old")
    registry = tmp_path / "app-data" / "active-core.json"

    initialized = initialize_active_core_pointer(
        registry,
        authentication_key=key,
        core_id=core_id,
        active_core_path=active,
    )
    assert initialized.generation == 1
    assert read_active_core_pointer(registry, authentication_key=key) == initialized

    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["generation"] = 2
    registry.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(TransferError, match="tag is invalid"):
        read_active_core_pointer(registry, authentication_key=key)


def test_activation_swaps_authenticated_pointer_and_retains_old_core(tmp_path: Path) -> None:
    core_id = str(uuid4())
    activation_id = str(uuid4())
    key = b"k" * 32
    cores = tmp_path / "cores"
    cores.mkdir()
    old = cores / "old"
    staging = cores / "new.staging"
    final = cores / "new"
    _core(old, core_id, "old")
    _core(staging, core_id, "new")
    registry = tmp_path / "app-data" / "active-core.json"
    initialize_active_core_pointer(
        registry,
        authentication_key=key,
        core_id=core_id,
        active_core_path=old,
    )

    result = activate_staged_core(
        staging,
        final,
        registry,
        authentication_key=key,
        core_id=core_id,
        activation_id=activation_id,
        verifier=_core_verifier(core_id),
    )

    assert result.pointer.generation == 2
    assert result.pointer.active_core_path == final.resolve()
    assert result.pointer.retained_core_path == old.resolve()
    assert old.is_dir()
    assert final.is_dir()
    assert not staging.exists()
    assert result.completion_path.is_file()
    assert not registry.with_name("active-core.json.activation").exists()
    assert read_active_core_pointer(registry, authentication_key=key) == result.pointer


@pytest.mark.parametrize(
    "boundary,pointer_is_new",
    [
        ("activation:after_journal", False),
        ("activation:after_directory_rename", False),
        ("activation:after_pointer", True),
        ("activation:after_completion", True),
        ("activation:after_journal_cleanup", True),
    ],
)
def test_activation_recovers_every_directory_pointer_completion_crash_seam(
    tmp_path: Path,
    boundary: str,
    pointer_is_new: bool,
) -> None:
    core_id = str(uuid4())
    activation_id = str(uuid4())
    key = b"r" * 32
    cores = tmp_path / "cores"
    cores.mkdir()
    old = cores / "old"
    staging = cores / "new.staging"
    final = cores / "new"
    _core(old, core_id, "old")
    _core(staging, core_id, "new")
    registry = tmp_path / "app-data" / "active-core.json"
    initialize_active_core_pointer(
        registry,
        authentication_key=key,
        core_id=core_id,
        active_core_path=old,
    )

    def crash(name: str) -> None:
        if name == boundary:
            raise OSError("simulated activation crash")

    with pytest.raises(OSError, match="activation crash"):
        activate_staged_core(
            staging,
            final,
            registry,
            authentication_key=key,
            core_id=core_id,
            activation_id=activation_id,
            verifier=_core_verifier(core_id),
            boundary_hook=crash,
        )

    interrupted = read_active_core_pointer(registry, authentication_key=key)
    assert (interrupted.active_core_path == final.resolve()) is pointer_is_new
    assert old.is_dir()

    recovered = recover_active_core_activation(
        registry,
        authentication_key=key,
        verifier=_core_verifier(core_id),
    )
    if boundary == "activation:after_journal_cleanup":
        assert recovered is None
        recovered_pointer = read_active_core_pointer(registry, authentication_key=key)
    else:
        assert recovered is not None
        recovered_pointer = recovered.pointer
    assert recovered_pointer.generation == 2
    assert recovered_pointer.active_core_path == final.resolve()
    assert recovered_pointer.retained_core_path == old.resolve()
    assert not registry.with_name("active-core.json.activation").exists()


def test_activation_verification_and_symlink_fail_before_pointer_change(tmp_path: Path) -> None:
    core_id = str(uuid4())
    key = b"s" * 32
    cores = tmp_path / "cores"
    cores.mkdir()
    old = cores / "old"
    staging = cores / "new.staging"
    final = cores / "new"
    _core(old, core_id, "old")
    _core(staging, core_id, "new")
    registry = tmp_path / "app-data" / "active-core.json"
    original = initialize_active_core_pointer(
        registry,
        authentication_key=key,
        core_id=core_id,
        active_core_path=old,
    )

    with pytest.raises(TransferError, match="verification failed"):
        activate_staged_core(
            staging,
            final,
            registry,
            authentication_key=key,
            core_id=core_id,
            activation_id=str(uuid4()),
            verifier=lambda _path: (_ for _ in ()).throw(
                TransferError("restored Core verification failed")
            ),
        )
    assert read_active_core_pointer(registry, authentication_key=key) == original
    assert staging.is_dir()
    assert not final.exists()

    (staging / "escape").symlink_to(old, target_is_directory=True)
    with pytest.raises(TransferError, match="symbolic-link"):
        activate_staged_core(
            staging,
            final,
            registry,
            authentication_key=key,
            core_id=core_id,
            activation_id=str(uuid4()),
            verifier=_core_verifier(core_id),
        )
    assert read_active_core_pointer(registry, authentication_key=key) == original


@pytest.mark.parametrize("crash_after_pointer", [False, True])
def test_retained_old_core_rollback_is_atomic_and_idempotent(
    tmp_path: Path,
    crash_after_pointer: bool,
) -> None:
    core_id = str(uuid4())
    activation_id = str(uuid4())
    rollback_id = str(uuid4())
    key = b"b" * 32
    cores = tmp_path / "cores"
    cores.mkdir()
    old = cores / "old"
    staging = cores / "new.staging"
    final = cores / "new"
    _core(old, core_id, "old")
    _core(staging, core_id, "new")
    registry = tmp_path / "app-data" / "active-core.json"
    initialize_active_core_pointer(
        registry,
        authentication_key=key,
        core_id=core_id,
        active_core_path=old,
    )
    activate_staged_core(
        staging,
        final,
        registry,
        authentication_key=key,
        core_id=core_id,
        activation_id=activation_id,
        verifier=_core_verifier(core_id),
    )

    def crash(name: str) -> None:
        if crash_after_pointer and name == "rollback:after_pointer":
            raise OSError("simulated rollback crash")

    if crash_after_pointer:
        with pytest.raises(OSError, match="rollback crash"):
            rollback_to_retained_core(
                registry,
                authentication_key=key,
                rollback_id=rollback_id,
                verifier=_core_verifier(core_id),
                boundary_hook=crash,
            )

    rolled_back = rollback_to_retained_core(
        registry,
        authentication_key=key,
        rollback_id=rollback_id,
        verifier=_core_verifier(core_id),
    )
    assert rolled_back.pointer.generation == 3
    assert rolled_back.pointer.active_core_path == old.resolve()
    assert rolled_back.pointer.retained_core_path == final.resolve()
    assert rolled_back.pointer.retained_core_id == core_id
    assert old.is_dir() and final.is_dir()
