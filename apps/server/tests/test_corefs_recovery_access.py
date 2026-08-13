from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.services.corefs import keyslots, recovery_access
from anima_server.services.corefs.keyslots import unlock_manifest_compartment_at
from anima_server.services.corefs.recovery_access import (
    CoreFsRecoveryAccessError,
    browse_staged_corefs,
    staged_core_identity,
)
from anima_server.services.corefs.types import PayloadScope, WrappingPath


def _staged_core(root: Path) -> tuple[tuple[str, int, str], ...]:
    (root / "keyslots").mkdir(parents=True)
    (root / "fs" / "catalogs").mkdir(parents=True)
    manifest = {
        "core_id": "018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
        "owner_id": "018f0f4e-4ee4-7aa5-8eb2-1eb7699855be",
        "archive_payload_scope": "fs",
        "degraded_state": "recovery_only",
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "keyslots" / "root-keyslots.json").write_text("{}", encoding="utf-8")
    (root / "fs" / "HEAD").write_bytes(b"authenticated-head")
    (root / "fs" / "catalogs" / "catalog.acore").write_bytes(b"encrypted-catalog")
    records: list[tuple[str, int, str]] = []
    for relative in (
        "manifest.json",
        "keyslots/root-keyslots.json",
        "fs/HEAD",
        "fs/catalogs/catalog.acore",
    ):
        payload = (root / relative).read_bytes()
        records.append((relative, len(payload), hashlib.sha256(payload).hexdigest()))
    return tuple(records)


def _source_scoped_manifest(*, include_soul: bool = False) -> dict[str, object]:
    slots: list[dict[str, object]] = [
        {
            "purpose": "filesystem-root",
            "wrapping_path": "password",
            "status": "active",
            "scope": "full",
            "key_version": 1,
            "credential_generation": 1,
            "frk_version": 1,
            "object_key_epoch": 1,
            "kdf_algorithm": "argon2id-v1",
            "wrap_algorithm": "aes-256-gcm",
            "envelope_version": 1,
            "wrapped": {},
        }
    ]
    if include_soul:
        slots.append(
            {
                "purpose": "soul",
                "wrapping_path": "password",
                "status": "active",
                "scope": "full",
                "key_version": 1,
                "credential_generation": 1,
                "frk_version": None,
                "object_key_epoch": None,
                "kdf_algorithm": "argon2id-v1",
                "wrap_algorithm": "aes-256-gcm",
                "envelope_version": 1,
                "wrapped": {},
            }
        )
    return {
        "core_id": "018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
        "owner_id": "018f0f4e-4ee4-7aa5-8eb2-1eb7699855be",
        "archive_payload_scope": "fs",
        "active_password_credential_generation": 1,
        "frk_rotation": {
            "active_version": 1,
            "decrypt_only_versions": [],
        },
        "keyslots_version": 1,
        "keyslots": slots,
    }


def test_partial_compartment_unlock_authenticates_source_scope_without_promoting_it(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = managed_tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_source_scoped_manifest()), encoding="utf-8")
    source_root = object()
    observed_scope: list[PayloadScope] = []

    def unwrap(_credential: str, slot, _aad: bytes) -> object:
        observed_scope.append(slot.scope)
        return source_root

    monkeypatch.setattr(keyslots, "_unwrap_manifest_slot", unwrap)
    roots = unlock_manifest_compartment_at(
        manifest_path,
        credential="source password",
        wrapping_path=WrappingPath.PASSWORD,
        compartment=PayloadScope.FS,
    )

    assert roots.scope is PayloadScope.FS
    assert roots.frks == {1: source_root}
    assert observed_scope == [PayloadScope.FULL]


def test_partial_compartment_unlock_rejects_any_foreign_root_before_unwrap(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = managed_tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(_source_scoped_manifest(include_soul=True)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        keyslots,
        "_unwrap_manifest_slot",
        lambda *_args: pytest.fail("foreign material must fail before unwrap"),
    )

    with pytest.raises(ValueError, match="foreign key material"):
        unlock_manifest_compartment_at(
            manifest_path,
            credential="source password",
            wrapping_path=WrappingPath.PASSWORD,
            compartment=PayloadScope.FS,
        )


def test_recovery_browse_opens_one_ephemeral_fs_scoped_session(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = managed_tmp_path / "staged"
    records = _staged_core(root)
    seen: dict[str, object] = {}

    def unlock(path: Path, **kwargs: object) -> SimpleNamespace:
        seen["manifest"] = path
        seen["credential"] = kwargs["credential"]
        seen["scope"] = kwargs["compartment"]
        return SimpleNamespace(frks={1: object()})

    class Native:
        def __init__(self, path: str, core_id: str) -> None:
            seen["native"] = (path, core_id)
            assert (Path(path) / "fs" / "VALIDATION_HEAD").read_bytes() == (
                Path(path) / "fs" / "HEAD"
            ).read_bytes()

        def validation_snapshot(self, _keys: object) -> dict[str, object]:
            return {"generation": 7, "catalogHash": "a" * 64}

        def list_v1(self, *_args: object, **_kwargs: object) -> bytes:
            return b'{"entries":[],"nextCursor":null}'

        def begin_close(self) -> None:
            seen["begin_close"] = True

        def close(self) -> None:
            seen["close"] = True

    monkeypatch.setattr(recovery_access, "unlock_manifest_compartment_at", unlock)
    monkeypatch.setattr(recovery_access, "derive_active_corefs_subkeys", lambda *_: "keys")

    result = browse_staged_corefs(
        staging_path=root,
        expected_core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
        expected_generation=7,
        expected_stage_identity=staged_core_identity(root),
        control_records=records,
        credential="one-request-only",
        wrapping_path=WrappingPath.RECOVERY,
        operation="list",
        logical_path="",
        session_factory=Native,
    )

    assert result.generation == 7
    assert result.catalog_hash == "a" * 64
    assert json.loads(result.payload or b"null") == {"entries": [], "nextCursor": None}
    assert seen["credential"] == "one-request-only"
    assert seen["scope"] is PayloadScope.FS
    assert seen["begin_close"] is True
    assert seen["close"] is True
    assert not (root / "fs" / "VALIDATION_HEAD").exists()


def test_recovery_browse_rejects_control_tamper_before_unwrap(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = managed_tmp_path / "staged"
    records = _staged_core(root)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        recovery_access,
        "unlock_manifest_compartment_at",
        lambda *_args, **_kwargs: pytest.fail("tampered control record must fail before unwrap"),
    )

    with pytest.raises(CoreFsRecoveryAccessError, match="control record changed"):
        browse_staged_corefs(
            staging_path=root,
            expected_core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
            expected_generation=7,
            expected_stage_identity=staged_core_identity(root),
            control_records=records,
            credential="secret",
            wrapping_path=WrappingPath.RECOVERY,
            operation="stat",
            logical_path="Notes",
        )


def test_recovery_browse_rejects_stale_cursor_before_list(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = managed_tmp_path / "staged"
    records = _staged_core(root)

    class Native:
        def __init__(self, _path: str, _core_id: str) -> None:
            pass

        def validation_snapshot(self, _keys: object) -> dict[str, object]:
            return {"generation": 7, "catalogHash": "a" * 64}

        def list_v1(self, *_args: object, **_kwargs: object) -> bytes:
            raise AssertionError("stale cursor must fail before native list")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        recovery_access,
        "unlock_manifest_compartment_at",
        lambda *_args, **_kwargs: SimpleNamespace(frks={1: object()}),
    )
    monkeypatch.setattr(recovery_access, "derive_active_corefs_subkeys", lambda *_: "keys")

    with pytest.raises(CoreFsRecoveryAccessError, match="cursor generation is stale"):
        browse_staged_corefs(
            staging_path=root,
            expected_core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
            expected_generation=7,
            expected_stage_identity=staged_core_identity(root),
            control_records=records,
            credential="secret",
            wrapping_path=WrappingPath.PASSWORD,
            operation="list",
            logical_path="",
            cursor_after="Notes",
            cursor_generation=6,
            session_factory=Native,
        )


def test_recovery_browse_removes_derived_validation_pointer_on_failure(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = managed_tmp_path / "staged"
    records = _staged_core(root)
    monkeypatch.setattr(
        recovery_access,
        "unlock_manifest_compartment_at",
        lambda *_args, **_kwargs: SimpleNamespace(frks={1: object()}),
    )
    monkeypatch.setattr(recovery_access, "derive_active_corefs_subkeys", lambda *_: "keys")

    def fail_session(path: str, _core_id: str) -> object:
        assert (Path(path) / "fs" / "VALIDATION_HEAD").is_file()
        raise RuntimeError("injected session-open failure")

    with pytest.raises(CoreFsRecoveryAccessError):
        browse_staged_corefs(
            staging_path=root,
            expected_core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
            expected_generation=7,
            expected_stage_identity=staged_core_identity(root),
            control_records=records,
            credential="secret",
            wrapping_path=WrappingPath.PASSWORD,
            operation="stat",
            logical_path="Notes",
            session_factory=fail_session,
        )

    assert not (root / "fs" / "VALIDATION_HEAD").exists()
