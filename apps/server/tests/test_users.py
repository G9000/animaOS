from __future__ import annotations

from anima_server.services.storage import get_user_data_dir
from conftest import managed_test_client
from fastapi.testclient import TestClient


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "pw123456", "name": "Alice"},
    )
    assert response.status_code == 201
    return response.json()


def test_get_user_requires_matching_unlock_session() -> None:
    with managed_test_client("anima-users-test-") as client:
        user = _register_user(client)

        unauthorized = client.get(f"/api/users/{user['id']}")
        assert unauthorized.status_code == 401
        assert unauthorized.json() == {
            "error": "Session locked. Please sign in again."}

        response = client.get(
            f"/api/users/{user['id']}",
            headers={"x-anima-unlock": user["unlockToken"]},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == user["id"]
        assert payload["username"] == "alice"
        assert payload["name"] == "Alice"
        assert payload["gender"] is None


def test_update_user_updates_profile_fields() -> None:
    with managed_test_client("anima-users-test-") as client:
        user = _register_user(client)
        token = str(user["unlockToken"])

        response = client.put(
            f"/api/users/{user['id']}",
            headers={"x-anima-unlock": token},
            json={
                "name": "Alice Updated",
                "gender": "female",
                "age": 29,
                "birthday": "1996-03-12",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "Alice Updated"
        assert payload["gender"] == "female"
        assert payload["age"] == 29
        assert payload["birthday"] == "1996-03-12"


def test_delete_user_removes_database_row_and_files() -> None:
    with managed_test_client("anima-users-test-") as client:
        user = _register_user(client)
        token = str(user["unlockToken"])
        user_dir = get_user_data_dir(int(user["id"]))
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "profile.txt").write_text("exists", encoding="utf-8")

        response = client.delete(
            f"/api/users/{user['id']}",
            headers={"x-anima-unlock": token},
        )

        assert response.status_code == 200
        assert response.json() == {"message": "User deleted"}
        assert not user_dir.exists()

        login_response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "pw123456"},
        )
        assert login_response.status_code == 401
