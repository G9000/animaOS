from __future__ import annotations

import json
import shutil
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock
from uuid import UUID

import anima_core
import pytest
from anima_server.api.routes import auth as auth_routes
from anima_server.config import settings
from anima_server.db import dispose_all_user_engines
from anima_server.db.session import get_user_session_factory
from anima_server.models import SoulKeyslot, User, UserKey
from anima_server.services.core import (
    ensure_core_manifest,
    get_manifest_path,
    get_owner_id,
    get_recovery_sqlcipher_key,
    get_user_id_from_index,
    get_wrapped_sqlcipher_key,
    store_user_index_entry,
    update_core_manifest,
)
from anima_server.services.corefs.credentials import (
    CredentialBoundary,
    change_filesystem_password_credential,
    change_password_credential_generation,
)
from anima_server.services.corefs.crypto import (
    manifest_keyslot_aad,
    soul_keyslot_aad,
    unwrap_keyslot_secret,
    wrap_keyslot_secret,
)
from anima_server.services.corefs.keyslots import (
    _manifest_slot,
    _record_from_payload,
    _record_to_payload,
    _unwrap_manifest_slot,
    manifest_has_versioned_key_hierarchy,
    unlock_key_hierarchy,
    unlock_manifest_key_hierarchy,
    validate_scope_completeness,
)
from anima_server.services.corefs.types import (
    KeyPurpose,
    KeyslotStatus,
    ManifestKeyslot,
    PayloadScope,
    WrappingPath,
)
from anima_server.services.data_crypto import ALL_DOMAINS
from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store
from conftest import managed_test_client
from cryptography.exceptions import InvalidTag
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def _reset_failed_login_attempts() -> Generator[None, None, None]:
    auth_routes._FAILED_LOGIN_ATTEMPTS.clear()
    admission = getattr(auth_routes, "_FS_CREDENTIAL_ADMISSION", None)
    if admission is not None:
        admission.reset()
    yield
    auth_routes._FAILED_LOGIN_ATTEMPTS.clear()
    if admission is not None:
        admission.reset()


def _wrapped_record() -> dict[str, object]:
    return {
        "user_id": 7,
        "kdf_salt": "c2FsdA==",
        "kdf_time_cost": 3,
        "kdf_memory_cost_kib": 65536,
        "kdf_parallelism": 4,
        "kdf_key_length": 32,
        "wrap_iv": "bm9uY2U=",
        "wrap_tag": "dGFn",
        "wrapped_key": "Y2lwaGVydGV4dA==",
    }


def _provision_retained_frk(
    *,
    password: str,
    recovery_phrase: str,
    scope: PayloadScope,
) -> None:
    if scope is PayloadScope.FS:
        _make_filesystem_only(password=password, recovery_phrase=recovery_phrase)
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    active_frk = anima_core.corefs_generate_root_key()
    active_slots = [
        _manifest_slot(
            credential,
            active_frk,
            core_id=str(manifest["core_id"]),
            owner_id=str(manifest["owner_id"]),
            purpose=KeyPurpose.FILESYSTEM_ROOT,
            wrapping_path=wrapping_path,
            status=KeyslotStatus.ACTIVE,
            scope=scope,
            key_version=2,
            credential_generation=1,
            frk_version=2,
            object_key_epoch=2,
        ).to_dict()
        for credential, wrapping_path in (
            (password, WrappingPath.PASSWORD),
            (recovery_phrase, WrappingPath.RECOVERY),
        )
    ]

    def retain_previous_frk(value: dict[str, object]) -> None:
        retained_slots = [
            slot
            for slot in value["keyslots"]
            if scope is PayloadScope.FULL or slot["purpose"] == KeyPurpose.FILESYSTEM_ROOT.value
        ]
        value["keyslots"] = [*retained_slots, *active_slots]
        value["frk_rotation"] = {
            "active_version": 2,
            "pending_version": None,
            "decrypt_only_versions": [1],
            "phase": "idle",
            "object_key_epoch": 2,
        }
        if scope is PayloadScope.FS:
            value["degraded_state"] = "recovery_only"

    update_core_manifest(retain_previous_frk)


def _make_filesystem_only(*, password: str, recovery_phrase: str) -> None:
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    converted: list[dict[str, object]] = []
    for credential, path in (
        (password, WrappingPath.PASSWORD),
        (recovery_phrase, WrappingPath.RECOVERY),
    ):
        unlocked = unlock_manifest_key_hierarchy(
            credential=credential,
            wrapping_path=path,
            expected_scope=PayloadScope.FULL,
        )
        for slot in manifest["keyslots"]:
            if (
                slot["purpose"] != KeyPurpose.FILESYSTEM_ROOT.value
                or slot["wrapping_path"] != path.value
                or slot["status"] != KeyslotStatus.ACTIVE.value
                or slot["credential_generation"] != unlocked.credential_generation
            ):
                continue
            version = int(slot["frk_version"])
            converted.append(
                _manifest_slot(
                    credential,
                    unlocked.frks[version],
                    core_id=str(manifest["core_id"]),
                    owner_id=unlocked.owner_id,
                    purpose=KeyPurpose.FILESYSTEM_ROOT,
                    wrapping_path=path,
                    status=KeyslotStatus.ACTIVE,
                    scope=PayloadScope.FS,
                    key_version=int(slot["key_version"]),
                    credential_generation=unlocked.credential_generation,
                    frk_version=version,
                    object_key_epoch=int(slot["object_key_epoch"]),
                ).to_dict()
            )

    def publish(value: dict[str, object]) -> None:
        value["keyslots"] = converted
        value["degraded_state"] = "recovery_only"

    update_core_manifest(publish)


def _make_soul_only(*, password: str, recovery_phrase: str) -> None:
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    converted: list[dict[str, object]] = []
    for credential, path in (
        (password, WrappingPath.PASSWORD),
        (recovery_phrase, WrappingPath.RECOVERY),
    ):
        unlocked = unlock_manifest_key_hierarchy(
            credential=credential,
            wrapping_path=path,
            expected_scope=PayloadScope.FULL,
        )
        assert unlocked.sqlcipher_key is not None
        source = next(
            slot
            for slot in manifest["keyslots"]
            if slot["purpose"] == KeyPurpose.SOUL.value
            and slot["wrapping_path"] == path.value
            and slot["status"] == KeyslotStatus.ACTIVE.value
            and slot["credential_generation"] == unlocked.credential_generation
        )
        converted.append(
            _manifest_slot(
                credential,
                unlocked.sqlcipher_key,
                core_id=str(manifest["core_id"]),
                owner_id=unlocked.owner_id,
                purpose=KeyPurpose.SOUL,
                wrapping_path=path,
                status=KeyslotStatus.ACTIVE,
                scope=PayloadScope.SOUL,
                key_version=int(source["key_version"]),
                credential_generation=unlocked.credential_generation,
            ).to_dict()
        )

    def publish(value: dict[str, object]) -> None:
        value["keyslots"] = converted
        value["degraded_state"] = "filesystem_missing"

    update_core_manifest(publish)


def test_legacy_manifest_gets_one_stable_opaque_owner_before_keyslots(
    managed_tmp_path,
) -> None:
    original = settings.data_dir
    settings.data_dir = managed_tmp_path
    try:
        managed_tmp_path.mkdir(parents=True, exist_ok=True)
        get_manifest_path().write_text(
            json.dumps({"core_id": "legacy-core", "owner_user_id": 7}),
            encoding="utf-8",
        )

        first = ensure_core_manifest()
        second = ensure_core_manifest()

        assert UUID(str(first["owner_id"]))
        assert first["owner_id"] == second["owner_id"] == get_owner_id()
        assert first["owner_binding"] == {"legacy_user_id": 7}
        assert not get_manifest_path().with_suffix(".json.tmp").exists()
    finally:
        settings.data_dir = original


def test_manifest_retains_legacy_sqlcipher_compatibility_without_profile_fields(
    managed_tmp_path,
) -> None:
    original = settings.data_dir
    settings.data_dir = managed_tmp_path
    try:
        managed_tmp_path.mkdir(parents=True, exist_ok=True)
        get_manifest_path().write_text(
            json.dumps(
                {
                    "core_id": "legacy-core",
                    "owner_user_id": 7,
                    "wrapped_sqlcipher_key": _wrapped_record(),
                    "user_index": {"alice": 7},
                }
            ),
            encoding="utf-8",
        )

        ensure_core_manifest()
        store_user_index_entry("alice", 7)
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))

        assert get_wrapped_sqlcipher_key() == _wrapped_record()
        assert manifest["user_index"] == {"alice": 7}
        new_structures = json.dumps(
            {"owner_binding": manifest["owner_binding"], "keyslots": manifest["keyslots"]}
        )
        assert "alice" not in new_structures
        assert "raw_key" not in new_structures
        assert "display_name" not in new_structures
    finally:
        settings.data_dir = original


def test_keyslot_contract_rejects_unknown_algorithms_versions_and_enums() -> None:
    valid = {
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
        "wrapped": _wrapped_record(),
    }

    slot = ManifestKeyslot.from_dict(valid)
    assert slot.purpose is KeyPurpose.SOUL
    assert slot.wrapping_path is WrappingPath.PASSWORD
    assert slot.status is KeyslotStatus.ACTIVE

    for field, value in {
        "purpose": "other",
        "wrapping_path": "escrow",
        "status": "retired",
        "scope": "hybrid",
        "kdf_algorithm": "scrypt",
        "wrap_algorithm": "aes-128-gcm",
        "envelope_version": 2,
    }.items():
        with pytest.raises(ValueError):
            ManifestKeyslot.from_dict({**valid, field: value})

    filesystem = {
        **valid,
        "purpose": "filesystem-root",
        "frk_version": 2,
        "object_key_epoch": 1,
    }
    with pytest.raises(ValueError, match="FRK version must match key version"):
        ManifestKeyslot.from_dict(filesystem)


def test_manifest_publication_returns_only_after_native_durability(
    managed_tmp_path,
    monkeypatch,
) -> None:
    original = settings.data_dir
    settings.data_dir = managed_tmp_path
    events: list[str] = []
    try:

        def publish(path: str, payload: bytes) -> None:
            events.append("native-durable")
            assert str(get_manifest_path()) == path
            get_manifest_path().write_bytes(payload)

        monkeypatch.setattr("anima_core.corefs_atomic_publish", publish, raising=False)
        update_core_manifest(lambda manifest: manifest.__setitem__("marker", "published"))
        events.append("returned")

        assert events == ["native-durable", "returned"]
        assert json.loads(get_manifest_path().read_text(encoding="utf-8"))["marker"] == "published"
    finally:
        settings.data_dir = original


def test_manifest_publication_falls_back_when_native_binding_is_missing(
    managed_tmp_path,
    monkeypatch,
) -> None:
    original = settings.data_dir
    settings.data_dir = managed_tmp_path
    monkeypatch.delattr(anima_core, "corefs_atomic_publish", raising=False)
    try:
        update_core_manifest(lambda manifest: manifest.__setitem__("marker", "fallback"))

        manifest_path = get_manifest_path()
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["marker"] == "fallback"
        assert list(managed_tmp_path.glob(f".{manifest_path.name}.*.tmp")) == []
    finally:
        settings.data_dir = original


def test_manifest_publication_failure_keeps_previous_generation(
    managed_tmp_path,
    monkeypatch,
) -> None:
    original = settings.data_dir
    settings.data_dir = managed_tmp_path
    try:
        update_core_manifest(lambda manifest: manifest.__setitem__("marker", "old"))
        before = get_manifest_path().read_bytes()
        monkeypatch.setattr(
            "anima_core.corefs_atomic_publish",
            lambda *_args: (_ for _ in ()).throw(OSError("durability failed")),
            raising=False,
        )

        with pytest.raises(OSError, match="durability failed"):
            update_core_manifest(lambda manifest: manifest.__setitem__("marker", "new"))

        assert get_manifest_path().read_bytes() == before
    finally:
        settings.data_dir = original


def test_scoped_key_completeness_forbids_cross_compartment_material() -> None:
    domains = {"memories", "conversations"}

    validate_scope_completeness(
        PayloadScope.FULL,
        purposes={KeyPurpose.SOUL, KeyPurpose.FILESYSTEM_ROOT},
        soul_domains=domains,
        required_soul_domains=domains,
        frk_versions={1},
        required_frk_versions={1},
    )
    validate_scope_completeness(
        PayloadScope.SOUL,
        purposes={KeyPurpose.SOUL},
        soul_domains=domains,
        required_soul_domains=domains,
        frk_versions=set(),
        required_frk_versions=set(),
    )
    validate_scope_completeness(
        PayloadScope.FS,
        purposes={KeyPurpose.FILESYSTEM_ROOT},
        soul_domains=set(),
        required_soul_domains=set(),
        frk_versions={1},
        required_frk_versions={1},
    )

    with pytest.raises(ValueError, match="forbids Filesystem Root"):
        validate_scope_completeness(
            PayloadScope.SOUL,
            purposes={KeyPurpose.SOUL, KeyPurpose.FILESYSTEM_ROOT},
            soul_domains=domains,
            required_soul_domains=domains,
            frk_versions={1},
            required_frk_versions=set(),
        )
    with pytest.raises(ValueError, match="forbids Soul"):
        validate_scope_completeness(
            PayloadScope.FS,
            purposes={KeyPurpose.SOUL, KeyPurpose.FILESYSTEM_ROOT},
            soul_domains=set(),
            required_soul_domains=set(),
            frk_versions={1},
            required_frk_versions={1},
        )
    with pytest.raises(ValueError, match="incomplete Soul domain set"):
        validate_scope_completeness(
            PayloadScope.FULL,
            purposes={KeyPurpose.SOUL, KeyPurpose.FILESYSTEM_ROOT},
            soul_domains={"memories"},
            required_soul_domains=domains,
            frk_versions={1},
            required_frk_versions={1},
        )


def test_soul_keyslot_identity_excludes_mutable_status() -> None:
    engine = create_engine("sqlite://")
    SoulKeyslot.__table__.create(engine)
    common = {
        "owner_id": "019f-owner",
        "domain": "memories",
        "wrapping_path": "password",
        "key_version": 1,
        "credential_generation": 1,
        "kdf_algorithm": "argon2id-v1",
        "wrap_algorithm": "aes-256-gcm",
        "envelope_version": 1,
        "kdf_salt": "salt",
        "kdf_time_cost": 3,
        "kdf_memory_cost_kib": 65536,
        "kdf_parallelism": 4,
        "kdf_key_length": 32,
        "wrap_iv": "nonce",
        "wrap_tag": "tag",
        "wrapped_dek": "ciphertext",
    }
    with Session(engine) as db:
        row = SoulKeyslot(**common, status="active")
        db.add(row)
        db.commit()

        row.status = "decrypt-only"
        db.commit()

        db.add(SoulKeyslot(**common, status="pending"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_keyslot_wrapping_uses_distinct_exact_aad_contracts() -> None:
    manifest_aad = manifest_keyslot_aad(
        core_id="019f-core",
        owner_id="019f-owner",
        purpose=KeyPurpose.FILESYSTEM_ROOT,
        key_version=1,
        credential_generation=2,
        scope=PayloadScope.FULL,
        frk_version=1,
        object_key_epoch=3,
        wrapping_path=WrappingPath.PASSWORD,
    )
    soul_aad = soul_keyslot_aad(
        core_id="019f-core",
        owner_id="019f-owner",
        domain="memories",
        key_version=1,
        credential_generation=2,
        wrapping_path=WrappingPath.PASSWORD,
    )

    assert manifest_aad == (
        b"anima-keyslot-v1:core=019f-core:owner=019f-owner:"
        b"purpose=filesystem-root:version=1:generation=2:scope=full:"
        b"frk-version=1:object-key-epoch=3:path=password"
    )
    assert soul_aad == (
        b"anima-soul-keyslot-v1:core=019f-core:owner=019f-owner:"
        b"domain=memories:version=1:generation=2:path=password"
    )
    assert manifest_aad != soul_aad

    secret = bytes([0x37]) * 32
    wrapped = wrap_keyslot_secret("password-123", secret, manifest_aad)
    assert unwrap_keyslot_secret("password-123", wrapped, manifest_aad) == secret
    with pytest.raises(InvalidTag):
        unwrap_keyslot_secret("password-123", wrapped, soul_aad)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("credential_generation", 3),
        ("scope", PayloadScope.FS),
        ("object_key_epoch", 4),
    ],
)
def test_manifest_keyslot_immutable_metadata_relabels_fail_authentication(
    field: str,
    replacement: object,
) -> None:
    root = anima_core.corefs_generate_root_key()
    slot = _manifest_slot(
        "password-123",
        root,
        core_id="019f-core",
        owner_id="019f-owner",
        purpose=KeyPurpose.FILESYSTEM_ROOT,
        wrapping_path=WrappingPath.PASSWORD,
        status=KeyslotStatus.ACTIVE,
        scope=PayloadScope.FULL,
        key_version=1,
        credential_generation=2,
        frk_version=1,
        object_key_epoch=3,
    )
    relabeled = replace(slot, **{field: replacement})
    relabeled_aad = manifest_keyslot_aad(
        core_id="019f-core",
        owner_id="019f-owner",
        purpose=relabeled.purpose,
        key_version=relabeled.key_version,
        credential_generation=relabeled.credential_generation,
        scope=relabeled.scope,
        frk_version=relabeled.frk_version,
        object_key_epoch=relabeled.object_key_epoch,
        wrapping_path=relabeled.wrapping_path,
    )

    with pytest.raises(ValueError):
        _unwrap_manifest_slot("password-123", relabeled, relabeled_aad)


def test_soul_keyslot_generation_relabel_fails_authentication() -> None:
    original_aad = soul_keyslot_aad(
        core_id="019f-core",
        owner_id="019f-owner",
        domain="memories",
        key_version=1,
        credential_generation=2,
        wrapping_path=WrappingPath.PASSWORD,
    )
    relabeled_aad = soul_keyslot_aad(
        core_id="019f-core",
        owner_id="019f-owner",
        domain="memories",
        key_version=1,
        credential_generation=3,
        wrapping_path=WrappingPath.PASSWORD,
    )
    wrapped = wrap_keyslot_secret("password-123", bytes([0x37]) * 32, original_aad)

    with pytest.raises(InvalidTag):
        unwrap_keyslot_secret("password-123", wrapped, relabeled_aad)


def test_persisted_keyslot_argon2_profile_is_bounded_before_derivation() -> None:
    payload = _record_to_payload(
        wrap_keyslot_secret("password-123", bytes([0x37]) * 32, b"bounded-profile")
    )
    for field, invalid in (
        ("kdf_time_cost", 4),
        ("kdf_memory_cost_kib", 1_048_576),
        ("kdf_parallelism", 8),
        ("kdf_key_length", 64),
        ("wrap_iv", "AA=="),
    ):
        with pytest.raises(ValueError, match="unsupported keyslot KDF/wrap profile"):
            _record_from_payload({**payload, field: invalid})


def test_registration_provisions_complete_password_and_recovery_hierarchy() -> None:
    with managed_test_client("anima-corefs-keyslots-") as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert response.status_code == 201
        phrase = response.json()["recoveryPhrase"]
        owner_id = get_owner_id()
        assert owner_id is not None

        with get_user_session_factory(0)() as db:
            rows = db.query(SoulKeyslot).all()
            assert len(rows) == len(ALL_DOMAINS) * 2
            assert {row.domain for row in rows} == set(ALL_DOMAINS)
            assert {row.wrapping_path for row in rows} == {"password", "recovery"}
            assert {row.status for row in rows} == {"active"}
            assert {row.owner_id for row in rows} == {owner_id}

            password_keys = unlock_key_hierarchy(
                db,
                credential="password-123",
                wrapping_path=WrappingPath.PASSWORD,
                scope=PayloadScope.FULL,
            )
            recovery_keys = unlock_key_hierarchy(
                db,
                credential=phrase,
                wrapping_path=WrappingPath.RECOVERY,
                scope=PayloadScope.FULL,
            )

        assert password_keys.sqlcipher_key == recovery_keys.sqlcipher_key
        assert password_keys.frks == recovery_keys.frks
        assert password_keys.soul_domains == recovery_keys.soul_domains
        assert set(password_keys.soul_domains) == set(ALL_DOMAINS)
        assert set(password_keys.frks) == {1}

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert len(manifest["keyslots"]) == 4
        assert manifest["active_password_credential_generation"] == 1
        assert manifest["active_recovery_credential_generation"] == 1
        assert manifest["frk_rotation"]["active_version"] == 1
        assert all("raw" not in key for key in manifest["keyslots"] for key in key)


def test_versioned_login_session_carries_frk_derived_corefs_subkeys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "core_passphrase", "")

    with managed_test_client("anima-corefs-login-subkeys-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()
        client.post(
            "/api/auth/logout",
            headers={"x-anima-unlock": registered["unlockToken"]},
        )

        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )

        assert login.status_code == 200
        session = unlock_session_store.resolve(login.json()["unlockToken"])
        assert session is not None
        assert isinstance(
            getattr(session, "corefs_keys", None),
            anima_core.CorefsSubkeys,
        )


def test_registration_publishes_legacy_locators_before_hierarchy_activation(
    monkeypatch,
) -> None:
    phrase = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    monkeypatch.setattr(
        "anima_server.services.recovery.generate_recovery_phrase",
        lambda: phrase,
    )
    monkeypatch.setattr(
        "anima_server.services.corefs.keyslots.provision_initial_key_hierarchy",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("before activation")),
    )

    with managed_test_client("anima-registration-before-activation-") as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert response.status_code == 503
        assert get_user_id_from_index("alice") == 0
        assert get_recovery_sqlcipher_key() is not None

        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert login.status_code == 200
        recovery = client.post(
            "/api/auth/recover",
            json={
                "recoveryPhrase": phrase,
                "newPassword": "recovered-password-123",
                "scope": "full",
            },
        )
        assert recovery.status_code == 200


def test_registration_backfill_crash_allows_password_change_before_recovery_upgrade(
    monkeypatch,
) -> None:
    from anima_server.services.corefs import keyslots

    phrase = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    monkeypatch.setattr(
        "anima_server.services.recovery.generate_recovery_phrase",
        lambda: phrase,
    )
    publish_manifest = keyslots.update_core_manifest

    def crash_after_soul_backfill(_mutator) -> None:
        raise RuntimeError("crash after Soul backfill")

    monkeypatch.setattr(keyslots, "update_core_manifest", crash_after_soul_backfill)
    with managed_test_client("anima-registration-backfill-crash-") as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert response.status_code == 503
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest["keyslots"] == []
        assert "active_recovery_credential_generation" not in manifest

        with get_user_session_factory(0)() as db:
            stranded = db.query(SoulKeyslot).all()
            assert len(stranded) == len(ALL_DOMAINS) * 2
            assert {row.status for row in stranded} == {"active"}

        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert login.status_code == 200
        monkeypatch.setattr(keyslots, "update_core_manifest", publish_manifest)

        changed = client.post(
            "/api/auth/change-password",
            headers={"x-anima-unlock": login.json()["unlockToken"]},
            json={"oldPassword": "password-123", "newPassword": "password-456"},
        )
        assert changed.status_code == 200

        prepared = client.post(
            "/api/auth/recovery-credential/prepare",
            headers={"x-anima-unlock": changed.json()["unlockToken"]},
            json={
                "currentRecoveryPhrase": phrase,
                "currentPassword": "password-456",
                "scope": "full",
            },
        )
        assert prepared.status_code == 200
        payload = prepared.json()
        confirmed = client.post(
            "/api/auth/recovery-credential/confirm",
            headers={"x-anima-unlock": changed.json()["unlockToken"]},
            json={
                "recoveryPhrase": payload["recoveryPhrase"],
                "pendingGeneration": payload["pendingGeneration"],
                "scope": payload["scope"],
                "currentPassword": "password-456",
            },
        )
        assert confirmed.status_code == 200


def test_registration_crash_after_hierarchy_activation_keeps_account_locator(
    monkeypatch,
) -> None:
    from anima_server.services.corefs import keyslots

    original = keyslots.provision_initial_key_hierarchy

    def activate_then_crash(*args, **kwargs) -> None:
        original(*args, **kwargs)
        raise RuntimeError("after activation")

    monkeypatch.setattr(keyslots, "provision_initial_key_hierarchy", activate_then_crash)
    with managed_test_client("anima-registration-after-activation-") as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert response.status_code == 503
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest["active_password_credential_generation"] == 1
        assert get_user_id_from_index("alice") == 0
        assert get_recovery_sqlcipher_key() is not None


def test_versioned_login_never_falls_back_to_legacy_wrappers() -> None:
    with managed_test_client("anima-versioned-login-fail-closed-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert registered.status_code == 201

        def delete_versioned_slots(manifest: dict[str, object]) -> None:
            assert manifest["active_password_credential_generation"] == 1
            manifest["keyslots"] = []

        update_core_manifest(delete_versioned_slots)
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_all_user_engines()

        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert login.status_code == 401


@pytest.mark.parametrize("slot_status", ["active", "decrypt-only"])
def test_activated_versioned_slots_block_legacy_login_and_recovery_without_markers(
    slot_status: str,
) -> None:
    with managed_test_client(f"anima-versioned-marker-damage-{slot_status}-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert registered.status_code == 201
        recovery_phrase = registered.json()["recoveryPhrase"]

        def remove_authority_markers(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = [
                {**slot, "status": slot_status} for slot in manifest["keyslots"]
            ]
            manifest.pop("active_password_credential_generation", None)
            manifest.pop("active_recovery_credential_generation", None)
            manifest.pop("frk_rotation", None)

        update_core_manifest(remove_authority_markers)
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest_has_versioned_key_hierarchy(manifest)
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_all_user_engines()

        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        recovery = client.post(
            "/api/auth/recover",
            json={
                "recoveryPhrase": recovery_phrase,
                "newPassword": "new-password-123",
                "scope": "full",
            },
        )
        assert login.status_code == 401
        assert recovery.status_code == 401


def test_versioned_login_rejects_corrupt_active_manifest_soul_root() -> None:
    with managed_test_client("anima-versioned-root-corrupt-") as client:
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "password-123", "name": "Alice"},
            ).status_code
            == 201
        )

        def corrupt_active_soul_root(manifest: dict[str, object]) -> None:
            slots = list(manifest["keyslots"])
            for slot in slots:
                if (
                    slot["wrapping_path"] == "password"
                    and slot["purpose"] == "soul"
                    and slot["status"] == "active"
                ):
                    slot["wrapped"] = {**slot["wrapped"], "kdf_salt": "not-base64"}
                    return
            raise AssertionError("active password Soul root is missing")

        update_core_manifest(corrupt_active_soul_root)
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_all_user_engines()

        response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert response.status_code == 401


def test_versioned_login_rejects_abusive_soul_keyslot_profile() -> None:
    with managed_test_client("anima-soul-kdf-bounds-") as client:
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "password-123", "name": "Alice"},
            ).status_code
            == 201
        )
        with get_user_session_factory(0)() as db:
            row = (
                db.query(SoulKeyslot)
                .filter_by(
                    wrapping_path="password",
                    status="active",
                )
                .first()
            )
            assert row is not None
            row.kdf_memory_cost_kib = 2**31
            db.commit()
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_all_user_engines()

        response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert response.status_code == 401


def test_legacy_login_and_recovery_reject_abusive_persisted_profiles() -> None:
    with managed_test_client("anima-legacy-kdf-bounds-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert registered.status_code == 201
        recovery_phrase = registered.json()["recoveryPhrase"]

        def make_malicious_legacy(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = []
            manifest.pop("active_password_credential_generation", None)
            manifest.pop("active_recovery_credential_generation", None)
            manifest.pop("frk_rotation", None)
            password_wrapper = dict(manifest["wrapped_sqlcipher_key"])
            password_wrapper["kdf_memory_cost_kib"] = 2**31
            manifest["wrapped_sqlcipher_key"] = password_wrapper
            recovery_wrapper = dict(manifest["recovery_sqlcipher_key"])
            recovery_wrapper["kdf_memory_cost_kib"] = 2**31
            manifest["recovery_sqlcipher_key"] = recovery_wrapper

        update_core_manifest(make_malicious_legacy)
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_all_user_engines()

        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert login.status_code == 401
        recovery = client.post(
            "/api/auth/recover",
            json={
                "recoveryPhrase": recovery_phrase,
                "newPassword": "new-password-123",
                "scope": "full",
            },
        )
        assert recovery.status_code == 401


def test_legacy_login_rejects_abusive_user_key_profile() -> None:
    with managed_test_client("anima-legacy-user-key-bounds-") as client:
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "password-123", "name": "Alice"},
            ).status_code
            == 201
        )
        with get_user_session_factory(0)() as db:
            row = db.query(UserKey).filter_by(user_id=0).first()
            assert row is not None
            row.kdf_memory_cost_kib = 2**31
            db.commit()

        def make_legacy(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = []
            manifest.pop("active_password_credential_generation", None)
            manifest.pop("active_recovery_credential_generation", None)
            manifest.pop("frk_rotation", None)

        update_core_manifest(make_legacy)
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_all_user_engines()

        response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert response.status_code == 401


def test_soul_scoped_manifest_activation_crash_finalizes_during_login() -> None:
    with managed_test_client("anima-soul-login-finalize-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert registered.status_code == 201

        _make_soul_only(
            password="password-123",
            recovery_phrase=str(registered.json()["recoveryPhrase"]),
        )
        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            with pytest.raises(RuntimeError, match="manifest-activated"):
                change_password_credential_generation(
                    db,
                    user,
                    old_password="password-123",
                    new_password="new-password-123",
                    current_deks=unlock_session_store.get_active_deks(0) or {},
                    scope=PayloadScope.SOUL,
                    failure_injector=lambda boundary: (
                        (_ for _ in ()).throw(RuntimeError("manifest-activated"))
                        if boundary is CredentialBoundary.MANIFEST_ACTIVATED
                        else None
                    ),
                )

        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_all_user_engines()
        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "new-password-123"},
        )
        assert login.status_code == 200
        assert login.json()["unlockToken"]


def test_fs_only_credentials_rotate_without_soul_database_or_unlock_session() -> None:
    with managed_test_client("anima-fs-only-credentials-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert registered.status_code == 201
        recovery_phrase = registered.json()["recoveryPhrase"]

        _make_filesystem_only(
            password="password-123",
            recovery_phrase=str(recovery_phrase),
        )
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_all_user_engines()
        user_dir = settings.data_dir / "users" / "0"
        shutil.rmtree(user_dir)

        password_change = client.post(
            "/api/auth/corefs/change-password",
            json={"currentPassword": "password-123", "newPassword": "new-password-123"},
        )
        assert password_change.status_code == 200
        assert password_change.json() == {"success": True, "scope": "fs"}

        prepared = client.post(
            "/api/auth/corefs/recovery-credential/prepare",
            json={
                "currentPassword": "new-password-123",
                "currentRecoveryPhrase": recovery_phrase,
            },
        )
        assert prepared.status_code == 200
        payload = prepared.json()
        assert payload["scope"] == "fs"
        assert "unlockToken" not in payload

        confirmed = client.post(
            "/api/auth/corefs/recovery-credential/confirm",
            json={
                "recoveryPhrase": payload["recoveryPhrase"],
                "pendingGeneration": payload["pendingGeneration"],
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json() == {"success": True, "scope": "fs"}
        assert not user_dir.exists()


def test_fs_recovery_prepare_maps_invalid_current_phrase_to_unauthorized() -> None:
    with managed_test_client("anima-fs-prepare-invalid-phrase-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()
        _make_filesystem_only(
            password="password-123",
            recovery_phrase=str(registered["recoveryPhrase"]),
        )

        response = client.post(
            "/api/auth/corefs/recovery-credential/prepare",
            json={
                "currentPassword": "password-123",
                "currentRecoveryPhrase": "legal winner thank year wave sausage worth useful legal winner thank yellow",
            },
        )

        assert response.status_code == 401


def test_fs_recovery_confirm_maps_invalid_pending_phrase_to_unauthorized() -> None:
    with managed_test_client("anima-fs-confirm-invalid-phrase-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()
        _make_filesystem_only(
            password="password-123",
            recovery_phrase=str(registered["recoveryPhrase"]),
        )
        prepared = client.post(
            "/api/auth/corefs/recovery-credential/prepare",
            json={
                "currentPassword": "password-123",
                "currentRecoveryPhrase": registered["recoveryPhrase"],
            },
        ).json()

        response = client.post(
            "/api/auth/corefs/recovery-credential/confirm",
            json={
                "recoveryPhrase": "legal winner thank year wave sausage worth useful legal winner thank yellow",
                "pendingGeneration": prepared["pendingGeneration"],
            },
        )

        assert response.status_code == 401


def test_fs_recovery_prepare_rejects_unversioned_manifest(monkeypatch) -> None:
    monkeypatch.setattr(settings, "core_passphrase", "agent-managed-core-passphrase")

    with managed_test_client("anima-fs-prepare-unversioned-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert not manifest_has_versioned_key_hierarchy(manifest)

        response = client.post(
            "/api/auth/corefs/recovery-credential/prepare",
            json={
                "currentPassword": "password-123",
                "currentRecoveryPhrase": registered["recoveryPhrase"],
            },
        )

        assert response.status_code == 401
        assert response.json() == {"error": "versioned key hierarchy is absent"}


def test_fs_recovery_confirm_rejects_unversioned_manifest(monkeypatch) -> None:
    monkeypatch.setattr(settings, "core_passphrase", "agent-managed-core-passphrase")

    with managed_test_client("anima-fs-confirm-unversioned-") as client:
        client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert not manifest_has_versioned_key_hierarchy(manifest)

        response = client.post(
            "/api/auth/corefs/recovery-credential/confirm",
            json={
                "recoveryPhrase": "legal winner thank year wave sausage worth useful legal winner thank yellow",
                "pendingGeneration": 1,
            },
        )

        assert response.status_code == 401
        assert response.json() == {"error": "versioned key hierarchy is absent"}


def test_fs_credential_endpoints_share_precharged_client_limit(monkeypatch) -> None:
    derived: list[str] = []

    def reject_change(**_kwargs) -> None:
        derived.append("change")
        raise ValueError("invalid")

    def reject_prepare(**_kwargs) -> None:
        derived.append("prepare")
        raise ValueError("invalid")

    def reject_confirm(**_kwargs) -> None:
        derived.append("confirm")
        raise ValueError("invalid")

    monkeypatch.setattr(auth_routes, "change_filesystem_password_credential", reject_change)
    monkeypatch.setattr(auth_routes, "prepare_filesystem_recovery_credential", reject_prepare)
    monkeypatch.setattr(auth_routes, "confirm_filesystem_recovery_credential", reject_confirm)

    requests = [
        (
            "/api/auth/corefs/change-password",
            {"currentPassword": "current-password", "newPassword": "new-password"},
        ),
        (
            "/api/auth/corefs/recovery-credential/prepare",
            {
                "currentPassword": "current-password",
                "currentRecoveryPhrase": "old recovery phrase",
            },
        ),
        (
            "/api/auth/corefs/recovery-credential/confirm",
            {"recoveryPhrase": "new recovery phrase", "pendingGeneration": 2},
        ),
    ]
    with managed_test_client("anima-fs-admission-rate-") as client:
        responses = [
            client.post(path, json=payload) for _ in range(2) for path, payload in requests
        ]

    assert [response.status_code for response in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    assert len(derived) == 5


def test_fs_credential_busy_rejection_happens_before_native_kdf(monkeypatch) -> None:
    entered_kdf = Event()
    release_kdf = Event()
    call_lock = Lock()
    kdf_calls = 0
    original_unwrap = anima_core.corefs_unwrap_root_key

    def blocking_unwrap(*args, **kwargs):
        nonlocal kdf_calls
        with call_lock:
            kdf_calls += 1
            call_number = kdf_calls
        if call_number == 1:
            entered_kdf.set()
            if not release_kdf.wait(timeout=10):
                raise RuntimeError("timed out waiting to release test KDF")
        return original_unwrap(*args, **kwargs)

    with managed_test_client("anima-fs-admission-busy-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert registered.status_code == 201

        _make_filesystem_only(
            password="password-123",
            recovery_phrase=str(registered.json()["recoveryPhrase"]),
        )
        monkeypatch.setattr(anima_core, "corefs_unwrap_root_key", blocking_unwrap)
        request = {
            "currentPassword": "password-123",
            "newPassword": "new-password-123",
        }
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                client.post,
                "/api/auth/corefs/change-password",
                json=request,
            )
            assert entered_kdf.wait(timeout=10)
            second = executor.submit(
                client.post,
                "/api/auth/corefs/change-password",
                json=request,
            )
            second_response = second.result(timeout=30)
            with call_lock:
                calls_before_release = kdf_calls
            release_kdf.set()
            first_response = first.result(timeout=30)

    assert second_response.status_code == 429
    assert calls_before_release == 1
    assert first_response.status_code == 200


def test_filesystem_password_activation_rejects_stale_expected_generation() -> None:
    with managed_test_client("anima-fs-password-stale-generation-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert registered.status_code == 201

        _make_filesystem_only(
            password="password-123",
            recovery_phrase=str(registered.json()["recoveryPhrase"]),
        )

        def replace_active_generation(boundary: CredentialBoundary) -> None:
            if boundary is CredentialBoundary.PENDING_REOPEN_VERIFIED:
                update_core_manifest(
                    lambda manifest: manifest.update(active_password_credential_generation=7)
                )

        with pytest.raises(ValueError, match="active password credential generation changed"):
            change_filesystem_password_credential(
                current_password="password-123",
                new_password="new-password-123",
                failure_injector=replace_active_generation,
            )
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest["active_password_credential_generation"] == 7


def test_filesystem_password_rejects_missing_retained_frk_before_activation() -> None:
    with managed_test_client("anima-fs-password-retained-frk-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()
        _provision_retained_frk(
            password="password-123",
            recovery_phrase=str(registered["recoveryPhrase"]),
            scope=PayloadScope.FS,
        )
        boundaries: list[CredentialBoundary] = []

        def remove_retained_pending_slot(boundary: CredentialBoundary) -> None:
            boundaries.append(boundary)
            if boundary is not CredentialBoundary.MANIFEST_PENDING_DURABLE:
                return

            def remove_slot(value: dict[str, object]) -> None:
                value["keyslots"] = [
                    slot
                    for slot in value["keyslots"]
                    if not (
                        slot["wrapping_path"] == WrappingPath.PASSWORD.value
                        and slot["credential_generation"] == 2
                        and slot["status"] == KeyslotStatus.PENDING.value
                        and slot["frk_version"] == 1
                    )
                ]

            update_core_manifest(remove_slot)

        with pytest.raises(ValueError, match="incomplete Filesystem Root key set"):
            change_filesystem_password_credential(
                current_password="password-123",
                new_password="new-password-123",
                failure_injector=remove_retained_pending_slot,
            )

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert boundaries == [CredentialBoundary.MANIFEST_PENDING_DURABLE]
        assert manifest["active_password_credential_generation"] == 1
        old_roots = unlock_manifest_key_hierarchy(
            credential="password-123",
            wrapping_path=WrappingPath.PASSWORD,
            expected_scope=PayloadScope.FS,
        )
        assert set(old_roots.frks) == {1, 2}


def test_general_recover_rejects_fs_agent_startup() -> None:
    with managed_test_client("anima-fs-recover-reject-") as client:
        response = client.post(
            "/api/auth/recover",
            json={
                "recoveryPhrase": "abandon " * 11 + "about",
                "newPassword": "new-password-123",
                "scope": "fs",
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "CoreFS-only recovery cannot start an agent session"


def test_registration_persists_owner_binding_before_keyslot_publication(monkeypatch) -> None:
    def crash_before_slots(*args, **kwargs) -> None:
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest["owner_user_id"] == 0
        assert manifest["owner_binding"] == {"legacy_user_id": 0}
        assert manifest["keyslots"] == []
        raise RuntimeError("crash after owner binding")

    with managed_test_client("anima-owner-binding-boundary-") as client:
        monkeypatch.setattr(
            "anima_server.services.corefs.keyslots.provision_initial_key_hierarchy",
            crash_before_slots,
        )
        response = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        )
        assert response.status_code == 503


def test_change_password_uses_cross_store_generation_and_retains_old_slots() -> None:
    with managed_test_client("anima-corefs-password-generation-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()
        changed = client.post(
            "/api/auth/change-password",
            headers={"x-anima-unlock": registered["unlockToken"]},
            json={"oldPassword": "password-123", "newPassword": "password-456"},
        )
        assert changed.status_code == 200

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest["active_password_credential_generation"] == 2
        password_slots = [
            slot for slot in manifest["keyslots"] if slot["wrapping_path"] == "password"
        ]
        assert {(slot["credential_generation"], slot["status"]) for slot in password_slots} == {
            (1, "decrypt-only"),
            (2, "active"),
        }
        with get_user_session_factory(0)() as db:
            rows = db.scalars(
                SoulKeyslot.__table__.select().where(SoulKeyslot.wrapping_path == "password")
            ).all()
            assert rows
        client.post(
            "/api/auth/logout",
            headers={"x-anima-unlock": changed.json()["unlockToken"]},
        )
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "password-123"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "password-456"},
            ).status_code
            == 200
        )


def test_change_password_rejects_duplicate_for_missing_retained_frk_before_activation() -> None:
    with managed_test_client("anima-password-retained-frk-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()
        recovery_phrase = str(registered["recoveryPhrase"])
        _provision_retained_frk(
            password="password-123",
            recovery_phrase=recovery_phrase,
            scope=PayloadScope.FULL,
        )
        session = unlock_session_store.resolve(registered["unlockToken"])
        assert session is not None
        boundaries: list[CredentialBoundary] = []

        def replace_retained_pending_slot(boundary: CredentialBoundary) -> None:
            boundaries.append(boundary)
            if boundary is not CredentialBoundary.MANIFEST_PENDING_DURABLE:
                return

            def duplicate_active_pending_slot(value: dict[str, object]) -> None:
                slots = list(value["keyslots"])
                active_pending = next(
                    slot
                    for slot in slots
                    if slot["wrapping_path"] == WrappingPath.PASSWORD.value
                    and slot["credential_generation"] == 2
                    and slot["status"] == KeyslotStatus.PENDING.value
                    and slot["frk_version"] == 2
                )
                value["keyslots"] = [
                    slot
                    for slot in slots
                    if not (
                        slot["wrapping_path"] == WrappingPath.PASSWORD.value
                        and slot["credential_generation"] == 2
                        and slot["status"] == KeyslotStatus.PENDING.value
                        and slot["frk_version"] == 1
                    )
                ]
                value["keyslots"].append(dict(active_pending))

            update_core_manifest(duplicate_active_pending_slot)

        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            with pytest.raises(ValueError):
                change_password_credential_generation(
                    db,
                    user,
                    old_password="password-123",
                    new_password="new-password-123",
                    current_deks=session.deks,
                    failure_injector=replace_retained_pending_slot,
                )

            manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
            assert boundaries == [
                CredentialBoundary.SOUL_PENDING_DURABLE,
                CredentialBoundary.MANIFEST_PENDING_DURABLE,
            ]
            assert manifest["active_password_credential_generation"] == 1
            assert {
                (row.credential_generation, row.status)
                for row in db.query(SoulKeyslot).filter_by(
                    wrapping_path=WrappingPath.PASSWORD.value
                )
            } == {(1, KeyslotStatus.ACTIVE.value), (2, KeyslotStatus.PENDING.value)}
            old_roots = unlock_key_hierarchy(
                db,
                credential="password-123",
                wrapping_path=WrappingPath.PASSWORD,
                scope=PayloadScope.FULL,
            )
            assert set(old_roots.frks) == {1, 2}


@pytest.mark.parametrize("scope", [PayloadScope.SOUL, PayloadScope.FS])
def test_change_password_respects_degraded_compartment_scope(scope) -> None:
    with managed_test_client(f"anima-password-scope-{scope.value}-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()

        make_scoped = _make_soul_only if scope is PayloadScope.SOUL else _make_filesystem_only
        make_scoped(
            password="password-123",
            recovery_phrase=str(registered["recoveryPhrase"]),
        )
        session = unlock_session_store.resolve(registered["unlockToken"])
        assert session is not None
        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            before_rows = db.query(SoulKeyslot).count()
            change_password_credential_generation(
                db,
                user,
                old_password="password-123",
                new_password="password-456",
                current_deks=session.deks,
                scope=scope,
            )
            unlocked = unlock_key_hierarchy(
                db,
                credential="password-456",
                wrapping_path=WrappingPath.PASSWORD,
                scope=scope,
            )
            after_rows = db.query(SoulKeyslot).count()

        if scope is PayloadScope.SOUL:
            assert unlocked.sqlcipher_key is not None
            assert unlocked.soul_domains
            assert unlocked.frks == {}
            assert after_rows > before_rows
        else:
            assert unlocked.sqlcipher_key is None
            assert unlocked.soul_domains == {}
            assert unlocked.frks
            assert after_rows == before_rows


@pytest.mark.parametrize("boundary", list(CredentialBoundary))
def test_password_generation_recovers_at_every_durable_boundary(boundary) -> None:
    with managed_test_client(f"anima-corefs-password-crash-{boundary.value}-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()
        session = unlock_session_store.resolve(registered["unlockToken"])
        assert session is not None

        def fail_at(current: CredentialBoundary) -> None:
            if current is boundary:
                raise RuntimeError(f"injected crash after {current.value}")

        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            with pytest.raises(RuntimeError, match="injected crash"):
                change_password_credential_generation(
                    db,
                    user,
                    old_password="password-123",
                    new_password="password-456",
                    current_deks=session.deks,
                    failure_injector=fail_at,
                )

        activated = boundary in {
            CredentialBoundary.MANIFEST_ACTIVATED,
            CredentialBoundary.SOUL_PROMOTED,
            CredentialBoundary.ACTIVE_REOPEN_VERIFIED,
        }
        clear_sqlcipher_key()
        dispose_all_user_engines()
        old_login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        clear_sqlcipher_key()
        dispose_all_user_engines()
        new_login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-456"},
        )
        if activated:
            assert old_login.status_code == 401
            assert new_login.status_code == 200
        else:
            assert old_login.status_code == 200
            assert new_login.status_code == 401


def test_password_generation_retry_replaces_pre_activation_pending_rows() -> None:
    with managed_test_client("anima-corefs-password-retry-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()
        session = unlock_session_store.resolve(registered["unlockToken"])
        assert session is not None

        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            with pytest.raises(RuntimeError):
                change_password_credential_generation(
                    db,
                    user,
                    old_password="password-123",
                    new_password="discarded-password",
                    current_deks=session.deks,
                    failure_injector=lambda current: (
                        (_ for _ in ()).throw(RuntimeError("crash"))
                        if current is CredentialBoundary.MANIFEST_PENDING_DURABLE
                        else None
                    ),
                )

        changed = client.post(
            "/api/auth/change-password",
            headers={"x-anima-unlock": registered["unlockToken"]},
            json={"oldPassword": "password-123", "newPassword": "password-456"},
        )
        assert changed.status_code == 200
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest["active_password_credential_generation"] == 2
