from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.api.routes import corefs_transfer
from anima_server.services.corefs import transfer_jobs
from anima_server.services.corefs.archive_transfer import (
    CoreArchiveExportResult,
    CoreArchiveInventory,
    CoreArchivePayloadKind,
)
from anima_server.services.corefs.transfer import (
    DestinationProbe,
    PublicationMode,
    TransferEstimate,
)
from anima_server.services.corefs.transfer_jobs import (
    CoreTransferOperationManager,
    PreparedTransfer,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(corefs_transfer.router)
    return app


def _inventory() -> CoreArchiveInventory:
    return CoreArchiveInventory(
        payload_kind=CoreArchivePayloadKind.FULL,
        core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
        owner_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855be",
        soul_generation=3,
        filesystem_generation=7,
        selected_bytes=4096,
        record_count=5,
    )


def _estimate() -> TransferEstimate:
    return TransferEstimate(
        selected_bytes=4096,
        record_count=5,
        archive_bytes=8192,
        required_capacity_bytes=64 * 1024 * 1024 + 8192,
    )


def test_estimate_and_probe_expose_capacity_not_source_paths(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(user_id=7)
    destination = managed_tmp_path / "backup"
    destination.mkdir()
    prepared = PreparedTransfer(
        inventory=_inventory(),
        estimate=_estimate(),
        probe=DestinationProbe(
            destination=destination.resolve(),
            available_bytes=10**9,
            maximum_single_file_bytes=None,
            publication_mode=PublicationMode.SINGLE_FILE,
            part_limit_bytes=None,
            declared_volume_count=1,
        ),
    )

    class Manager:
        def inspect(self, **kwargs):
            return (
                prepared if kwargs.get("destination") is not None else (_inventory(), _estimate())
            )

    monkeypatch.setattr(corefs_transfer, "require_unlocked_session", lambda _request: session)
    monkeypatch.setattr(corefs_transfer, "core_transfer_operations", Manager())

    with TestClient(_app()) as client:
        estimate_response = client.post(
            "/api/corefs/transfer/estimate",
            json={"payloadKind": "full"},
        )
        probe_response = client.post(
            "/api/corefs/transfer/probe",
            json={"payloadKind": "full", "destination": str(destination)},
        )

    assert estimate_response.status_code == 200
    assert estimate_response.json()["selectedBytes"] == 4096
    assert probe_response.status_code == 200
    assert probe_response.json()["publicationMode"] == "single_file"
    assert "sourcePath" not in str(probe_response.json())


def test_transfer_conflict_returns_stable_code_without_internal_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        def inspect(self, **_kwargs):
            raise OSError("/private/path/that/must/not/leak")

    monkeypatch.setattr(
        corefs_transfer,
        "require_unlocked_session",
        lambda _request: SimpleNamespace(user_id=7),
    )
    monkeypatch.setattr(corefs_transfer, "core_transfer_operations", Manager())

    with TestClient(_app()) as client:
        response = client.post(
            "/api/corefs/transfer/probe",
            json={"payloadKind": "full", "destination": "/tmp"},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "core_transfer_precondition_failed"}}
    assert "private/path" not in response.text


def test_operation_manager_publishes_verified_archive_and_retains_no_passphrase(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = managed_tmp_path / "backup"
    destination.mkdir()
    inventory = _inventory()
    verified = False

    monkeypatch.setattr(transfer_jobs, "_coherent_soul_generation", lambda _kind: 3)
    monkeypatch.setattr(
        transfer_jobs,
        "inspect_core_archive_v2",
        lambda **_kwargs: inventory,
    )
    monkeypatch.setattr(transfer_jobs, "get_core_dir", lambda: managed_tmp_path / ".anima")

    def export(**kwargs) -> CoreArchiveExportResult:
        assert kwargs["passphrase"] == "correct horse battery staple"
        kwargs["output_path"].write_bytes(b"authenticated-archive")
        return CoreArchiveExportResult(
            inventory=inventory,
            archive_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bf",
            plaintext_bytes=inventory.selected_bytes,
            chunk_count=5,
            max_buffer_bytes=1024,
        )

    def verify(_path: Path, **kwargs) -> dict[str, object]:
        nonlocal verified
        assert kwargs["passphrase"] == "correct horse battery staple"
        assert kwargs["expected"] == inventory
        verified = True
        return {"version": 2}

    monkeypatch.setattr(transfer_jobs, "export_core_archive_v2", export)
    monkeypatch.setattr(transfer_jobs, "verify_core_archive_v2", verify)
    manager = CoreTransferOperationManager()

    operation = manager.start_export(
        user_id=7,
        session=SimpleNamespace(),
        destination=destination,
        final_name="ANIMA-CORE.anima-core",
        passphrase="correct horse battery staple",
        payload_kind=CoreArchivePayloadKind.FULL,
    )
    deadline = time.monotonic() + 2
    while operation.state.value not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.01)

    assert operation.state.value == "completed"
    assert verified is True
    assert (destination / "ANIMA-CORE.anima-core").read_bytes() == b"authenticated-archive"
    assert operation.public()["progressPercent"] == 100
    assert not hasattr(operation, "passphrase")
