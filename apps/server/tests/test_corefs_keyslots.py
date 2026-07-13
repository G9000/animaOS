from __future__ import annotations

import json
from uuid import UUID

import pytest
from anima_server.config import settings
from anima_server.db import dispose_all_user_engines
from anima_server.db.session import get_user_session_factory
from anima_server.models import SoulKeyslot, User
from anima_server.services.core import (
    ensure_core_manifest,
    get_manifest_path,
    get_owner_id,
    get_wrapped_sqlcipher_key,
    store_user_index_entry,
    update_core_manifest,
)
from anima_server.services.corefs.credentials import (
    CredentialBoundary,
    change_password_credential_generation,
)
from anima_server.services.corefs.crypto import (
    manifest_keyslot_aad,
    soul_keyslot_aad,
    unwrap_keyslot_secret,
    wrap_keyslot_secret,
)
from anima_server.services.corefs.keyslots import (
    _record_from_payload,
    _record_to_payload,
    unlock_key_hierarchy,
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


def test_soul_keyslots_are_owner_scoped_and_keep_credential_status_history() -> None:
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
        db.add(SoulKeyslot(**common, status="active"))
        db.add(SoulKeyslot(**common, status="decrypt-only"))
        db.commit()

        db.add(SoulKeyslot(**common, status="active"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_keyslot_wrapping_uses_distinct_exact_aad_contracts() -> None:
    manifest_aad = manifest_keyslot_aad(
        core_id="019f-core",
        owner_id="019f-owner",
        purpose=KeyPurpose.FILESYSTEM_ROOT,
        key_version=1,
        wrapping_path=WrappingPath.PASSWORD,
    )
    soul_aad = soul_keyslot_aad(
        core_id="019f-core",
        owner_id="019f-owner",
        domain="memories",
        key_version=1,
        wrapping_path=WrappingPath.PASSWORD,
    )

    assert manifest_aad == (
        b"anima-keyslot-v1:core=019f-core:owner=019f-owner:"
        b"purpose=filesystem-root:version=1:path=password"
    )
    assert soul_aad == (
        b"anima-soul-keyslot-v1:core=019f-core:owner=019f-owner:"
        b"domain=memories:version=1:path=password"
    )
    assert manifest_aad != soul_aad

    secret = bytes([0x37]) * 32
    wrapped = wrap_keyslot_secret("password-123", secret, manifest_aad)
    assert unwrap_keyslot_secret("password-123", wrapped, manifest_aad) == secret
    with pytest.raises(InvalidTag):
        unwrap_keyslot_secret("password-123", wrapped, soul_aad)


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
        password_slots = [slot for slot in manifest["keyslots"] if slot["wrapping_path"] == "password"]
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
        assert client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        ).status_code == 401
        assert client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-456"},
        ).status_code == 200


@pytest.mark.parametrize(
    ("scope", "retained_purpose"),
    [
        (PayloadScope.SOUL, "soul"),
        (PayloadScope.FS, "filesystem-root"),
    ],
)
def test_change_password_respects_degraded_compartment_scope(scope, retained_purpose) -> None:
    with managed_test_client(f"anima-password-scope-{scope.value}-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123", "name": "Alice"},
        ).json()

        def make_scoped(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = [
                {**slot, "scope": scope.value}
                for slot in manifest["keyslots"]
                if slot["purpose"] == retained_purpose
            ]

        update_core_manifest(make_scoped)
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
