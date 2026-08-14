from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from anima_server.config import settings
from anima_server.services.core import ensure_core_manifest, get_core_id
from anima_server.services.corefs.active_core_registry import (
    consume_scheduled_account_deletion,
    initialize_active_core_after_manifest,
    read_active_core_status,
    resolve_active_core_for_startup,
    schedule_active_core_account_deletion,
    schedule_active_core_rollback,
    schedule_full_restore_activation,
    verify_full_core_candidate,
)
from anima_server.services.corefs.instance_registry import (
    RuntimeInstanceBinding,
    RuntimeInstanceRegistry,
)
from anima_server.services.corefs.transfer import (
    TransferError,
    activate_staged_core,
    read_active_core_pointer,
)
from anima_server.services.credentials import CredentialStore, MemoryCredentialBackend


def _manifest_core(
    path: Path,
    core_id: str,
    *,
    complete: bool = False,
    owner_user_id: int = 1,
) -> None:
    path.mkdir()
    manifest = {
        "core_id": core_id,
        "owner_id": str(uuid4()),
        "owner_user_id": owner_user_id,
    }
    if complete:
        manifest["archive_payload_scope"] = "full"
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "soul").mkdir()
    (path / "soul" / "soul.db").write_bytes(b"encrypted-soul")
    (path / "fs" / "catalogs").mkdir(parents=True)
    (path / "fs" / "HEAD").write_bytes(b"authenticated-head")
    (path / "fs" / "catalogs" / "catalog.acore").write_bytes(b"encrypted-catalog")


def _claim_runtime(
    core: Path,
    app_data: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RuntimeInstanceRegistry, RuntimeInstanceBinding]:
    registry = RuntimeInstanceRegistry(app_data)
    binding = registry.resolve(core)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(app_data))
    monkeypatch.setattr(settings, "runtime_instance_data_dir", str(binding.instance_root))
    return registry, binding


def test_whole_core_account_deletion_is_restart_only_and_recreates_no_old_data(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    core_id = str(uuid4())
    _manifest_core(configured, core_id, owner_user_id=7)
    (configured / "private-marker").write_text("must disappear", encoding="utf-8")
    app_data = managed_tmp_path / "app-data"
    runtime_registry, runtime_binding = _claim_runtime(configured, app_data, monkeypatch)
    runtime = runtime_binding.instance_root
    (runtime / "sealed-runtime").write_bytes(b"ciphertext")
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)

    startup = resolve_active_core_for_startup(store=store)
    initialize_active_core_after_manifest(startup)
    scheduled = schedule_active_core_account_deletion(user_id=7, store=store)

    assert scheduled.restart_required is True
    assert configured.is_dir()
    assert runtime.is_dir()
    assert scheduled.request_path.is_file()

    with pytest.raises(TransferError, match="not stopped"):
        consume_scheduled_account_deletion(
            startup.registry_path,
            authentication_key=startup.authentication_key,
            store=store,
        )
    assert configured.is_dir()
    assert runtime.is_dir()

    runtime_registry.release(runtime_binding)
    monkeypatch.setattr(settings, "runtime_instance_data_dir", "")
    resumed = resolve_active_core_for_startup(store=store)

    assert resumed.pointer is None
    assert settings.data_dir == configured
    assert not configured.exists()
    assert not runtime.exists()
    assert not scheduled.request_path.exists()
    assert not startup.registry_path.exists()
    instance_registry = json.loads(
        (app_data / "core-instance-registry.json").read_text(encoding="utf-8")
    )
    assert instance_registry["instances"] == []

    ensure_core_manifest()
    replacement_pointer = initialize_active_core_after_manifest(resumed)
    assert replacement_pointer.core_id == get_core_id()
    assert replacement_pointer.core_id != core_id
    assert not (configured / "private-marker").exists()


def test_whole_core_account_deletion_removes_retained_rollback_core(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    configured_id = str(uuid4())
    _manifest_core(configured, configured_id, owner_user_id=7)
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    monkeypatch.setattr(settings, "runtime_instance_data_dir", "")
    startup = resolve_active_core_for_startup(store=store)
    initialize_active_core_after_manifest(startup)

    restored = managed_tmp_path / ".restore.partial"
    restored_id = str(uuid4())
    _manifest_core(restored, restored_id, complete=True, owner_user_id=7)
    activated = activate_staged_core(
        restored,
        managed_tmp_path / ".anima-restored",
        startup.registry_path,
        authentication_key=startup.authentication_key,
        core_id=restored_id,
        activation_id=str(uuid4()),
        verifier=verify_full_core_candidate,
    )
    monkeypatch.setattr(settings, "data_dir", activated.pointer.active_core_path)
    runtime_registry, runtime_binding = _claim_runtime(
        activated.pointer.active_core_path,
        managed_tmp_path / "app-data",
        monkeypatch,
    )
    schedule_active_core_account_deletion(user_id=7, store=store)
    runtime_registry.release(runtime_binding)

    monkeypatch.setattr(settings, "data_dir", configured)
    resolve_active_core_for_startup(store=store)

    assert not configured.exists()
    assert not activated.pointer.active_core_path.exists()


@pytest.mark.parametrize(
    "crash_boundary",
    [
        "account-delete:after-journal",
        "account-delete:after-active-quarantine",
        "account-delete:after-retained-quarantine",
        "account-delete:after-runtime-quarantine",
        "account-delete:after-registry-removal",
        "account-delete:after-data-removal",
        "account-delete:after-request-removal",
    ],
)
def test_whole_core_account_deletion_resumes_at_every_crash_boundary(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_boundary: str,
) -> None:
    configured = managed_tmp_path / ".anima"
    _manifest_core(configured, str(uuid4()), owner_user_id=7)
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    runtime_registry, runtime_binding = _claim_runtime(
        configured,
        managed_tmp_path / "app-data",
        monkeypatch,
    )
    startup = resolve_active_core_for_startup(store=store)
    initialize_active_core_after_manifest(startup)
    scheduled = schedule_active_core_account_deletion(user_id=7, store=store)
    runtime_registry.release(runtime_binding)

    def crash(boundary: str) -> None:
        if boundary == crash_boundary:
            raise OSError("simulated restart")

    with pytest.raises(OSError, match="simulated restart"):
        consume_scheduled_account_deletion(
            startup.registry_path,
            authentication_key=startup.authentication_key,
            store=store,
            boundary_hook=crash,
        )

    if crash_boundary == "account-delete:after-request-removal":
        assert not scheduled.request_path.exists()
    else:
        assert scheduled.request_path.is_file()
    assert startup.registry_path.with_name("active-core.json.delete-journal").is_file()

    resolve_active_core_for_startup(store=store)
    assert not configured.exists()
    assert not runtime_binding.instance_root.exists()
    assert not scheduled.request_path.exists()
    assert not startup.registry_path.with_name("active-core.json.delete-journal").exists()
    instance_registry = json.loads(
        (
            managed_tmp_path / "app-data" / "core-instance-registry.json"
        ).read_text(encoding="utf-8")
    )
    assert instance_registry["instances"] == []


def test_tampered_whole_core_account_deletion_request_preserves_core(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    _manifest_core(configured, str(uuid4()), owner_user_id=7)
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    _claim_runtime(configured, managed_tmp_path / "app-data", monkeypatch)
    startup = resolve_active_core_for_startup(store=store)
    initialize_active_core_after_manifest(startup)
    scheduled = schedule_active_core_account_deletion(user_id=7, store=store)
    payload = json.loads(scheduled.request_path.read_text(encoding="utf-8"))
    payload["userId"] = 8
    scheduled.request_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TransferError, match="tag is invalid"):
        resolve_active_core_for_startup(store=store)

    assert configured.is_dir()
    assert startup.registry_path.is_file()


def test_startup_registry_selects_activated_full_core_and_retains_old_core(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    configured_id = str(uuid4())
    _manifest_core(configured, configured_id)
    app_data = managed_tmp_path / "app-data"
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(app_data))

    startup = resolve_active_core_for_startup(store=store)
    assert startup.pointer is None
    pointer = initialize_active_core_after_manifest(startup)
    assert pointer.active_core_path == configured
    assert pointer.core_id == configured_id
    assert pointer.retained_core_path is None
    assert startup.registry_path.is_file()
    assert startup.authentication_key.hex() not in startup.registry_path.read_text(encoding="utf-8")

    restored_id = str(uuid4())
    staging = managed_tmp_path / ".restore.partial"
    final = managed_tmp_path / ".anima-restored"
    _manifest_core(staging, restored_id, complete=True)
    activated = activate_staged_core(
        staging,
        final,
        startup.registry_path,
        authentication_key=startup.authentication_key,
        core_id=restored_id,
        activation_id=str(uuid4()),
        verifier=verify_full_core_candidate,
    )
    assert activated.pointer.active_core_path == final
    assert activated.pointer.retained_core_path == configured

    monkeypatch.setattr(settings, "data_dir", configured)
    resumed = resolve_active_core_for_startup(store=store)
    assert resumed.pointer is not None
    assert settings.data_dir == final
    assert resumed.pointer.retained_core_path == configured


def test_startup_registry_tampering_fails_closed(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    _manifest_core(configured, str(uuid4()))
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    startup = resolve_active_core_for_startup(store=store)
    initialize_active_core_after_manifest(startup)
    record = json.loads(startup.registry_path.read_text(encoding="utf-8"))
    record["generation"] += 1
    startup.registry_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(TransferError):
        resolve_active_core_for_startup(store=store)


def test_startup_recovers_interrupted_full_core_activation(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    _manifest_core(configured, str(uuid4()))
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    startup = resolve_active_core_for_startup(store=store)
    initialize_active_core_after_manifest(startup)
    restored_id = str(uuid4())
    staging = managed_tmp_path / ".restore.partial"
    final = managed_tmp_path / ".anima-restored"
    _manifest_core(staging, restored_id, complete=True)

    def crash_after_rename(boundary: str) -> None:
        if boundary == "activation:after_directory_rename":
            raise OSError("simulated restart")

    with pytest.raises(OSError, match="simulated restart"):
        activate_staged_core(
            staging,
            final,
            startup.registry_path,
            authentication_key=startup.authentication_key,
            core_id=restored_id,
            activation_id=str(uuid4()),
            verifier=verify_full_core_candidate,
            boundary_hook=crash_after_rename,
        )

    assert final.is_dir()
    assert startup.registry_path.with_name("active-core.json.activation").is_file()
    recovered = resolve_active_core_for_startup(store=store)
    assert recovered.pointer is not None
    assert recovered.pointer.active_core_path == final
    assert settings.data_dir == final
    assert not startup.registry_path.with_name("active-core.json.activation").exists()


def test_scheduled_activation_does_not_swap_running_core_until_restart(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    _manifest_core(configured, str(uuid4()))
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    startup = resolve_active_core_for_startup(store=store)
    initialize_active_core_after_manifest(startup)
    restored_id = str(uuid4())
    staging = managed_tmp_path / ".restore.partial"
    _manifest_core(staging, restored_id, complete=True)

    scheduled = schedule_full_restore_activation(
        staging,
        core_id=restored_id,
        store=store,
    )
    assert settings.data_dir == configured
    assert scheduled.request_path.is_file()
    assert staging.is_dir()
    assert not scheduled.final_core_path.exists()

    resumed = resolve_active_core_for_startup(store=store)
    assert resumed.pointer is not None
    assert resumed.pointer.active_core_path == scheduled.final_core_path
    assert resumed.pointer.retained_core_path == configured
    assert settings.data_dir == scheduled.final_core_path
    assert not scheduled.request_path.exists()


def test_retained_core_rollback_is_scheduled_without_live_swap_and_consumed_on_restart(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    configured_id = str(uuid4())
    _manifest_core(configured, configured_id)
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    startup = resolve_active_core_for_startup(store=store)
    initialize_active_core_after_manifest(startup)

    restored_id = str(uuid4())
    staging = managed_tmp_path / ".restore.partial"
    _manifest_core(staging, restored_id, complete=True)
    scheduled_activation = schedule_full_restore_activation(
        staging,
        core_id=restored_id,
        store=store,
    )
    activated = resolve_active_core_for_startup(store=store)
    assert activated.pointer is not None
    assert settings.data_dir == scheduled_activation.final_core_path

    scheduled_rollback = schedule_active_core_rollback(store=store)
    status = read_active_core_status(store=store)
    assert status.active_core_id == restored_id
    assert status.retained_core_id == configured_id
    assert status.rollback_scheduled is True
    assert settings.data_dir == scheduled_activation.final_core_path
    assert scheduled_rollback.request_path.is_file()

    monkeypatch.setattr(settings, "data_dir", configured)
    resumed = resolve_active_core_for_startup(store=store)
    assert resumed.pointer is not None
    assert resumed.pointer.active_core_path == configured.resolve()
    assert resumed.pointer.retained_core_path == scheduled_activation.final_core_path
    assert settings.data_dir == configured.resolve()
    assert not scheduled_rollback.request_path.exists()


def test_scheduled_rollback_rejects_incomplete_retained_core_before_pointer_change(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    _manifest_core(configured, str(uuid4()))
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    startup = resolve_active_core_for_startup(store=store)
    initialize_active_core_after_manifest(startup)

    restored_id = str(uuid4())
    staging = managed_tmp_path / ".restore.partial"
    _manifest_core(staging, restored_id, complete=True)
    scheduled_activation = schedule_full_restore_activation(
        staging,
        core_id=restored_id,
        store=store,
    )
    resolve_active_core_for_startup(store=store)
    scheduled_rollback = schedule_active_core_rollback(store=store)
    (configured / "soul" / "soul.db").unlink()

    monkeypatch.setattr(settings, "data_dir", configured)
    with pytest.raises(TransferError, match="registry Core candidate is incomplete"):
        resolve_active_core_for_startup(store=store)

    pointer = read_active_core_pointer(
        startup.registry_path,
        authentication_key=startup.authentication_key,
    )
    assert pointer.active_core_path == scheduled_activation.final_core_path
    assert scheduled_rollback.request_path.is_file()


@pytest.mark.parametrize("degraded_state", ["filesystem_missing", "recovery_only"])
def test_partial_restore_cannot_activate_as_full_core(
    managed_tmp_path: Path,
    degraded_state: str,
) -> None:
    candidate = managed_tmp_path / "partial"
    _manifest_core(candidate, str(uuid4()), complete=True)
    manifest_path = candidate / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_payload_scope"] = "soul" if degraded_state == "filesystem_missing" else "fs"
    manifest["degraded_state"] = degraded_state
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TransferError, match="only a complete full archive"):
        verify_full_core_candidate(candidate)


def test_registry_credential_shape_is_fail_closed(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = managed_tmp_path / ".anima"
    _manifest_core(configured, str(uuid4()))
    backend = MemoryCredentialBackend()
    store = CredentialStore(backend)
    monkeypatch.setattr(settings, "data_dir", configured)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    resolve_active_core_for_startup(store=store)
    reference = next(iter(backend.values))
    backend.values[reference] = "not-canonical-base64"

    with pytest.raises(TransferError, match="credential is invalid"):
        resolve_active_core_for_startup(store=store)
