from __future__ import annotations

from types import SimpleNamespace

import pytest
from anima_server.services.corefs import gc


def binding(**changes):
    value = {
        "coreId": "core-a",
        "ownerId": "owner-a",
        "userId": 7,
        "sessionId": 123,
        "credentialGeneration": 2,
        "recoveryGeneration": 3,
        "action": "prune",
        "inventoryDigest": "a" * 64,
        "parameters": {"retentionDays": 30},
        "backupDigest": "b" * 64,
    }
    value.update(changes)
    return value


def test_confirmation_is_bound_to_session_inventory_and_one_use():
    store = gc.MaintenanceConfirmations(clock=lambda: 100.0)
    token = store.issue(binding())
    store.consume(token, binding())
    with pytest.raises(gc.MaintenanceError, match="confirmation"):
        store.consume(token, binding())


@pytest.mark.parametrize(
    "change",
    [
        {"sessionId": 124},
        {"coreId": "core-b"},
        {"ownerId": "owner-b"},
        {"credentialGeneration": 4},
        {"inventoryDigest": "c" * 64},
        {"parameters": {"retentionDays": 7}},
        {"backupDigest": "c" * 64},
    ],
)
def test_confirmation_rejects_changed_authority_or_inventory(change):
    store = gc.MaintenanceConfirmations(clock=lambda: 100.0)
    token = store.issue(binding())
    with pytest.raises(gc.MaintenanceError, match="confirmation"):
        store.consume(token, binding(**change))


def test_expired_confirmation_cannot_purge():
    now = [100.0]
    store = gc.MaintenanceConfirmations(clock=lambda: now[0])
    token = store.issue(binding(action="purge"))
    now[0] += 121
    with pytest.raises(gc.MaintenanceError, match="confirmation"):
        store.consume(token, binding(action="purge"))


def test_confirmation_does_not_alias_mutable_inventory():
    store = gc.MaintenanceConfirmations(clock=lambda: 100.0)
    original = binding()
    token = store.issue(original)
    original["parameters"]["retentionDays"] = 1
    with pytest.raises(gc.MaintenanceError, match="confirmation"):
        store.consume(token, original)


def test_native_blockers_never_authorize_deletion():
    with pytest.raises(gc.MaintenanceError, match="blocked"):
        gc.require_complete_inventory(
            {
                "version": 1,
                "headGeneration": 3,
                "headCatalogHash": "a" * 64,
                "inventoryDigest": "b" * 64,
                "requiredFrkVersions": [1],
                "blockers": ["catalog_authentication_failed"],
            }
        )


def test_incomplete_native_proof_never_authorizes_deletion():
    with pytest.raises(gc.MaintenanceError):
        gc.require_complete_inventory({"inventoryDigest": "a" * 64})


def test_current_trash_and_external_copies_are_distinguished_in_public_report():
    report = gc.public_inventory(
        {
            "version": 1,
            "headGeneration": 3,
            "headCatalogHash": "a" * 64,
            "inventoryDigest": "b" * 64,
            "requiredFrkVersions": [1],
            "blockers": [],
            "candidateBytes": 24,
            "candidatePreparationCount": 2,
            "privateNames": ["personal diary"],
            "physicalCandidates": ["object-secret.acore"],
        }
    )
    assert "privateNames" not in report
    assert "physicalCandidates" not in report
    assert report["retentionDays"] == 30
    assert report["trashExpiresAutomatically"] is False
    assert report["externalCopiesErased"] is False
    assert report["candidatePreparationCount"] == 2


def test_maintenance_requires_full_corefs_session():
    with pytest.raises(gc.MaintenanceError, match="unlock"):
        gc.require_maintenance_session(SimpleNamespace(corefs_session=None, corefs_keys=None))


def test_active_transfer_blocks_maintenance(monkeypatch):
    from anima_server.services.corefs import transfer_jobs
    from anima_server.services.corefs.transfer_jobs import TransferOperationState

    monkeypatch.setattr(
        transfer_jobs.core_transfer_operations,
        "_operations",
        {
            "job": SimpleNamespace(state=TransferOperationState.RUNNING),
        },
    )
    with pytest.raises(gc.MaintenanceError, match="transfer"):
        gc.require_idle_operations({"frk_rotation": {"phase": "idle"}})


def test_prune_does_not_invoke_native_without_verified_confirmation(monkeypatch):
    from anima_server.schemas.corefs_maintenance import MaintenanceExecuteRequest

    calls = []
    native = SimpleNamespace(maintenance_prune_v1=lambda *arguments: calls.append(arguments))
    session = SimpleNamespace(user_id=7, corefs_keys=object(), corefs_session=native)
    inventory = {
        "version": 1,
        "headGeneration": 3,
        "headCatalogHash": "a" * 64,
        "inventoryDigest": "b" * 64,
        "requiredFrkVersions": [1],
        "blockers": [],
    }
    manifest = {
        "core_id": "core-a",
        "owner_id": "owner-a",
        "active_password_credential_generation": 2,
        "active_recovery_credential_generation": 3,
    }
    monkeypatch.setattr(gc, "_reauthenticate", lambda *args, **kwargs: (manifest, []))
    monkeypatch.setattr(gc, "_native_inventory", lambda *args, **kwargs: inventory)
    monkeypatch.setattr(gc, "_verify_current_backup", lambda **kwargs: "c" * 64)
    payload = MaintenanceExecuteRequest(
        currentPassword="secret",
        recoveryPhrase="phrase",
        parameters={"action": "prune"},
        backupPath="C:/backup.anima",
        backupPassphrase="backup",
        confirmation="x" * 43,
        inventoryDigest="b" * 64,
        observedAtMs=1000,
    )
    with pytest.raises(gc.MaintenanceError, match="confirmation"):
        gc.CoreMaintenanceService().execute(session, payload)
    assert calls == []


def test_prepare_rejects_unverified_backup_before_issuing_confirmation(monkeypatch):
    from anima_server.schemas.corefs_maintenance import MaintenancePrepareRequest

    manifest = {"core_id": "core-a", "owner_id": "owner-a"}
    monkeypatch.setattr(gc, "_reauthenticate", lambda *args, **kwargs: (manifest, []))
    monkeypatch.setattr(
        gc, "_native_inventory", lambda *args, **kwargs: {"inventoryDigest": "b" * 64}
    )

    def reject_backup(**kwargs):
        raise gc.MaintenanceError("Backup verification failed")

    monkeypatch.setattr(gc, "_verify_current_backup", reject_backup)
    monkeypatch.setattr(
        gc.maintenance_confirmations, "issue", lambda *args: pytest.fail("issued without backup")
    )
    payload = MaintenancePrepareRequest(
        currentPassword="secret",
        recoveryPhrase="phrase",
        parameters={"action": "prune"},
        backupPath="C:/backup.anima",
        backupPassphrase="backup",
    )
    with pytest.raises(gc.MaintenanceError, match="Backup"):
        gc.CoreMaintenanceService().prepare(SimpleNamespace(), payload)


def test_native_purge_inventory_is_not_invented_from_user_fields(monkeypatch):
    calls = []
    inventory = {
        "version": 1,
        "headGeneration": 3,
        "headCatalogHash": "a" * 64,
        "inventoryDigest": "b" * 64,
        "requiredFrkVersions": [1],
        "blockers": [],
    }

    def native(keys, request_json, runtime_directory):
        calls.append(request_json)
        return {
            "inventory": inventory,
            "targetStableId": "01J00000000000000000000001",
            "expectedRevision": 2,
            "recursiveInventoryDigest": "c" * 64,
            "requiredWaivedGenerations": [1, 2],
            "blockers": [],
            "objectCount": 3,
        }

    monkeypatch.setattr(gc, "_maintenance_directory", lambda: "server-derived-runtime")
    result = gc._native_inventory(
        SimpleNamespace(corefs_session=SimpleNamespace(maintenance_purge_inventory_v1=native)),
        [],
        {"targetStableId": "01J00000000000000000000001", "expectedRevision": 2},
    )
    assert result["recursiveInventoryDigest"] == "c" * 64
    assert result["waivedGenerations"] == [1, 2]
    assert result["targetObjectCount"] == 3
    assert len(calls) == 1


def test_active_or_referenced_frk_never_retires():
    from anima_server.schemas.corefs_maintenance import MaintenanceParameters

    manifest = {"frk_rotation": {"active_version": 2, "decrypt_only_versions": [1]}}
    with pytest.raises(gc.MaintenanceError, match="decrypt-only"):
        gc.CoreMaintenanceService._require_retirement_proof(
            manifest,
            {"requiredFrkVersions": [1, 2]},
            MaintenanceParameters(action="retire-key", retiringFrkVersion=2),
        )
    with pytest.raises(gc.MaintenanceError, match="require this key"):
        gc.CoreMaintenanceService._require_retirement_proof(
            manifest,
            {"requiredFrkVersions": [1, 2]},
            MaintenanceParameters(action="retire-key", retiringFrkVersion=1),
        )


def test_admission_fence_blocks_new_transfer_jobs_during_maintenance():
    from threading import Event, Thread

    from anima_server.services.corefs.maintenance_coordination import (
        CORE_MAINTENANCE_ADMISSION,
        serialized_core_maintenance,
    )

    started, entered = Event(), Event()

    @serialized_core_maintenance
    def admit():
        entered.set()

    def worker():
        started.set()
        admit()

    with CORE_MAINTENANCE_ADMISSION:
        thread = Thread(target=worker)
        thread.start()
        assert started.wait(1)
        assert not entered.is_set()
    thread.join(timeout=1)
    assert entered.is_set()
    assert not thread.is_alive()


def test_pending_marker_string_alone_does_not_authorize_resume():
    inventory = {
        "version": 1,
        "headGeneration": 3,
        "headCatalogHash": "a" * 64,
        "inventoryDigest": "b" * 64,
        "requiredFrkVersions": [1],
        "blockers": ["maintenance_operation_pending"],
    }
    with pytest.raises(gc.MaintenanceError):
        gc.require_complete_inventory(inventory, continuation="resume")


def test_only_authenticated_journal_recovery_can_resume_pending_operation():
    inventory = {
        "version": 1,
        "headGeneration": 3,
        "headCatalogHash": "a" * 64,
        "inventoryDigest": "b" * 64,
        "requiredFrkVersions": [1],
        "blockers": ["maintenance_operation_pending", "head_lineage_corrupt"],
        "resumeAuthorized": True,
        "operation": {
            "kind": "reanchor",
            "sourceHeadGeneration": 3,
            "sourceHeadCatalogHash": "a" * 64,
            "expectedInventoryDigest": "b" * 64,
        },
    }
    assert gc.require_complete_inventory(inventory, continuation="resume") is inventory
    with pytest.raises(gc.MaintenanceError):
        gc.require_complete_inventory(inventory)
    with pytest.raises(gc.MaintenanceError):
        gc.require_complete_inventory(
            {**inventory, "blockers": ["maintenance_journal_missing"]}, continuation="resume"
        )


def test_key_retirement_reopens_full_soul_under_native_guard_and_rolls_back_failure(monkeypatch):
    from copy import deepcopy

    from anima_server.services import core
    from anima_server.services.corefs import keyslots, rotation
    from anima_server.services.corefs.types import KeyPurpose, KeyslotStatus

    original = {
        "core_id": "core-a",
        "owner_id": "owner-a",
        "keyslots": [{"version": 1}],
        "frk_rotation": {"active_version": 2, "decrypt_only_versions": [1]},
    }
    current = deepcopy(original)
    guard_entered = [False]
    observed = []

    class Guard:
        def __enter__(self):
            guard_entered[0] = True
            return self

        def __exit__(self, *_args):
            guard_entered[0] = False

    def update(callback):
        callback(current)
        observed.append(deepcopy(current))

    def fail_full_reopen(*_args, **_kwargs):
        assert guard_entered[0] is True
        assert current["frk_rotation"]["decrypt_only_versions"] == []
        raise gc.MaintenanceError("Soul recovery cannot reopen the full Core")

    retired_slot = SimpleNamespace(
        purpose=KeyPurpose.FILESYSTEM_ROOT, frk_version=1, status=KeyslotStatus.DECRYPT_ONLY
    )
    monkeypatch.setattr(keyslots, "_manifest_slots", lambda manifest: [retired_slot])
    monkeypatch.setattr(core, "update_core_manifest", update)
    monkeypatch.setattr(core, "get_core_dir", lambda: "explicit-current-Core")
    monkeypatch.setattr(rotation, "_manifest", lambda: current)
    monkeypatch.setattr(gc, "_verify_full_core_reopen", fail_full_reopen)
    monkeypatch.setattr(gc, "_maintenance_directory", lambda: "explicit-runtime")
    session = SimpleNamespace(
        user_id=7,
        deks={},
        corefs_keys=object(),
        corefs_session=SimpleNamespace(maintenance_lock_v1=lambda *_args: Guard()),
    )
    payload = SimpleNamespace(
        parameters=SimpleNamespace(retiringFrkVersion=1),
        observedAtMs=1000,
        currentPassword=SimpleNamespace(get_secret_value=lambda: "password"),
        recoveryPhrase=SimpleNamespace(get_secret_value=lambda: "recovery"),
    )
    with pytest.raises(gc.MaintenanceError, match="Soul recovery"):
        gc.CoreMaintenanceService._retire_key(
            session, original, [], {"inventoryDigest": "a" * 64}, payload
        )
    assert len(observed) == 2
    assert current == original
    assert guard_entered[0] is False
