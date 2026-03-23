from __future__ import annotations

from unittest.mock import patch

import anima_server.api.routes.auth as auth_routes
from anima_server.db import session as db_session
from anima_server.services.agent.llm import LLMInvocationError
from anima_server.services.sessions import get_active_dek, get_sqlcipher_key, set_sqlcipher_key
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
