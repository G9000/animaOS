from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from anima_server.services.corefs import transfer
from anima_server.services.corefs.transfer import (
    ARCHIVE_FRAME_RESERVE_BYTES,
    CAPACITY_MARGIN_BYTES,
    FAT32_MAX_FILE_BYTES,
    PublicationMode,
    TransferCancelled,
    TransferError,
    estimate_transfer,
    probe_local_destination,
    publish_multipart,
    publish_single_file,
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
