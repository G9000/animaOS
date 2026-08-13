from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.api.routes import corefs_transfer
from anima_server.services.corefs import active_core_registry, transfer_jobs
from anima_server.services.corefs.archive_transfer import (
    CoreArchiveExportResult,
    CoreArchiveImportResult,
    CoreArchiveInventory,
    CoreArchivePayloadKind,
)
from anima_server.services.corefs.transfer import (
    DestinationProbe,
    ImportCapacityProbe,
    PublicationMode,
    TransferEstimate,
)
from anima_server.services.corefs.transfer_jobs import (
    CorefsReattachmentNotSupported,
    CoreImportOperationManager,
    CoreTransferOperationManager,
    ImportOperation,
    PreparedImport,
    PreparedTransfer,
    TransferOperationState,
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


def test_import_manager_stages_verified_core_without_activation_or_passphrase_retention(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = managed_tmp_path / ".anima"
    active.mkdir()
    archive = managed_tmp_path / "backup.anima"
    archive.write_bytes(b"authenticated-archive")
    staging_parent = managed_tmp_path / "restore"
    staging_parent.mkdir()
    inventory = _inventory()
    monkeypatch.setattr(transfer_jobs, "get_core_dir", lambda: active)

    def stage(path: Path, **kwargs) -> CoreArchiveImportResult:
        assert path == archive
        assert kwargs["passphrase"] == "correct horse battery staple"
        staging = kwargs["staging_path"]
        staging.mkdir()
        (staging / "manifest.json").write_bytes(b"verified")
        return CoreArchiveImportResult(
            inventory=inventory,
            archive_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bf",
            staging_path=staging,
            chunk_count=5,
            max_buffer_bytes=1024,
        )

    monkeypatch.setattr(transfer_jobs, "stage_core_archive_v2", stage)
    manager = CoreImportOperationManager()
    operation = manager.start_import(
        user_id=7,
        archive_path=archive,
        staging_parent=staging_parent,
        passphrase="correct horse battery staple",
    )
    deadline = time.monotonic() + 2
    while operation.state.value not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.01)

    assert operation.state.value == "completed"
    assert operation.phase == "staged_restart_required"
    assert operation.recovery_state == "complete"
    assert operation.staging_path is not None
    assert operation.staging_path.is_dir()
    assert not hasattr(operation, "passphrase")

    monkeypatch.setattr(
        active_core_registry,
        "schedule_full_restore_activation",
        lambda *_args, **_kwargs: SimpleNamespace(activation_id="activation-a"),
    )
    scheduled = manager.schedule_activation(operation.operation_id, user_id=7)
    assert scheduled.activation_id == "activation-a"
    assert scheduled.restart_required is True
    assert scheduled.phase == "activation_scheduled"


def test_corefs_only_recovery_cannot_attach_to_a_soul_in_v1(
    managed_tmp_path: Path,
) -> None:
    archive = managed_tmp_path / "corefs-only.anima"
    archive.write_bytes(b"authenticated-archive")
    staging_parent = managed_tmp_path / "restore"
    staging_parent.mkdir()
    operation = ImportOperation(
        operation_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bf",
        user_id=7,
        prepared=PreparedImport(
            archive_path=archive,
            archive_bytes=archive.stat().st_size,
            probe=ImportCapacityProbe(
                staging_parent=staging_parent,
                restored_core_bytes=archive.stat().st_size,
                available_bytes=10**9,
                required_capacity_bytes=64 * 1024 * 1024 + archive.stat().st_size,
            ),
        ),
        state=TransferOperationState.COMPLETED,
        phase="staged_credential_required",
        payload_kind=CoreArchivePayloadKind.FS,
        recovery_state="recovery_only",
    )
    manager = CoreImportOperationManager()
    manager._operations[operation.operation_id] = operation

    with pytest.raises(
        CorefsReattachmentNotSupported,
        match="not supported in V1",
    ):
        manager.request_corefs_reattachment(operation.operation_id, user_id=7)


def test_corefs_reattachment_api_returns_the_stable_v1_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        def request_corefs_reattachment(self, *_args, **_kwargs):
            raise CorefsReattachmentNotSupported("private internal details")

    monkeypatch.setattr(
        corefs_transfer,
        "require_unlocked_session",
        lambda _request: SimpleNamespace(user_id=7),
    )
    monkeypatch.setattr(corefs_transfer, "core_import_operations", Manager())

    with TestClient(_app()) as client:
        response = client.post("/api/corefs/transfer/import/operations/import-a/attach-corefs")

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "corefs_reattachment_not_supported"}}
    assert "private internal" not in response.text


def test_import_api_probes_and_stages_without_exposing_passphrase(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(user_id=7)
    archive = managed_tmp_path / "backup.anima"
    archive.write_bytes(b"archive")
    staging_parent = managed_tmp_path / "restore"
    staging_parent.mkdir()
    prepared = PreparedImport(
        archive_path=archive,
        archive_bytes=7,
        probe=ImportCapacityProbe(
            staging_parent=staging_parent,
            restored_core_bytes=7,
            available_bytes=10**9,
            required_capacity_bytes=64 * 1024 * 1024 + 7,
        ),
    )

    class Operation:
        def public(self) -> dict[str, object]:
            return {
                "operationId": "018f0f4e-4ee4-7aa5-8eb2-1eb7699855bf",
                "state": "prepared",
                "phase": "prepared",
                "archiveBytes": 7,
                "bytesProcessed": 0,
                "progressPercent": 0,
                "payloadKind": None,
                "recoveryState": None,
                "stagingPath": None,
                "archiveId": None,
                "activationId": None,
                "restartRequired": False,
                "errorCode": None,
            }

    class Manager:
        def inspect(self, **_kwargs) -> PreparedImport:
            return prepared

        def start_import(self, **kwargs) -> Operation:
            assert kwargs["passphrase"] == "correct horse battery staple"
            return Operation()

    monkeypatch.setattr(corefs_transfer, "require_unlocked_session", lambda _request: session)
    monkeypatch.setattr(corefs_transfer, "core_import_operations", Manager())

    with TestClient(_app()) as client:
        probe = client.post(
            "/api/corefs/transfer/import/probe",
            json={"archivePath": str(archive), "stagingParent": str(staging_parent)},
        )
        prepare = client.post(
            "/api/corefs/transfer/import/prepare",
            json={
                "archivePath": str(archive),
                "stagingParent": str(staging_parent),
                "passphrase": "correct horse battery staple",
            },
        )

    assert probe.status_code == 200
    assert probe.json()["requiredCapacityBytes"] == 64 * 1024 * 1024 + 7
    assert prepare.status_code == 202
    assert "passphrase" not in prepare.text.casefold()


def test_active_core_status_and_confirmed_restart_rollback_expose_no_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(user_id=7)
    scheduled = False

    def status_value():
        return SimpleNamespace(
            generation=3,
            active_core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
            retained_core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855be",
            activation_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bf",
            rollback_scheduled=scheduled,
        )

    def schedule() -> None:
        nonlocal scheduled
        scheduled = True

    monkeypatch.setattr(corefs_transfer, "require_unlocked_session", lambda _request: session)
    monkeypatch.setattr(corefs_transfer, "read_active_core_status", status_value)
    monkeypatch.setattr(corefs_transfer, "schedule_active_core_rollback", schedule)

    with TestClient(_app()) as client:
        status_response = client.get("/api/corefs/transfer/active-core")
        missing_confirmation = client.post(
            "/api/corefs/transfer/active-core/rollback-on-restart",
            json={"confirmed": False},
        )
        rollback_response = client.post(
            "/api/corefs/transfer/active-core/rollback-on-restart",
            json={"confirmed": True},
        )

    assert status_response.status_code == 200
    assert status_response.json()["rollbackScheduled"] is False
    assert missing_confirmation.status_code == 422
    assert rollback_response.status_code == 200
    assert rollback_response.json()["rollbackScheduled"] is True
    assert "path" not in rollback_response.text.casefold()


def test_import_manager_rechecks_exact_inputs_before_extraction(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = managed_tmp_path / ".anima"
    active.mkdir()
    archive = managed_tmp_path / "backup.anima"
    archive.write_bytes(b"authenticated-archive")
    staging_parent = managed_tmp_path / "restore"
    staging_parent.mkdir()
    monkeypatch.setattr(transfer_jobs, "get_core_dir", lambda: active)
    real_probe = transfer_jobs.probe_import_staging
    calls = 0

    def probe(*args, **kwargs) -> ImportCapacityProbe:
        nonlocal calls
        calls += 1
        result = real_probe(*args, **kwargs)
        if calls == 1:
            archive.write_bytes(b"changed-after-probe")
        return result

    stage_called = False

    def stage(*_args, **_kwargs):
        nonlocal stage_called
        stage_called = True
        raise AssertionError("changed archive must fail before extraction")

    monkeypatch.setattr(transfer_jobs, "probe_import_staging", probe)
    monkeypatch.setattr(transfer_jobs, "stage_core_archive_v2", stage)
    manager = CoreImportOperationManager()
    operation = manager.start_import(
        user_id=7,
        archive_path=archive,
        staging_parent=staging_parent,
        passphrase="correct horse battery staple",
    )
    deadline = time.monotonic() + 2
    while operation.state.value not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.01)

    assert operation.state.value == "failed"
    assert stage_called is False
