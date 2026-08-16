from __future__ import annotations

from anima_server.services.corefs.diary_migration import (
    migration_opaque_id,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.formats import decode_account_profile_document
from anima_server.services.sessions import unlock_session_store
from anima_server.services.storage import get_user_data_dir
from conftest import managed_test_client
from fastapi.testclient import TestClient


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "pw123456", "name": "Alice"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_get_user_requires_matching_unlock_session() -> None:
    with managed_test_client("anima-users-test-") as client:
        user = _register_user(client)
        unauthorized = client.get(f"/api/users/{user['id']}")
        assert unauthorized.status_code == 401

        response = client.get(
            f"/api/users/{user['id']}",
            headers={"x-anima-unlock": str(user["unlockToken"])},
        )
        assert response.status_code == 200
        assert response.json()["username"] == "alice"
        assert response.json()["name"] == "Alice"


def test_greenfield_account_profile_and_avatar_are_corefs_authoritative() -> None:
    with managed_test_client("anima-users-greenfield-") as client:
        user = _register_user(client)
        user_id = int(user["id"])
        token = str(user["unlockToken"])
        headers = {"x-anima-unlock": token}
        session = unlock_session_store.resolve(token)
        assert session is not None
        assert session.content_authority is not None
        assert session.content_authority["state"] == "authoritative"

        updated = client.put(
            f"/api/users/{user_id}",
            headers=headers,
            json={
                "username": "renamed",
                "name": "Canonical Name",
                "gender": "other",
                "age": 34,
                "birthday": "1992-05-06",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["username"] == "renamed"
        assert client.get("/api/auth/me", headers=headers).json()["name"] == ("Canonical Name")

        setup = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=headers,
            json={"agentName": "Anima"},
        )
        assert setup.status_code == 200, setup.text
        assert setup.json()["setupComplete"] is True

        avatar_bytes = b"\x89PNG\r\n\x1a\ncanonical-agent-avatar"
        avatar = client.post(
            f"/api/consciousness/{user_id}/agent-profile/avatar",
            headers=headers,
            files={"file": ("agent.png", avatar_bytes, "image/png")},
        )
        assert avatar.status_code == 200, avatar.text
        fetched_avatar = client.get(
            f"/api/consciousness/{user_id}/agent-profile/avatar", headers=headers
        )
        assert fetched_avatar.status_code == 200
        assert fetched_avatar.content == avatar_bytes
        assert not (get_user_data_dir(user_id) / "avatars" / "agent.png").exists()

        account_item = next(
            item
            for item in read_prepared_writing_snapshot(session=session).objects
            if item.kind == "account-profile"
        )
        account = decode_account_profile_document(
            read_prepared_writing_body(session=session, item=account_item)
        )
        assert account.username == "renamed"
        assert account.display_name == "Canonical Name"
        assert account.setup_complete is True

        avatar_item = next(
            item
            for item in read_prepared_writing_snapshot(session=session).objects
            if item.stable_id == migration_opaque_id("identity-avatar", "agent-profile")
        )
        assert read_prepared_writing_body(session=session, item=avatar_item) == avatar_bytes

        removed = client.delete(
            f"/api/consciousness/{user_id}/agent-profile/avatar", headers=headers
        )
        assert removed.status_code == 200, removed.text
        assert (
            client.get(
                f"/api/consciousness/{user_id}/agent-profile/avatar", headers=headers
            ).status_code
            == 404
        )


def test_authoritative_account_deletion_is_restart_gated() -> None:
    with managed_test_client("anima-users-delete-") as client:
        user = _register_user(client)
        user_id = int(user["id"])
        token = str(user["unlockToken"])
        response = client.delete(f"/api/users/{user_id}", headers={"x-anima-unlock": token})
        assert response.status_code == 200, response.text
        assert response.json()["message"] == ("Whole-Core account deletion scheduled for restart")
        assert response.json()["restartRequired"] is True
        assert isinstance(response.json()["deletionId"], str)
        assert (
            client.get(f"/api/users/{user_id}", headers={"x-anima-unlock": token}).status_code
            == 401
        )
