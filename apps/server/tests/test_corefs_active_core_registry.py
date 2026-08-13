from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from anima_server.config import settings
from anima_server.services.corefs.active_core_registry import (
    initialize_active_core_after_manifest,
    resolve_active_core_for_startup,
    schedule_full_restore_activation,
    verify_full_core_candidate,
)
from anima_server.services.corefs.transfer import (
    TransferError,
    activate_staged_core,
)
from anima_server.services.credentials import CredentialStore, MemoryCredentialBackend


def _manifest_core(path: Path, core_id: str, *, complete: bool = False) -> None:
    path.mkdir()
    manifest = {
        "core_id": core_id,
        "owner_id": str(uuid4()),
    }
    if complete:
        manifest["archive_payload_scope"] = "full"
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if complete:
        (path / "soul").mkdir()
        (path / "soul" / "soul.db").write_bytes(b"encrypted-soul")
        (path / "fs" / "catalogs").mkdir(parents=True)
        (path / "fs" / "HEAD").write_bytes(b"authenticated-head")
        (path / "fs" / "catalogs" / "catalog.acore").write_bytes(b"encrypted-catalog")


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
