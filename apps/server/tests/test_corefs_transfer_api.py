from __future__ import annotations

import time
from collections.abc import Generator
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
from anima_server.services.corefs.recovery_access import (
    CoreFsRecoveryAccessError,
    CoreFsRecoveryBrowseResult,
    CoreFsRecoveryCredentialResult,
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


@pytest.fixture(autouse=True)
def _reset_recovery_browse_admission() -> Generator[None, None, None]:
    corefs_transfer._RECOVERY_BROWSE_ADMISSION.reset()
    yield
    corefs_transfer._RECOVERY_BROWSE_ADMISSION.reset()


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


def test_corefs_recovery_manager_keeps_credentials_ephemeral(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = managed_tmp_path / "corefs-only.anima"
    archive.write_bytes(b"authenticated-archive")
    staging_parent = managed_tmp_path / "restore"
    staging_parent.mkdir()
    staging = staging_parent / ".staged"
    staging.mkdir()
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
        core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
        recovery_state="recovery_only",
        staging_path=staging,
        staging_identity=(1, 2),
        control_records=(("manifest.json", 1, "a" * 64),),
        filesystem_generation=7,
    )
    captured: dict[str, object] = {}

    def browse(**kwargs: object) -> CoreFsRecoveryBrowseResult:
        captured.update(kwargs)
        return CoreFsRecoveryBrowseResult("stat", 7, "b" * 64, b"{}")

    monkeypatch.setattr(transfer_jobs, "browse_staged_corefs", browse)
    manager = CoreImportOperationManager()
    manager._operations[operation.operation_id] = operation

    result = manager.browse_corefs_recovery(
        operation.operation_id,
        user_id=7,
        credential="one-request-only",
        wrapping_path=transfer_jobs.WrappingPath.RECOVERY,
        browse_operation="stat",
        logical_path="Notes",
    )

    assert result.generation == 7
    assert captured["credential"] == "one-request-only"
    assert captured["staging_path"] == staging
    assert not hasattr(operation, "credential")


def test_corefs_recovery_manager_replaces_controls_without_retaining_credentials(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = managed_tmp_path / "corefs-only.anima"
    archive.write_bytes(b"authenticated-archive")
    staging_parent = managed_tmp_path / "restore"
    staging_parent.mkdir()
    staging = staging_parent / ".staged"
    staging.mkdir()
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
        core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
        recovery_state="recovery_only",
        staging_path=staging,
        staging_identity=(1, 2),
        control_records=(("manifest.json", 1, "a" * 64),),
        filesystem_generation=7,
    )
    captured: dict[str, object] = {}
    new_records = (("manifest.json", 2, "b" * 64),)

    def replace(**kwargs: object) -> CoreFsRecoveryCredentialResult:
        captured.update(kwargs)
        return CoreFsRecoveryCredentialResult(
            recovery_phrase="new phrase returned once",
            password_generation=8,
            recovery_generation=9,
            control_records=new_records,
        )

    monkeypatch.setattr(transfer_jobs, "replace_staged_corefs_credentials", replace)
    manager = CoreImportOperationManager()
    manager._operations[operation.operation_id] = operation

    completed = manager.replace_corefs_recovery_credentials(
        operation.operation_id,
        user_id=7,
        source_credential="one-request source",
        source_wrapping_path=transfer_jobs.WrappingPath.RECOVERY,
        new_password="new portable password",
    )

    assert completed.result.recovery_phrase == "new phrase returned once"
    assert captured["source_credential"] == "one-request source"
    assert captured["new_password"] == "new portable password"
    assert operation.control_records == new_records
    assert operation.credentials_replaced is True
    assert operation.phase == "staged_recovery_ready"
    assert operation.public()["credentialsReplaced"] is True
    assert not hasattr(operation, "source_credential")
    assert not hasattr(operation, "new_password")
    assert not hasattr(operation, "recovery_phrase")


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


def test_corefs_recovery_credential_api_returns_phrase_once_without_source_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Operation:
        def public(self) -> dict[str, object]:
            return {
                "operationId": "import-a",
                "state": "completed",
                "phase": "staged_recovery_ready",
                "archiveBytes": 7,
                "bytesProcessed": 7,
                "progressPercent": 100,
                "payloadKind": "fs",
                "recoveryState": "recovery_only",
                "archiveId": "archive-a",
                "activationId": None,
                "restartRequired": False,
                "credentialsReplaced": True,
                "errorCode": None,
            }

    class Manager:
        def replace_corefs_recovery_credentials(
            self, operation_id: str, **kwargs: object
        ) -> SimpleNamespace:
            captured["operation_id"] = operation_id
            captured.update(kwargs)
            return SimpleNamespace(
                operation=Operation(),
                result=CoreFsRecoveryCredentialResult(
                    recovery_phrase="new phrase returned once",
                    password_generation=8,
                    recovery_generation=9,
                    control_records=(),
                ),
            )

    monkeypatch.setattr(
        corefs_transfer,
        "require_unlocked_session",
        lambda _request: SimpleNamespace(user_id=7),
    )
    monkeypatch.setattr(corefs_transfer, "core_import_operations", Manager())

    with TestClient(_app()) as client:
        response = client.post(
            "/api/corefs/transfer/import/operations/import-a/replace-corefs-credentials",
            json={
                "sourceCredentialKind": "recovery",
                "sourceCredential": "  OLD RECOVERY PHRASE  ",
                "newPassword": "new portable password",
                "confirmed": True,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "scope": "fs",
        "recoveryPhrase": "new phrase returned once",
        "passwordGeneration": 8,
        "recoveryGeneration": 9,
        "operation": Operation().public(),
    }
    assert captured == {
        "operation_id": "import-a",
        "user_id": 7,
        "source_credential": "old recovery phrase",
        "source_wrapping_path": transfer_jobs.WrappingPath.RECOVERY,
        "new_password": "new portable password",
    }
    assert "old recovery phrase" not in response.text.casefold()
    assert "new portable password" not in response.text.casefold()


def test_corefs_recovery_credential_api_precharges_and_hides_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        def replace_corefs_recovery_credentials(self, *_args: object, **_kwargs: object) -> object:
            raise CoreFsRecoveryAccessError("private credential and staging details")

    monkeypatch.setattr(
        corefs_transfer,
        "require_unlocked_session",
        lambda _request: SimpleNamespace(user_id=7),
    )
    monkeypatch.setattr(corefs_transfer, "core_import_operations", Manager())
    payload = {
        "sourceCredentialKind": "password",
        "sourceCredential": "wrong source password",
        "newPassword": "new portable password",
        "confirmed": True,
    }

    with TestClient(_app()) as client:
        failures = [
            client.post(
                "/api/corefs/transfer/import/operations/import-a/replace-corefs-credentials",
                json=payload,
            )
            for _ in range(5)
        ]
        limited = client.post(
            "/api/corefs/transfer/import/operations/import-a/replace-corefs-credentials",
            json=payload,
        )

    assert all(response.status_code == 409 for response in failures)
    assert all(
        response.json() == {"detail": {"code": "corefs_recovery_credential_replacement_failed"}}
        for response in failures
    )
    assert limited.status_code == 429
    assert limited.json() == {"detail": {"code": "corefs_recovery_credential_rate_limited"}}
    assert "private credential" not in limited.text
    assert "wrong source password" not in limited.text
    assert "new portable password" not in limited.text


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
                "archiveId": None,
                "activationId": None,
                "restartRequired": False,
                "credentialsReplaced": False,
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
    assert "stagingPath" not in prepare.text


def test_corefs_recovery_browse_uses_one_request_credential_without_path_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Manager:
        def browse_corefs_recovery(self, operation_id: str, **kwargs: object):
            captured["operation_id"] = operation_id
            captured.update(kwargs)
            return CoreFsRecoveryBrowseResult(
                operation="list",
                generation=7,
                catalog_hash="a" * 64,
                payload=b'{"entries":[],"nextCursor":null}',
            )

    monkeypatch.setattr(
        corefs_transfer,
        "require_unlocked_session",
        lambda _request: SimpleNamespace(user_id=7),
    )
    monkeypatch.setattr(corefs_transfer, "core_import_operations", Manager())

    with TestClient(_app()) as client:
        response = client.post(
            "/api/corefs/transfer/import/operations/import-a/browse-corefs",
            json={
                "operation": "list",
                "credentialKind": "recovery",
                "credential": "recovery phrase that remains private",
                "path": "",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "operation": "list",
        "generation": 7,
        "catalogHash": "a" * 64,
        "result": {"entries": [], "nextCursor": None},
    }
    assert captured["operation_id"] == "import-a"
    assert captured["user_id"] == 7
    assert captured["credential"] == "recovery phrase that remains private"
    assert captured["wrapping_path"].value == "recovery"
    assert "credential" not in response.text.casefold()
    assert "path" not in response.text.casefold()


def test_corefs_recovery_browse_precharges_expensive_credential_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        def browse_corefs_recovery(self, *_args: object, **_kwargs: object):
            raise CoreFsRecoveryAccessError("private credential failure")

    monkeypatch.setattr(
        corefs_transfer,
        "require_unlocked_session",
        lambda _request: SimpleNamespace(user_id=7),
    )
    monkeypatch.setattr(corefs_transfer, "core_import_operations", Manager())
    payload = {
        "operation": "stat",
        "credentialKind": "password",
        "credential": "wrong credential",
        "path": "Notes",
    }

    with TestClient(_app()) as client:
        failures = [
            client.post(
                "/api/corefs/transfer/import/operations/import-a/browse-corefs",
                json=payload,
            )
            for _ in range(5)
        ]
        limited = client.post(
            "/api/corefs/transfer/import/operations/import-a/browse-corefs",
            json=payload,
        )

    assert all(response.status_code == 409 for response in failures)
    assert limited.status_code == 429
    assert limited.json() == {"detail": {"code": "corefs_recovery_browse_rate_limited"}}
    assert limited.headers["retry-after"]
    assert "private credential failure" not in limited.text


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
