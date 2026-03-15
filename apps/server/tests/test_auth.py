from __future__ import annotations

from fastapi.testclient import TestClient

from anima_server.services.sessions import get_active_dek
from conftest import managed_test_client


def _register_user(
    client: TestClient,
    *,
    username: str = "alice",
    password: str = "pw1234",
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
        payload = _register_user(client, password="pw123")

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
        _register_user(client, password="pw123")

        login_response = client.post(
            "/api/auth/login",
            json={"username": "Alice", "password": "pw123"},
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


def test_login_rejects_invalid_credentials() -> None:
    with managed_test_client("anima-auth-test-") as client:
        _register_user(client, password="right-password")

        response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )

        assert response.status_code == 401
        assert response.json() == {"error": "Invalid credentials"}


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
