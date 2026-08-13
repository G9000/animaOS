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
    replace_staged_corefs_credentials,
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


def _credential_staged_core(root: Path) -> tuple[tuple[str, int, str], ...]:
    (root / "keyslots").mkdir(parents=True)
    manifest = {
        **_source_scoped_manifest(),
        "degraded_state": "recovery_only",
        "active_password_credential_generation": 3,
        "active_recovery_credential_generation": 4,
        "frk_rotation": {
            "active_version": 1,
            "pending_version": None,
            "decrypt_only_versions": [],
            "phase": "idle",
            "object_key_epoch": 7,
        },
        "keyslots": [
            {
                **dict(_source_scoped_manifest()["keyslots"][0]),
                "wrapping_path": path,
                "credential_generation": generation,
                "object_key_epoch": 7,
            }
            for path, generation in (("password", 3), ("recovery", 4))
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "keyslots" / "root-keyslots.json").write_bytes(b"original-keyslots")
    records: list[tuple[str, int, str]] = []
    for relative in ("manifest.json", "keyslots/root-keyslots.json"):
        payload = (root / relative).read_bytes()
        records.append((relative, len(payload), hashlib.sha256(payload).hexdigest()))
    return tuple(records)


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


def test_recovery_credential_replacement_publishes_only_fresh_fs_wrappers(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = managed_tmp_path / "staged"
    records = _credential_staged_core(root)
    roots = {1: b"filesystem-root"}
    opened: list[tuple[str, WrappingPath]] = []

    def unlock(path: Path, **kwargs: object) -> SimpleNamespace:
        credential = str(kwargs["credential"])
        wrapping_path = WrappingPath(kwargs["wrapping_path"])
        assert credential in {
            "source password",
            "new portable password",
            "fixed recovery phrase",
        }
        assert kwargs["compartment"] is PayloadScope.FS
        assert path.parent == root
        opened.append((credential, wrapping_path))
        return SimpleNamespace(sqlcipher_key=None, frks=roots)

    def slot(
        _credential: str,
        _secret: object,
        **kwargs: object,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            to_dict=lambda: {
                "purpose": kwargs["purpose"].value,
                "wrapping_path": kwargs["wrapping_path"].value,
                "status": kwargs["status"].value,
                "scope": kwargs["scope"].value,
                "key_version": kwargs["key_version"],
                "credential_generation": kwargs["credential_generation"],
                "frk_version": kwargs["frk_version"],
                "object_key_epoch": kwargs["object_key_epoch"],
                "kdf_algorithm": "argon2id-v1",
                "wrap_algorithm": "aes-256-gcm",
                "envelope_version": 1,
                "wrapped": {},
            }
        )

    monkeypatch.setattr(recovery_access, "unlock_manifest_compartment_at", unlock)
    monkeypatch.setattr(recovery_access, "_manifest_slot", slot)
    monkeypatch.setattr(
        recovery_access,
        "generate_recovery_phrase",
        lambda: "fixed recovery phrase",
    )
    boundaries: list[str] = []

    result = replace_staged_corefs_credentials(
        staging_path=root,
        expected_core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
        expected_stage_identity=staged_core_identity(root),
        control_records=records,
        source_credential="source password",
        source_wrapping_path=WrappingPath.PASSWORD,
        new_password="new portable password",
        boundary_hook=boundaries.append,
    )

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    slots = manifest["keyslots"]
    assert result.recovery_phrase == "fixed recovery phrase"
    assert result.password_generation == 4
    assert result.recovery_generation == 5
    assert manifest["archive_payload_scope"] == "fs"
    assert manifest["degraded_state"] == "recovery_only"
    assert manifest["active_password_credential_generation"] == 4
    assert manifest["active_recovery_credential_generation"] == 5
    assert {slot["purpose"] for slot in slots} == {"filesystem-root"}
    assert {slot["scope"] for slot in slots} == {"fs"}
    assert {slot["status"] for slot in slots} == {"active"}
    assert {(slot["wrapping_path"], slot["credential_generation"]) for slot in slots} == {
        ("password", 4),
        ("recovery", 5),
    }
    assert boundaries == ["keyslots_durable", "manifest_durable"]
    assert opened == [
        ("source password", WrappingPath.PASSWORD),
        ("new portable password", WrappingPath.PASSWORD),
        ("fixed recovery phrase", WrappingPath.RECOVERY),
        ("new portable password", WrappingPath.PASSWORD),
        ("fixed recovery phrase", WrappingPath.RECOVERY),
    ]
    assert not list(root.glob(".manifest-credential-check-*.json"))
    recovery_access._verify_control_records(root, result.control_records)


@pytest.mark.parametrize("failure_boundary", ["keyslots_durable", "manifest_durable"])
def test_recovery_credential_replacement_rolls_back_both_control_files(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_boundary: str,
) -> None:
    root = managed_tmp_path / "staged"
    records = _credential_staged_core(root)
    original_manifest = (root / "manifest.json").read_bytes()
    original_keyslots = (root / "keyslots" / "root-keyslots.json").read_bytes()
    roots = {1: b"filesystem-root"}

    monkeypatch.setattr(
        recovery_access,
        "unlock_manifest_compartment_at",
        lambda *_args, **_kwargs: SimpleNamespace(sqlcipher_key=None, frks=roots),
    )
    monkeypatch.setattr(
        recovery_access,
        "_manifest_slot",
        lambda *_args, **kwargs: SimpleNamespace(
            to_dict=lambda: {
                "purpose": kwargs["purpose"].value,
                "wrapping_path": kwargs["wrapping_path"].value,
                "status": kwargs["status"].value,
                "scope": kwargs["scope"].value,
                "key_version": kwargs["key_version"],
                "credential_generation": kwargs["credential_generation"],
                "frk_version": kwargs["frk_version"],
                "object_key_epoch": kwargs["object_key_epoch"],
                "kdf_algorithm": "argon2id-v1",
                "wrap_algorithm": "aes-256-gcm",
                "envelope_version": 1,
                "wrapped": {},
            }
        ),
    )
    monkeypatch.setattr(
        recovery_access,
        "generate_recovery_phrase",
        lambda: "fixed recovery phrase",
    )

    def fail(boundary: str) -> None:
        if boundary == failure_boundary:
            raise RuntimeError("injected credential publication failure")

    with pytest.raises(CoreFsRecoveryAccessError):
        replace_staged_corefs_credentials(
            staging_path=root,
            expected_core_id="018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd",
            expected_stage_identity=staged_core_identity(root),
            control_records=records,
            source_credential="source password",
            source_wrapping_path=WrappingPath.PASSWORD,
            new_password="new portable password",
            boundary_hook=fail,
        )

    assert (root / "manifest.json").read_bytes() == original_manifest
    assert (root / "keyslots" / "root-keyslots.json").read_bytes() == original_keyslots
    recovery_access._verify_control_records(root, records)
