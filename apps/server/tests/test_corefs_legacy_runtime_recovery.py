from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from anima_server.config import settings
from anima_server.services.corefs import legacy_runtime_recovery
from anima_server.services.corefs.cutover import CutoverState
from anima_server.services.corefs.instance_registry import (
    RuntimeInstanceBinding,
    RuntimeInstanceRegistry,
)
from anima_server.services.corefs.legacy_runtime_recovery import (
    LegacyRuntimeRecoveryError,
    finalize_runtime_transition_after_startup,
    prepare_current_legacy_runtime_recovery_bundle,
    prepare_legacy_runtime_recovery_bundle,
    require_first_write_runtime_recovery,
    retire_legacy_runtime_plaintext,
    runtime_transition_restart_required,
    select_runtime_pg_data_dir_for_startup,
    verify_legacy_runtime_recovery_bundle,
)
from anima_server.services.credentials import CredentialStore, MemoryCredentialBackend


def _fixture(managed_tmp_path: Path) -> tuple[Path, RuntimeInstanceBinding, str]:
    core = managed_tmp_path / "portable" / ".anima"
    core.mkdir(parents=True)
    (core / "manifest.json").write_text(
        json.dumps({"core_id": str(uuid4())}),
        encoding="utf-8",
    )
    binding = RuntimeInstanceRegistry(managed_tmp_path / "app-data").resolve(core)
    source = binding.legacy_pg_data_dir
    (source / "base").mkdir(parents=True)
    (source / "PG_VERSION").write_text("17", encoding="ascii")
    private_marker = "seeded-legacy-runtime-private-message"
    (source / "base" / "12345").write_bytes(
        private_marker.encode("utf-8") + (b"x" * (1024 * 1024 + 97))
    )
    return core, binding, private_marker


def test_recovery_bundle_is_encrypted_verified_and_outside_portable_core(
    managed_tmp_path: Path,
) -> None:
    core, binding, private_marker = _fixture(managed_tmp_path)
    backend = MemoryCredentialBackend()
    store = CredentialStore(backend)

    bundle = prepare_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )
    verified = verify_legacy_runtime_recovery_bundle(binding, store=store)

    assert bundle == verified
    assert bundle.file_count == 2
    assert bundle.plaintext_bytes > 1024 * 1024
    assert bundle.path.is_relative_to(binding.instance_root)
    assert not bundle.path.is_relative_to(core)
    assert binding.legacy_pg_data_dir.is_dir()
    encoded = bundle.path.read_bytes()
    assert private_marker.encode("utf-8") not in encoded
    for encoded_key in backend.values.values():
        assert encoded_key.encode("ascii") not in encoded


def test_recovery_bundle_tampering_fails_closed_without_touching_source(
    managed_tmp_path: Path,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())
    bundle = prepare_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )
    encoded = bytearray(bundle.path.read_bytes())
    encoded[len(encoded) // 2] ^= 0x80
    bundle.path.write_bytes(encoded)

    with pytest.raises(LegacyRuntimeRecoveryError):
        verify_legacy_runtime_recovery_bundle(binding, store=store)

    assert (binding.legacy_pg_data_dir / "PG_VERSION").read_text(encoding="ascii") == "17"


def test_source_change_after_inventory_removes_unpublished_partial(
    managed_tmp_path: Path,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())

    def mutate_after_manifest(boundary: str) -> None:
        if boundary == "legacy-runtime-recovery:after_manifest":
            (binding.legacy_pg_data_dir / "PG_VERSION").write_text("16", encoding="ascii")

    with pytest.raises(LegacyRuntimeRecoveryError, match="source changed"):
        prepare_legacy_runtime_recovery_bundle(
            binding,
            legacy_postgres_running=False,
            store=store,
            boundary_hook=mutate_after_manifest,
        )

    recovery_dir = binding.instance_root / "recovery"
    assert not (recovery_dir / "legacy-runtime-source.anima-runtime-recovery").exists()
    assert list(recovery_dir.glob("*.partial")) == []
    assert (binding.legacy_pg_data_dir / "PG_VERSION").read_text(encoding="ascii") == "16"


def test_live_postmaster_pid_fails_closed_even_if_caller_reports_stopped(
    managed_tmp_path: Path,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    (binding.legacy_pg_data_dir / "postmaster.pid").write_text(
        f"{os.getpid()}\n",
        encoding="ascii",
    )

    with pytest.raises(LegacyRuntimeRecoveryError, match="PostgreSQL is stopped"):
        prepare_legacy_runtime_recovery_bundle(
            binding,
            legacy_postgres_running=False,
            store=CredentialStore(MemoryCredentialBackend()),
        )

    assert not (binding.instance_root / "recovery").exists()


def test_existing_bundle_never_gets_overwritten_for_changed_source(
    managed_tmp_path: Path,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())
    bundle = prepare_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )
    original = bundle.path.read_bytes()
    (binding.legacy_pg_data_dir / "PG_VERSION").write_text("16", encoding="ascii")

    with pytest.raises(LegacyRuntimeRecoveryError, match="does not match the source"):
        prepare_legacy_runtime_recovery_bundle(
            binding,
            legacy_postgres_running=False,
            store=store,
        )

    assert bundle.path.read_bytes() == original


def test_pending_restart_refreshes_authenticated_bundle_after_stopped_source_changes(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(
        legacy_runtime_recovery,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE),
    )
    select_runtime_pg_data_dir_for_startup(binding, store=store)
    original = verify_legacy_runtime_recovery_bundle(binding, store=store)
    (binding.legacy_pg_data_dir / "PG_VERSION").write_text("16", encoding="ascii")

    assert select_runtime_pg_data_dir_for_startup(binding, store=store) == (
        binding.legacy_pg_data_dir
    )
    refreshed = verify_legacy_runtime_recovery_bundle(binding, store=store)

    assert refreshed.bundle_id != original.bundle_id
    assert not refreshed.path.with_name(f".{refreshed.path.name}.previous").exists()


@pytest.mark.parametrize(
    "crash_boundary",
    [
        "legacy-runtime-recovery:after_stale_rotation",
        "legacy-runtime-recovery:after_refresh_publish",
    ],
)
def test_pending_bundle_refresh_resumes_after_every_replacement_boundary(
    managed_tmp_path: Path,
    crash_boundary: str,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())
    original = prepare_current_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )
    (binding.legacy_pg_data_dir / "PG_VERSION").write_text("16", encoding="ascii")

    def crash_during_refresh(boundary: str) -> None:
        if boundary == crash_boundary:
            raise RuntimeError("crash")

    with pytest.raises(RuntimeError, match="crash"):
        prepare_current_legacy_runtime_recovery_bundle(
            binding,
            legacy_postgres_running=False,
            store=store,
            boundary_hook=crash_during_refresh,
        )

    previous = original.path.with_name(f".{original.path.name}.previous")
    assert previous.is_file()
    if crash_boundary.endswith("stale_rotation"):
        assert not original.path.exists()
    else:
        assert original.path.is_file()
    resumed = prepare_current_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )
    assert resumed.path.is_file()
    assert resumed.bundle_id != original.bundle_id
    assert not previous.exists()


def test_durable_partial_resumes_create_only_with_same_authenticated_bundle(
    managed_tmp_path: Path,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())
    bundle = prepare_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )
    partial = bundle.path.with_name(f".{bundle.path.name}.partial")
    bundle.path.replace(partial)

    resumed = prepare_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )

    assert resumed.bundle_id == bundle.bundle_id
    assert resumed.path == bundle.path
    assert resumed.path.is_file()
    assert not partial.exists()


def test_existing_bundle_never_generates_a_replacement_for_missing_credential(
    managed_tmp_path: Path,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    backend = MemoryCredentialBackend()
    store = CredentialStore(backend)
    bundle = prepare_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )
    original = bundle.path.read_bytes()
    backend.values.clear()

    with pytest.raises(LegacyRuntimeRecoveryError, match="credential is unavailable"):
        prepare_legacy_runtime_recovery_bundle(
            binding,
            legacy_postgres_running=False,
            store=store,
        )

    assert bundle.path.read_bytes() == original


def test_plaintext_retirement_requires_marker_stopped_source_and_fresh_runtime(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())
    bundle = prepare_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )
    monkeypatch.setattr(
        legacy_runtime_recovery,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.LEGACY_AUTHORITATIVE),
    )

    with pytest.raises(LegacyRuntimeRecoveryError, match="forward-only"):
        retire_legacy_runtime_plaintext(
            binding,
            legacy_postgres_running=False,
            store=store,
        )
    assert binding.legacy_pg_data_dir.is_dir()

    monkeypatch.setattr(
        legacy_runtime_recovery,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY),
    )
    with pytest.raises(LegacyRuntimeRecoveryError, match="PostgreSQL is stopped"):
        retire_legacy_runtime_plaintext(
            binding,
            legacy_postgres_running=True,
            store=store,
        )
    with pytest.raises(LegacyRuntimeRecoveryError, match="fresh Runtime"):
        retire_legacy_runtime_plaintext(
            binding,
            legacy_postgres_running=False,
            store=store,
        )

    binding.pg_data_dir.mkdir(parents=True)
    (binding.pg_data_dir / "PG_VERSION").write_text("17", encoding="ascii")
    retired = retire_legacy_runtime_plaintext(
        binding,
        legacy_postgres_running=False,
        store=store,
    )

    assert retired == bundle
    assert not binding.legacy_pg_data_dir.exists()
    assert bundle.path.is_file()
    assert verify_legacy_runtime_recovery_bundle(binding, store=store) == bundle


def test_startup_transition_selects_fresh_runtime_only_after_bundle_and_marker(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(
        legacy_runtime_recovery,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.LEGACY_AUTHORITATIVE),
    )

    assert select_runtime_pg_data_dir_for_startup(binding, store=store) == (
        binding.legacy_pg_data_dir
    )
    assert not (binding.instance_root / "recovery").exists()

    monkeypatch.setattr(
        legacy_runtime_recovery,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE),
    )
    assert select_runtime_pg_data_dir_for_startup(binding, store=store) == (
        binding.legacy_pg_data_dir
    )
    pending_bundle = verify_legacy_runtime_recovery_bundle(binding, store=store)
    (binding.legacy_pg_data_dir / "PG_VERSION").write_text("16", encoding="ascii")

    monkeypatch.setattr(
        legacy_runtime_recovery,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY),
    )
    assert select_runtime_pg_data_dir_for_startup(binding, store=store) == binding.pg_data_dir
    assert binding.legacy_pg_data_dir.is_dir()
    bundle = verify_legacy_runtime_recovery_bundle(binding, store=store)
    assert bundle.bundle_id != pending_bundle.bundle_id

    binding.pg_data_dir.mkdir(parents=True)
    (binding.pg_data_dir / "PG_VERSION").write_text("17", encoding="ascii")
    finalized = finalize_runtime_transition_after_startup(binding, store=store)

    assert finalized == bundle
    assert not binding.legacy_pg_data_dir.exists()
    assert bundle.path.is_file()
    assert select_runtime_pg_data_dir_for_startup(binding, store=store) == binding.pg_data_dir
    assert finalize_runtime_transition_after_startup(binding, store=store) is None


def test_first_write_requires_pending_restart_bundle_and_second_restart_after_marker(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(settings, "data_dir", core)
    monkeypatch.setattr(settings, "runtime_app_data_dir", str(managed_tmp_path / "app-data"))
    monkeypatch.setattr(settings, "runtime_instance_data_dir", str(binding.instance_root))
    monkeypatch.setattr(legacy_runtime_recovery, "credential_store", lambda: store)
    monkeypatch.setattr(
        legacy_runtime_recovery,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE),
    )

    assert runtime_transition_restart_required() is True
    with pytest.raises(LegacyRuntimeRecoveryError, match="restart-prepared"):
        require_first_write_runtime_recovery()

    prepare_legacy_runtime_recovery_bundle(
        binding,
        legacy_postgres_running=False,
        store=store,
    )
    require_first_write_runtime_recovery()
    assert runtime_transition_restart_required() is False

    monkeypatch.setattr(
        legacy_runtime_recovery,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY),
    )
    assert runtime_transition_restart_required() is True


def test_verified_external_runtime_can_finalize_forward_only_transition(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _core, binding, _private_marker = _fixture(managed_tmp_path)
    store = CredentialStore(MemoryCredentialBackend())
    monkeypatch.setattr(
        legacy_runtime_recovery,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY),
    )
    select_runtime_pg_data_dir_for_startup(binding, store=store)
    verified = False

    def verify_external_runtime() -> None:
        nonlocal verified
        verified = True

    finalized = finalize_runtime_transition_after_startup(
        binding,
        store=store,
        fresh_runtime_verifier=verify_external_runtime,
    )

    assert finalized is not None
    assert verified is True
    assert not binding.legacy_pg_data_dir.exists()
