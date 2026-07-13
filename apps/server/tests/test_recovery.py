from __future__ import annotations

import json
from collections.abc import Generator

import anima_core
import pytest
from anima_server.api.routes import auth as auth_routes
from anima_server.db.session import get_user_session_factory
from anima_server.models import SoulKeyslot, User
from anima_server.services.core import get_manifest_path, update_core_manifest
from anima_server.services.corefs.credentials import (
    CredentialBoundary,
    confirm_filesystem_recovery_credential,
    confirm_recovery_credential,
    prepare_filesystem_recovery_credential,
    prepare_recovery_credential,
)
from anima_server.services.corefs.keyslots import (
    _manifest_slot,
    manifest_has_versioned_key_hierarchy,
    unlock_key_hierarchy,
    unlock_manifest_key_hierarchy,
)
from anima_server.services.corefs.types import (
    KeyPurpose,
    KeyslotStatus,
    PayloadScope,
    WrappingPath,
)
from conftest import managed_test_client
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_failed_login_attempts() -> Generator[None, None, None]:
    auth_routes._FAILED_LOGIN_ATTEMPTS.clear()
    yield
    auth_routes._FAILED_LOGIN_ATTEMPTS.clear()


def _register_user(
    client: TestClient,
    *,
    username: str = "alice",
    password: str = "pw123456",
    name: str = "Alice",
) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "name": name},
    )
    assert response.status_code == 201
    return response.json()


def _provision_retained_frk(
    *,
    password: str,
    recovery_phrase: str,
    scope: PayloadScope = PayloadScope.FULL,
) -> None:
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
            {**slot, "scope": scope.value}
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


def test_register_returns_recovery_phrase() -> None:
    with managed_test_client("anima-recovery-test-") as client:
        payload = _register_user(client)

        assert "recoveryPhrase" in payload
        phrase = str(payload["recoveryPhrase"])
        words = phrase.split()
        assert len(words) == 12, f"Expected 12 words, got {len(words)}"
        # Each word should be lowercase alphabetic
        for word in words:
            assert word.isalpha(), f"Non-alpha word: {word}"


def test_recover_account_with_valid_phrase() -> None:
    with managed_test_client("anima-recovery-test-") as client:
        register_payload = _register_user(client, password="old-password")
        phrase = str(register_payload["recoveryPhrase"])

        # Recover with the phrase and a new password
        recover_response = client.post(
            "/api/auth/recover",
            json={"recoveryPhrase": phrase, "newPassword": "new-password"},
        )

        assert recover_response.status_code == 200
        recover_payload = recover_response.json()
        assert recover_payload["username"] == "alice"
        assert recover_payload["name"] == "Alice"
        assert recover_payload["message"] == "Account recovered successfully"
        assert "unlockToken" in recover_payload

        # Old password should no longer work
        old_login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "old-password"},
        )
        assert old_login.status_code == 401

        # New password should work
        new_login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "new-password"},
        )
        assert new_login.status_code == 200


def test_recover_with_wrong_phrase_fails() -> None:
    with managed_test_client("anima-recovery-test-") as client:
        _register_user(client)

        recover_response = client.post(
            "/api/auth/recover",
            json={
                "recoveryPhrase": "wrong words here that are not the right phrase at all nope",
                "newPassword": "new-password",
            },
        )

        assert recover_response.status_code == 401


def test_recover_then_login_and_use_api() -> None:
    with managed_test_client("anima-recovery-test-") as client:
        register_payload = _register_user(client, password="original-pw")
        phrase = str(register_payload["recoveryPhrase"])

        # Recover
        recover_response = client.post(
            "/api/auth/recover",
            json={"recoveryPhrase": phrase, "newPassword": "recovered-pw"},
        )
        assert recover_response.status_code == 200
        unlock_token = recover_response.json()["unlockToken"]

        # Use the unlock token to access /me
        me_response = client.get(
            "/api/auth/me",
            headers={"x-anima-unlock": unlock_token},
        )
        assert me_response.status_code == 200
        assert me_response.json()["username"] == "alice"


def test_recovery_credential_prepare_keeps_old_active_until_typed_back_confirmation() -> None:
    with managed_test_client("anima-recovery-replacement-") as client:
        registered = _register_user(client, password="password-123")
        old_phrase = str(registered["recoveryPhrase"])

        response = client.post(
            "/api/auth/recovery-credential/prepare",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "currentRecoveryPhrase": old_phrase,
                "currentPassword": "password-123",
                "scope": "full",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        new_phrase = str(payload["recoveryPhrase"])
        assert new_phrase != old_phrase
        assert len(new_phrase.split()) == 12
        assert payload["pendingGeneration"] == 2
        assert payload["scope"] == "full"

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest["active_recovery_credential_generation"] == 1
        recovery_slots = [
            slot for slot in manifest["keyslots"] if slot["wrapping_path"] == "recovery"
        ]
        assert {(slot["credential_generation"], slot["status"]) for slot in recovery_slots} == {
            (1, "active"),
            (2, "pending"),
        }
        assert new_phrase not in get_manifest_path().read_text(encoding="utf-8")

        with get_user_session_factory(0)() as db:
            old_unlocked = unlock_key_hierarchy(
                db,
                credential=old_phrase,
                wrapping_path=WrappingPath.RECOVERY,
                scope=PayloadScope.FULL,
            )
            assert old_unlocked.frks
            with pytest.raises(InvalidTag):
                unlock_key_hierarchy(
                    db,
                    credential=new_phrase,
                    wrapping_path=WrappingPath.RECOVERY,
                    scope=PayloadScope.FULL,
                )

        confirmed = client.post(
            "/api/auth/recovery-credential/confirm",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "recoveryPhrase": new_phrase,
                "pendingGeneration": 2,
                "scope": "full",
                "currentPassword": "password-123",
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json() == {"success": True}

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest["active_recovery_credential_generation"] == 2
        recovery_slots = [
            slot for slot in manifest["keyslots"] if slot["wrapping_path"] == "recovery"
        ]
        assert {(slot["credential_generation"], slot["status"]) for slot in recovery_slots} == {
            (1, "decrypt-only"),
            (2, "active"),
        }
        with get_user_session_factory(0)() as db:
            unlocked = unlock_key_hierarchy(
                db,
                credential=new_phrase,
                wrapping_path=WrappingPath.RECOVERY,
                scope=PayloadScope.FULL,
            )
            assert unlocked.sqlcipher_key is not None
            assert unlocked.soul_domains
            assert unlocked.frks


def test_recovery_confirmation_requires_current_password() -> None:
    with managed_test_client("anima-recovery-confirm-password-contract-") as client:
        registered = _register_user(client, password="password-123")
        prepared = client.post(
            "/api/auth/recovery-credential/prepare",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "currentRecoveryPhrase": registered["recoveryPhrase"],
                "currentPassword": "password-123",
                "scope": "full",
            },
        ).json()

        response = client.post(
            "/api/auth/recovery-credential/confirm",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "recoveryPhrase": prepared["recoveryPhrase"],
                "pendingGeneration": prepared["pendingGeneration"],
                "scope": prepared["scope"],
            },
        )

        assert response.status_code == 422


def test_recovery_confirmation_is_retryable_after_manifest_activation_crash(monkeypatch) -> None:
    replacement = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    monkeypatch.setattr(
        "anima_server.services.corefs.credentials.generate_recovery_phrase",
        lambda: replacement,
    )
    with managed_test_client("anima-recovery-confirm-retry-") as client:
        registered = _register_user(client, password="password-123")
        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            prepared = prepare_recovery_credential(
                db,
                user,
                current_recovery_phrase=str(registered["recoveryPhrase"]),
                current_password="password-123",
            )
            with pytest.raises(RuntimeError, match="manifest-activated"):
                confirm_recovery_credential(
                    db,
                    user,
                    recovery_phrase=prepared.recovery_phrase,
                    pending_generation=prepared.pending_generation,
                    scope=prepared.scope,
                    current_password="password-123",
                    failure_injector=lambda boundary: (
                        (_ for _ in ()).throw(RuntimeError("manifest-activated"))
                        if boundary is CredentialBoundary.MANIFEST_ACTIVATED
                        else None
                    ),
                )
            confirm_recovery_credential(
                db,
                user,
                recovery_phrase=prepared.recovery_phrase,
                pending_generation=prepared.pending_generation,
                scope=prepared.scope,
                current_password="password-123",
            )
            unlocked = unlock_key_hierarchy(
                db,
                credential=replacement,
                wrapping_path=WrappingPath.RECOVERY,
                scope=PayloadScope.FULL,
            )
            assert unlocked.frks


def test_recovery_confirmation_rejects_missing_retained_frk_before_activation() -> None:
    with managed_test_client("anima-recovery-retained-frk-") as client:
        registered = _register_user(client, password="password-123")
        old_phrase = str(registered["recoveryPhrase"])
        _provision_retained_frk(
            password="password-123",
            recovery_phrase=old_phrase,
        )

        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            prepared = prepare_recovery_credential(
                db,
                user,
                current_recovery_phrase=old_phrase,
                current_password="password-123",
            )

            def remove_retained_pending_slot(value: dict[str, object]) -> None:
                value["keyslots"] = [
                    slot
                    for slot in value["keyslots"]
                    if not (
                        slot["wrapping_path"] == WrappingPath.RECOVERY.value
                        and slot["credential_generation"] == prepared.pending_generation
                        and slot["status"] == KeyslotStatus.PENDING.value
                        and slot["frk_version"] == 1
                    )
                ]

            update_core_manifest(remove_retained_pending_slot)
            boundaries: list[CredentialBoundary] = []
            with pytest.raises(ValueError, match="incomplete Filesystem Root key set"):
                confirm_recovery_credential(
                    db,
                    user,
                    recovery_phrase=prepared.recovery_phrase,
                    pending_generation=prepared.pending_generation,
                    scope=prepared.scope,
                    current_password="password-123",
                    failure_injector=boundaries.append,
                )

            manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
            assert boundaries == []
            assert manifest["active_recovery_credential_generation"] == 1
            assert {
                (slot["credential_generation"], slot["status"])
                for slot in manifest["keyslots"]
                if slot["wrapping_path"] == WrappingPath.RECOVERY.value
            } == {(1, KeyslotStatus.ACTIVE.value), (2, KeyslotStatus.PENDING.value)}
            assert {
                (row.credential_generation, row.status)
                for row in db.query(SoulKeyslot).filter_by(
                    wrapping_path=WrappingPath.RECOVERY.value
                )
            } == {(1, KeyslotStatus.ACTIVE.value), (2, KeyslotStatus.PENDING.value)}

            old_roots = unlock_key_hierarchy(
                db,
                credential=old_phrase,
                wrapping_path=WrappingPath.RECOVERY,
                scope=PayloadScope.FULL,
            )
            assert set(old_roots.frks) == {1, 2}


def test_filesystem_recovery_confirmation_rejects_missing_retained_frk_before_activation() -> None:
    with managed_test_client("anima-fs-recovery-retained-frk-") as client:
        registered = _register_user(client, password="password-123")
        old_phrase = str(registered["recoveryPhrase"])
        _provision_retained_frk(
            password="password-123",
            recovery_phrase=old_phrase,
            scope=PayloadScope.FS,
        )
        prepared = prepare_filesystem_recovery_credential(
            current_password="password-123",
            current_recovery_phrase=old_phrase,
        )

        def remove_retained_pending_slot(value: dict[str, object]) -> None:
            value["keyslots"] = [
                slot
                for slot in value["keyslots"]
                if not (
                    slot["wrapping_path"] == WrappingPath.RECOVERY.value
                    and slot["credential_generation"] == prepared.pending_generation
                    and slot["status"] == KeyslotStatus.PENDING.value
                    and slot["frk_version"] == 1
                )
            ]

        update_core_manifest(remove_retained_pending_slot)
        boundaries: list[CredentialBoundary] = []
        with pytest.raises(ValueError, match="incomplete Filesystem Root key set"):
            confirm_filesystem_recovery_credential(
                recovery_phrase=prepared.recovery_phrase,
                pending_generation=prepared.pending_generation,
                failure_injector=boundaries.append,
            )

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert boundaries == []
        assert manifest["active_recovery_credential_generation"] == 1
        assert {
            (slot["credential_generation"], slot["status"])
            for slot in manifest["keyslots"]
            if slot["wrapping_path"] == WrappingPath.RECOVERY.value
        } == {(1, KeyslotStatus.ACTIVE.value), (2, KeyslotStatus.PENDING.value)}
        old_roots = unlock_manifest_key_hierarchy(
            credential=old_phrase,
            wrapping_path=WrappingPath.RECOVERY,
            expected_scope=PayloadScope.FS,
        )
        assert set(old_roots.frks) == {1, 2}


def test_recovery_prepare_explicitly_upgrades_complete_legacy_account() -> None:
    with managed_test_client("anima-recovery-legacy-upgrade-") as client:
        registered = _register_user(client, password="password-123")
        old_phrase = str(registered["recoveryPhrase"])

        with get_user_session_factory(0)() as db:
            db.query(SoulKeyslot).delete()
            db.commit()

        def make_legacy(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = []
            manifest.pop("active_password_credential_generation", None)
            manifest.pop("active_recovery_credential_generation", None)
            manifest.pop("frk_rotation", None)

        update_core_manifest(make_legacy)
        prepared_response = client.post(
            "/api/auth/recovery-credential/prepare",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "currentRecoveryPhrase": old_phrase,
                "currentPassword": "password-123",
                "scope": "full",
            },
        )
        assert prepared_response.status_code == 200
        prepared = prepared_response.json()
        assert prepared["pendingGeneration"] == 1
        pending_manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert {slot["status"] for slot in pending_manifest["keyslots"]} == {
            KeyslotStatus.PENDING.value
        }
        assert not manifest_has_versioned_key_hierarchy(pending_manifest)

        before_confirm_login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert before_confirm_login.status_code == 200

        confirmed = client.post(
            "/api/auth/recovery-credential/confirm",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "recoveryPhrase": prepared["recoveryPhrase"],
                "pendingGeneration": 1,
                "scope": "full",
                "currentPassword": "password-123",
            },
        )
        assert confirmed.status_code == 200

        with get_user_session_factory(0)() as db:
            recovered = unlock_key_hierarchy(
                db,
                credential=prepared["recoveryPhrase"],
                wrapping_path=WrappingPath.RECOVERY,
                scope=PayloadScope.FULL,
            )
            password = unlock_key_hierarchy(
                db,
                credential="password-123",
                wrapping_path=WrappingPath.PASSWORD,
                scope=PayloadScope.FULL,
            )
            assert recovered.frks
            assert password.frks == recovered.frks
            assert password.soul_domains == recovered.soul_domains


def test_legacy_confirm_rejects_missing_pending_password_root_before_activation() -> None:
    with managed_test_client("anima-recovery-legacy-password-tamper-") as client:
        registered = _register_user(client, password="password-123")
        old_phrase = str(registered["recoveryPhrase"])
        with get_user_session_factory(0)() as db:
            db.query(SoulKeyslot).delete()
            db.commit()

        def make_legacy(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = []
            manifest.pop("active_password_credential_generation", None)
            manifest.pop("active_recovery_credential_generation", None)
            manifest.pop("frk_rotation", None)

        update_core_manifest(make_legacy)
        prepared = client.post(
            "/api/auth/recovery-credential/prepare",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "currentRecoveryPhrase": old_phrase,
                "currentPassword": "password-123",
                "scope": "full",
            },
        ).json()

        def remove_pending_password_root(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = [
                slot
                for slot in manifest["keyslots"]
                if not (
                    slot["wrapping_path"] == WrappingPath.PASSWORD.value
                    and slot["purpose"] == KeyPurpose.FILESYSTEM_ROOT.value
                    and slot["status"] == KeyslotStatus.PENDING.value
                )
            ]

        update_core_manifest(remove_pending_password_root)
        confirmation = client.post(
            "/api/auth/recovery-credential/confirm",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "recoveryPhrase": prepared["recoveryPhrase"],
                "pendingGeneration": prepared["pendingGeneration"],
                "scope": prepared["scope"],
                "currentPassword": "password-123",
            },
        )
        assert confirmation.status_code == 401

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert "active_password_credential_generation" not in manifest
        assert "active_recovery_credential_generation" not in manifest
        assert "frk_rotation" not in manifest
        assert {slot["status"] for slot in manifest["keyslots"]} == {
            KeyslotStatus.PENDING.value
        }
        old_login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password-123"},
        )
        assert old_login.status_code == 200
        old_recovery = client.post(
            "/api/auth/recover",
            json={
                "recoveryPhrase": old_phrase,
                "newPassword": "recovered-password-123",
                "scope": "full",
            },
        )
        assert old_recovery.status_code == 200


def test_legacy_confirm_reopens_password_after_activation() -> None:
    with managed_test_client("anima-recovery-legacy-password-final-reopen-") as client:
        registered = _register_user(client, password="password-123")
        old_phrase = str(registered["recoveryPhrase"])
        with get_user_session_factory(0)() as db:
            db.query(SoulKeyslot).delete()
            db.commit()

        def make_legacy(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = []
            manifest.pop("active_password_credential_generation", None)
            manifest.pop("active_recovery_credential_generation", None)
            manifest.pop("frk_rotation", None)

        update_core_manifest(make_legacy)
        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            prepared = prepare_recovery_credential(
                db,
                user,
                current_recovery_phrase=old_phrase,
                current_password="password-123",
            )

            def remove_password_root_after_promotion(boundary: CredentialBoundary) -> None:
                if boundary is not CredentialBoundary.SOUL_PROMOTED:
                    return

                def remove_root(manifest: dict[str, object]) -> None:
                    manifest["keyslots"] = [
                        slot
                        for slot in manifest["keyslots"]
                        if not (
                            slot["wrapping_path"] == WrappingPath.PASSWORD.value
                            and slot["purpose"] == KeyPurpose.FILESYSTEM_ROOT.value
                            and slot["status"] == KeyslotStatus.ACTIVE.value
                        )
                    ]

                update_core_manifest(remove_root)

            with pytest.raises(
                ValueError,
                match="full scope requires both Soul and Filesystem Root keys",
            ):
                confirm_recovery_credential(
                    db,
                    user,
                    recovery_phrase=prepared.recovery_phrase,
                    pending_generation=prepared.pending_generation,
                    scope=prepared.scope,
                    current_password="password-123",
                    failure_injector=remove_password_root_after_promotion,
                )


@pytest.mark.parametrize("boundary", list(CredentialBoundary))
def test_recovery_replacement_recovers_at_every_durable_boundary(
    boundary,
    monkeypatch,
) -> None:
    replacement = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    monkeypatch.setattr(
        "anima_server.services.corefs.credentials.generate_recovery_phrase",
        lambda: replacement,
    )
    with managed_test_client(f"anima-recovery-crash-{boundary.value}-") as client:
        registered = _register_user(client, password="password-123")
        current = str(registered["recoveryPhrase"])

        def fail_at(current_boundary: CredentialBoundary) -> None:
            if current_boundary is boundary:
                raise RuntimeError(f"injected crash after {current_boundary.value}")

        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            prepare_boundaries = {
                CredentialBoundary.SOUL_PENDING_DURABLE,
                CredentialBoundary.MANIFEST_PENDING_DURABLE,
                CredentialBoundary.PENDING_REOPEN_VERIFIED,
            }
            if boundary in prepare_boundaries:
                with pytest.raises(RuntimeError, match="injected crash"):
                    prepare_recovery_credential(
                        db,
                        user,
                        current_recovery_phrase=current,
                        current_password="password-123",
                        failure_injector=fail_at,
                    )
            else:
                prepared = prepare_recovery_credential(
                    db,
                    user,
                    current_recovery_phrase=current,
                    current_password="password-123",
                )
                with pytest.raises(RuntimeError, match="injected crash"):
                    confirm_recovery_credential(
                        db,
                        user,
                        recovery_phrase=prepared.recovery_phrase,
                        pending_generation=prepared.pending_generation,
                        scope=prepared.scope,
                        current_password="password-123",
                        failure_injector=fail_at,
                    )

            activated = boundary in {
                CredentialBoundary.MANIFEST_ACTIVATED,
                CredentialBoundary.SOUL_PROMOTED,
                CredentialBoundary.ACTIVE_REOPEN_VERIFIED,
            }
            active_phrase = replacement if activated else current
            rejected_phrase = current if activated else replacement
            unlocked = unlock_key_hierarchy(
                db,
                credential=active_phrase,
                wrapping_path=WrappingPath.RECOVERY,
                scope=PayloadScope.FULL,
            )
            assert unlocked.sqlcipher_key is not None
            with pytest.raises(InvalidTag):
                unlock_key_hierarchy(
                    db,
                    credential=rejected_phrase,
                    wrapping_path=WrappingPath.RECOVERY,
                    scope=PayloadScope.FULL,
                )


@pytest.mark.parametrize(
    ("scope", "retained_purpose", "degraded_state"),
    [
        (PayloadScope.SOUL, "soul", "filesystem_missing"),
        (PayloadScope.FS, "filesystem-root", "recovery_only"),
    ],
)
def test_scoped_recovery_replacement_preserves_degraded_compartment(
    scope,
    retained_purpose,
    degraded_state,
) -> None:
    with managed_test_client(f"anima-recovery-scoped-{scope.value}-") as client:
        registered = _register_user(client, password="password-123")
        current = str(registered["recoveryPhrase"])

        def make_scoped(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = [
                {**slot, "scope": scope.value}
                for slot in manifest["keyslots"]
                if slot["purpose"] == retained_purpose
            ]
            manifest["degraded_state"] = degraded_state

        update_core_manifest(make_scoped)
        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            before_soul_rows = db.query(SoulKeyslot).count()
            prepared = prepare_recovery_credential(
                db,
                user,
                current_recovery_phrase=current,
                current_password="password-123",
                scope=scope,
            )
            confirm_recovery_credential(
                db,
                user,
                recovery_phrase=prepared.recovery_phrase,
                pending_generation=prepared.pending_generation,
                scope=prepared.scope,
                current_password="password-123",
            )
            unlocked = unlock_key_hierarchy(
                db,
                credential=prepared.recovery_phrase,
                wrapping_path=WrappingPath.RECOVERY,
                scope=scope,
            )
            after_soul_rows = db.query(SoulKeyslot).count()

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert manifest["degraded_state"] == degraded_state
        assert {slot["purpose"] for slot in manifest["keyslots"]} == {retained_purpose}
        if scope is PayloadScope.SOUL:
            assert unlocked.sqlcipher_key is not None
            assert unlocked.soul_domains
            assert unlocked.frks == {}
            assert after_soul_rows > before_soul_rows
        else:
            assert unlocked.sqlcipher_key is None
            assert unlocked.soul_domains == {}
            assert unlocked.frks
            assert after_soul_rows == before_soul_rows


def test_recovery_replacement_retry_replaces_pre_activation_pending_rows(monkeypatch) -> None:
    phrases = iter(
        [
            "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
            "legal winner thank year wave sausage worth useful legal winner thank yellow",
        ]
    )
    monkeypatch.setattr(
        "anima_server.services.corefs.credentials.generate_recovery_phrase",
        lambda: next(phrases),
    )
    with managed_test_client("anima-recovery-retry-") as client:
        registered = _register_user(client, password="password-123")
        current = str(registered["recoveryPhrase"])
        with get_user_session_factory(0)() as db:
            user = db.get(User, 0)
            assert user is not None
            with pytest.raises(RuntimeError):
                prepare_recovery_credential(
                    db,
                    user,
                    current_recovery_phrase=current,
                    current_password="password-123",
                    failure_injector=lambda boundary: (
                        (_ for _ in ()).throw(RuntimeError("crash"))
                        if boundary is CredentialBoundary.MANIFEST_PENDING_DURABLE
                        else None
                    ),
                )
            replacement = prepare_recovery_credential(
                db,
                user,
                current_recovery_phrase=current,
                current_password="password-123",
            )
            assert replacement.recovery_phrase.startswith("legal winner")
