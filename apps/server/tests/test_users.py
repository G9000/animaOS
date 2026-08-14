from __future__ import annotations

import pytest
from anima_server.db.session import get_user_session_factory
from anima_server.models import AgentProfile, User
from anima_server.services.corefs import logical
from anima_server.services.corefs.cutover import (
    approve_validation_cutover,
    begin_migration,
    publish_validation_readonly,
    reconcile_cutover_authority,
)
from anima_server.services.corefs.diary_migration import (
    migration_opaque_id,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.formats import decode_account_profile_document
from anima_server.services.corefs.soul_relocation import relocate_owner_soul_database
from anima_server.services.sessions import unlock_session_store
from anima_server.services.storage import get_user_data_dir
from conftest import managed_test_client
from fastapi.testclient import TestClient
from sqlalchemy import select


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
        assert unauthorized.json() == {"error": "Session locked. Please sign in again."}

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
        assert response.json() == {
            "message": "User deleted",
            "restartRequired": False,
            "deletionId": None,
        }
        assert not user_dir.exists()

        login_response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "pw123456"},
        )
        assert login_response.status_code == 401


def test_post_cutover_account_profile_never_mutates_legacy_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with managed_test_client("anima-users-corefs-") as client:
        user = _register_user(client)
        user_id = int(user["id"])
        token = str(user["unlockToken"])
        headers = {"x-anima-unlock": token}

        legacy_update = client.put(
            f"/api/users/{user_id}",
            headers=headers,
            json={"name": "Legacy retained"},
        )
        assert legacy_update.status_code == 200, legacy_update.text
        legacy_avatar_bytes = b"\x89PNG\r\n\x1a\nretained-legacy-avatar"
        legacy_avatar = client.post(
            f"/api/consciousness/{user_id}/agent-profile/avatar",
            headers=headers,
            files={"file": ("legacy.png", legacy_avatar_bytes, "image/png")},
        )
        assert legacy_avatar.status_code == 200, legacy_avatar.text
        legacy_avatar_url = legacy_avatar.json()["avatarUrl"]

        session = unlock_session_store.resolve(token)
        assert session is not None
        selected = session.corefs_session.validation_snapshot(session.corefs_keys)
        begin_migration()
        relocate_owner_soul_database(user_id)
        publish_validation_readonly(
            generation=int(selected["generation"]),
            catalog_hash=str(selected["catalogHash"]),
        )
        approve_validation_cutover()
        logical.execute_mutation_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=logical.CoreFsValidationSnapshot(
                int(selected["generation"]), str(selected["catalogHash"])
            ),
            principal="user",
            mutation={"operation": "mkdir", "path": "Account activation proof"},
        )
        marker = reconcile_cutover_authority(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
        )
        assert marker is not None
        object.__setattr__(session, "content_authority", marker)

        def reject_legacy_path(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("post-cutover account touched legacy persistence")

        monkeypatch.setattr("anima_server.api.routes.users.get_user_by_id", reject_legacy_path)
        monkeypatch.setattr(
            "anima_server.api.routes.users.prepare_writing_source_catalog",
            reject_legacy_path,
        )
        monkeypatch.setattr(
            "anima_server.api.routes.consciousness.prepare_writing_source_catalog",
            reject_legacy_path,
        )
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
        assert updated.json()["name"] == "Canonical Name"

        fetched = client.get(f"/api/users/{user_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["username"] == "renamed"
        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        assert me.json()["name"] == "Canonical Name"

        setup = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=headers,
            json={"agentName": "Anima"},
        )
        assert setup.status_code == 200, setup.text
        assert setup.json()["setupComplete"] is True
        fetched_setup = client.get(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=headers,
        )
        assert fetched_setup.status_code == 200, fetched_setup.text
        assert fetched_setup.json()["setupComplete"] is True
        assert fetched_setup.json()["avatarUrl"] is None
        assert (
            client.get(
                f"/api/consciousness/{user_id}/agent-profile/avatar",
                headers=headers,
            ).status_code
            == 404
        )

        avatar_bytes = b"\x89PNG\r\n\x1a\ncanonical-agent-avatar"
        avatar = client.post(
            f"/api/consciousness/{user_id}/agent-profile/avatar",
            headers=headers,
            files={"file": ("agent.png", avatar_bytes, "image/png")},
        )
        assert avatar.status_code == 200, avatar.text
        assert avatar.json()["avatarUrl"] == (
            f"/consciousness/{user_id}/agent-profile/avatar"
        )
        avatar_response = client.get(
            f"/api/consciousness/{user_id}/agent-profile/avatar",
            headers=headers,
        )
        assert avatar_response.status_code == 200, avatar_response.text
        assert avatar_response.content == avatar_bytes
        assert avatar_response.headers["content-type"] == "image/png"
        profile_with_avatar = client.get(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=headers,
        )
        assert profile_with_avatar.status_code == 200, profile_with_avatar.text
        assert profile_with_avatar.json()["avatarUrl"] == avatar.json()["avatarUrl"]
        legacy_avatar_path = get_user_data_dir(user_id) / "avatars" / "agent.png"
        assert legacy_avatar_path.read_bytes() == legacy_avatar_bytes

        with get_user_session_factory(user_id)() as db:
            legacy = db.get(User, user_id)
            legacy_agent = db.scalar(select(AgentProfile).where(AgentProfile.user_id == user_id))
            assert legacy is not None
            assert legacy_agent is not None
            assert legacy.username == "alice"
            assert legacy.display_name == "Legacy retained"
            assert legacy_agent.setup_complete is False
            assert legacy_agent.avatar_url == legacy_avatar_url

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
        assert avatar_item.kind == "gallery-asset"
        assert read_prepared_writing_body(session=session, item=avatar_item) == avatar_bytes

        removed_avatar = client.delete(
            f"/api/consciousness/{user_id}/agent-profile/avatar",
            headers=headers,
        )
        assert removed_avatar.status_code == 200, removed_avatar.text
        assert removed_avatar.json()["avatarUrl"] is None
        assert (
            client.get(
                f"/api/consciousness/{user_id}/agent-profile/avatar",
                headers=headers,
            ).status_code
            == 404
        )
        profile_without_avatar = client.get(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=headers,
        )
        assert profile_without_avatar.status_code == 200, profile_without_avatar.text
        assert profile_without_avatar.json()["avatarUrl"] is None
        assert legacy_avatar_path.read_bytes() == legacy_avatar_bytes

        delete = client.delete(f"/api/users/{user_id}", headers=headers)
        assert delete.status_code == 200, delete.text
        assert delete.json()["message"] == (
            "Whole-Core account deletion scheduled for restart"
        )
        assert delete.json()["restartRequired"] is True
        assert isinstance(delete.json()["deletionId"], str)
        assert get_user_data_dir(user_id).is_dir()

        locked = client.get(f"/api/users/{user_id}", headers=headers)
        assert locked.status_code == 401
        login = client.post(
            "/api/auth/login",
            json={"username": "renamed", "password": "pw123456"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["name"] == "Canonical Name"
