from __future__ import annotations

import json
from unittest.mock import patch

import anima_server.api.routes.auth as auth_routes
from anima_server.config import settings
from anima_server.db import session as db_session
from anima_server.db.session import get_user_session_factory
from anima_server.models import SoulKeyslot, User
from anima_server.services.agent.llm import LLMInvocationError
from anima_server.services.core import get_manifest_path, update_core_manifest
from anima_server.services.corefs.keyslots import manifest_has_versioned_key_hierarchy
from anima_server.services.sessions import (
    get_active_dek,
    get_sqlcipher_key,
    set_sqlcipher_key,
    unlock_session_store,
)
from conftest import managed_test_client
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
        json={
            "username": username,
            "password": password,
            "name": name,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_register_creates_user_and_unlock_session() -> None:
    with managed_test_client("anima-auth-test-") as client:
        payload = _register_user(client, password="pw123456")

        assert payload["username"] == "alice"
        assert payload["name"] == "Alice"
        assert isinstance(payload["unlockToken"], str)
        assert payload["unlockToken"]
        assert get_active_dek(int(payload["id"])) is not None

        me_response = client.get(
            "/api/auth/me",
            headers={"x-anima-unlock": payload["unlockToken"]},
        )
        assert me_response.status_code == 200
        assert me_response.json()["username"] == "alice"


def test_login_me_and_logout_use_the_same_unlock_token() -> None:
    with managed_test_client("anima-auth-test-") as client:
        _register_user(client, password="pw123456")

        login_response = client.post(
            "/api/auth/login",
            json={"username": "Alice", "password": "pw123456"},
        )

        assert login_response.status_code == 200
        login_payload = login_response.json()
        assert login_payload["name"] == "Alice"
        assert login_payload["message"] == "Login successful"
        assert get_active_dek(int(login_payload["id"])) is not None

        headers = {"x-anima-unlock": login_payload["unlockToken"]}

        me_response = client.get("/api/auth/me", headers=headers)
        assert me_response.status_code == 200
        assert me_response.json()["name"] == "Alice"

        logout_response = client.post("/api/auth/logout", headers=headers)
        assert logout_response.status_code == 200
        assert logout_response.json() == {"success": True}

        locked_response = client.get("/api/auth/me", headers=headers)
        assert locked_response.status_code == 401
        assert locked_response.json() == {"error": "Session locked. Please sign in again."}


def test_versioned_login_hydrates_private_profile_after_keyslot_unlock() -> None:
    with managed_test_client("anima-auth-encrypted-profile-") as client:
        registered = _register_user(client, password="pw123456")
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert "user_index" not in manifest
        assert (settings.data_dir / "fs" / "VALIDATION_HEAD").is_file()

        with get_user_session_factory(int(registered["id"]))() as db:
            user = db.get(User, int(registered["id"]))
            assert user is not None
            user.display_name = "Unpublished SQL Name"
            db.commit()
        client.post(
            "/api/auth/logout",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
        )

        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "pw123456"},
        )
        assert login.status_code == 200
        assert login.json()["name"] == "Alice"


def test_logout_clears_sqlcipher_key_and_disposes_user_engines() -> None:
    with managed_test_client("anima-auth-test-") as client:
        register_payload = _register_user(client, password="pw123456")
        headers = {"x-anima-unlock": register_payload["unlockToken"]}

        set_sqlcipher_key(b"test-sqlcipher-key")

        me_response = client.get("/api/auth/me", headers=headers)
        assert me_response.status_code == 200

        user_engines = list(db_session._user_engines.values())
        assert len(user_engines) == 1

        dispose_calls: list[bool] = []
        original_dispose = user_engines[0].dispose

        def _tracked_dispose() -> None:
            dispose_calls.append(True)
            original_dispose()

        user_engines[0].dispose = _tracked_dispose  # type: ignore[method-assign]

        logout_response = client.post("/api/auth/logout", headers=headers)

        assert logout_response.status_code == 200
        assert get_sqlcipher_key() is None
        assert dispose_calls == [True]
        assert db_session._user_engines == {}


def test_login_rejects_invalid_credentials() -> None:
    with managed_test_client("anima-auth-test-") as client:
        _register_user(client, password="right-password")

        response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )

        assert response.status_code == 401
        assert response.json() == {"error": "Invalid credentials"}


def test_register_rejects_passwords_shorter_than_eight_characters() -> None:
    with managed_test_client("anima-auth-test-") as client:
        response = client.post(
            "/api/auth/register",
            json={
                "username": "shorty",
                "password": "pw1234",
                "name": "Shorty",
            },
        )

        assert response.status_code == 422


def test_login_rate_limits_after_five_failed_attempts_within_one_minute() -> None:
    original_time = auth_routes.time.time
    auth_routes._FAILED_LOGIN_ATTEMPTS.clear()
    try:
        auth_routes.time.time = lambda: 1_000.0
        with managed_test_client("anima-auth-test-") as client:
            _register_user(
                client,
                username="rate-limit-user",
                password="correct-password",
                name="Rate Limit User",
            )

            for _ in range(4):
                response = client.post(
                    "/api/auth/login",
                    json={
                        "username": "rate-limit-user",
                        "password": "wrong-password",
                    },
                )
                assert response.status_code == 401

            blocked_response = client.post(
                "/api/auth/login",
                json={
                    "username": "rate-limit-user",
                    "password": "wrong-password",
                },
            )

            assert blocked_response.status_code == 429
            assert blocked_response.headers["Retry-After"] == "60"
            assert blocked_response.json() == {
                "error": "Too many failed login attempts. Try again later.",
            }
    finally:
        auth_routes.time.time = original_time
        auth_routes._FAILED_LOGIN_ATTEMPTS.clear()


def test_login_rate_limit_expires_after_one_minute() -> None:
    original_time = auth_routes.time.time
    auth_routes._FAILED_LOGIN_ATTEMPTS.clear()
    now = 2_000.0
    try:
        auth_routes.time.time = lambda: now
        with managed_test_client("anima-auth-test-") as client:
            _register_user(
                client,
                username="rate-limit-reset",
                password="correct-password",
                name="Rate Limit Reset",
            )

            for _ in range(5):
                response = client.post(
                    "/api/auth/login",
                    json={
                        "username": "rate-limit-reset",
                        "password": "wrong-password",
                    },
                )

            assert response.status_code == 429

            now += 61.0
            reset_response = client.post(
                "/api/auth/login",
                json={
                    "username": "rate-limit-reset",
                    "password": "wrong-password",
                },
            )

            assert reset_response.status_code == 401
            assert "Retry-After" not in reset_response.headers
    finally:
        auth_routes.time.time = original_time
        auth_routes._FAILED_LOGIN_ATTEMPTS.clear()


def test_change_password_rewraps_existing_dek_and_rotates_unlock_token() -> None:
    with managed_test_client("anima-auth-test-") as client:
        register_payload = _register_user(
            client,
            username="change-me",
            password="old-password",
            name="Change Me",
        )
        user_id = int(register_payload["id"])
        old_unlock_token = str(register_payload["unlockToken"])
        old_dek = get_active_dek(user_id)
        assert old_dek is not None
        old_dek = bytes(bytearray(old_dek))

        change_response = client.post(
            "/api/auth/change-password",
            headers={"x-anima-unlock": old_unlock_token},
            json={
                "oldPassword": "old-password",
                "newPassword": "new-password",
            },
        )

        assert change_response.status_code == 200
        change_payload = change_response.json()
        assert change_payload["success"] is True
        assert change_payload["unlockToken"] != old_unlock_token
        assert get_active_dek(user_id) == old_dek

        old_token_me = client.get(
            "/api/auth/me",
            headers={"x-anima-unlock": old_unlock_token},
        )
        assert old_token_me.status_code == 401

        new_token_me = client.get(
            "/api/auth/me",
            headers={"x-anima-unlock": change_payload["unlockToken"]},
        )
        assert new_token_me.status_code == 200

        old_login = client.post(
            "/api/auth/login",
            json={"username": "change-me", "password": "old-password"},
        )
        assert old_login.status_code == 401

        new_login = client.post(
            "/api/auth/login",
            json={"username": "change-me", "password": "new-password"},
        )
        assert new_login.status_code == 200


def test_change_password_preserves_unversioned_env_passphrase_core(monkeypatch) -> None:
    monkeypatch.setattr(settings, "core_passphrase", "agent-managed-core-passphrase")

    with managed_test_client("anima-auth-legacy-password-change-") as client:
        registered = _register_user(
            client,
            username="legacy-change-me",
            password="old-password",
            name="Legacy Change Me",
        )
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert not manifest_has_versioned_key_hierarchy(manifest)

        changed = client.post(
            "/api/auth/change-password",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "oldPassword": "old-password",
                "newPassword": "new-password",
                "scope": "full",
            },
        )

        assert changed.status_code == 200
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "legacy-change-me", "password": "old-password"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "legacy-change-me", "password": "new-password"},
            ).status_code
            == 200
        )


def test_change_password_rewraps_unversioned_unified_core_root() -> None:
    with managed_test_client("anima-auth-legacy-unified-password-change-") as client:
        registered = _register_user(
            client,
            username="legacy-unified-change",
            password="old-password",
            name="Legacy Unified Change",
        )
        user_id = int(registered["id"])

        def remove_versioned_authority(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = []
            manifest.pop("active_password_credential_generation", None)
            manifest.pop("active_recovery_credential_generation", None)
            manifest.pop("frk_rotation", None)

        update_core_manifest(remove_versioned_authority)
        with get_user_session_factory(user_id)() as db:
            db.query(SoulKeyslot).delete()
            db.commit()

        changed = client.post(
            "/api/auth/change-password",
            headers={"x-anima-unlock": str(registered["unlockToken"])},
            json={
                "oldPassword": "old-password",
                "newPassword": "new-password",
                "scope": "full",
            },
        )

        assert changed.status_code == 200
        assert (
            client.post(
                "/api/auth/logout",
                headers={"x-anima-unlock": str(changed.json()["unlockToken"])},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "legacy-unified-change", "password": "new-password"},
            ).status_code
            == 200
        )


def test_change_password_rejects_wrong_old_password() -> None:
    with managed_test_client("anima-auth-test-") as client:
        register_payload = _register_user(
            client,
            username="stay-same",
            password="old-password",
            name="Stay Same",
        )

        change_response = client.post(
            "/api/auth/change-password",
            headers={"x-anima-unlock": register_payload["unlockToken"]},
            json={
                "oldPassword": "wrong-password",
                "newPassword": "new-password",
            },
        )

        assert change_response.status_code == 401
        assert change_response.json() == {"error": "Invalid credentials"}


def test_register_blocked_after_provisioning() -> None:
    with managed_test_client("anima-auth-test-") as client:
        _register_user(client, username="owner", password="pw123456", name="Owner")

        second = client.post(
            "/api/auth/register",
            json={"username": "intruder", "password": "pw123456", "name": "Nope"},
        )
        assert second.status_code == 403
        assert second.json() == {"error": "Core is already provisioned"}


def test_health_provisioned_flag_after_register() -> None:
    with managed_test_client("anima-auth-test-", invalidate_agent=False) as client:
        health_before = client.get("/api/health").json()
        assert health_before["provisioned"] is False

        _register_user(client)

        health_after = client.get("/api/health").json()
        assert health_after["provisioned"] is True


def test_create_ai_chat_hides_provider_error_details() -> None:
    async def _raise_provider_error(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise LLMInvocationError("provider leaked details")

    with (
        managed_test_client("anima-auth-test-") as client,
        patch("anima_server.services.creation_agent.handle_creation_turn", _raise_provider_error),
        patch.object(auth_routes.logger, "exception") as log_exception,
    ):
        response = client.post(
            "/api/auth/create-ai/chat",
            json={
                "ownerName": "Alice",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 503
    assert response.json() == {"error": "AI provider error occurred"}
    log_exception.assert_called_once()


def test_fs_locked_session_on_activated_core_fails_content_closed() -> None:
    """A session without CoreFS capability on an activated Core must not reach
    a legacy content branch (PR #148 review, P1): legacy writes would fork
    state the canonical catalog never sees. The reachable path is the retained
    legacy-credential upgrade window, whose Soul-only login must stay valid
    for the credential upgrade itself while content fails closed."""
    with managed_test_client("anima-auth-fs-locked-") as client:
        registered = _register_user(client, password="pw123456")
        user_id = int(registered["id"])
        headers = {"x-anima-unlock": str(registered["unlockToken"])}

        folder = client.post(
            "/api/diary/folders",
            headers=headers,
            json={"userId": user_id, "name": "Before"},
        )
        assert folder.status_code == 201, folder.text

        with get_user_session_factory(0)() as db:
            db.query(SoulKeyslot).delete()
            db.commit()

        def make_legacy(manifest: dict[str, object]) -> None:
            manifest["keyslots"] = []
            manifest.pop("active_password_credential_generation", None)
            manifest.pop("active_recovery_credential_generation", None)
            manifest.pop("frk_rotation", None)

        update_core_manifest(make_legacy)
        # Drop every canonical session so only the FS-locked login remains;
        # otherwise a still-unlocked session legitimately serves canonical
        # reads and the locked path is never exercised.
        unlock_session_store.clear()
        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "pw123456"},
        )
        assert login.status_code == 200, login.text
        locked_headers = {"x-anima-unlock": str(login.json()["unlockToken"])}

        # Authentication survives for the credential upgrade, but content
        # routes fail closed instead of writing or serving legacy state.
        assert client.get("/api/auth/me", headers=locked_headers).status_code == 200
        blocked_entry = client.post(
            "/api/diary",
            headers=locked_headers,
            json={
                "userId": user_id,
                "entryDate": "2026-08-23",
                "title": "Must not fork",
                "body": "This body must never reach legacy SQL.",
            },
        )
        assert blocked_entry.status_code in {400, 409}, blocked_entry.text
        blocked_task = client.post(
            "/api/tasks",
            headers=locked_headers,
            json={"userId": user_id, "text": "Must not fork", "priority": 1},
        )
        assert blocked_task.status_code in {400, 409}, blocked_task.text
        blocked_history = client.get(
            "/api/chat/history",
            headers=locked_headers,
            params={"userId": user_id, "limit": 10},
        )
        assert blocked_history.status_code in {400, 409}, blocked_history.text

        # Asset/document/knowledge READS must also fail closed rather than
        # falling back to Runtime rows and plaintext files, which could
        # resurface content canonical state superseded or deleted.
        blocked_knowledge = client.get(
            "/api/knowledge/sources",
            headers=locked_headers,
            params={"userId": user_id},
        )
        assert blocked_knowledge.status_code == 409, blocked_knowledge.text
        assert (
            blocked_knowledge.json()["details"]["code"]
            == "corefs_content_authority_unavailable"
        )
