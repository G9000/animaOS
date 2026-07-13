from __future__ import annotations

import json

import pytest
from anima_server.db.session import get_user_session_factory
from anima_server.models import SoulKeyslot, User
from anima_server.services.core import get_manifest_path, update_core_manifest
from anima_server.services.corefs.credentials import (
    CredentialBoundary,
    confirm_recovery_credential,
    prepare_recovery_credential,
)
from anima_server.services.corefs.keyslots import unlock_key_hierarchy
from anima_server.services.corefs.types import PayloadScope, WrappingPath
from conftest import managed_test_client
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient


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
            )
            unlocked = unlock_key_hierarchy(
                db,
                credential=replacement,
                wrapping_path=WrappingPath.RECOVERY,
                scope=PayloadScope.FULL,
            )
            assert unlocked.frks


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
