from __future__ import annotations

import json

from fastapi.testclient import TestClient

from anima_server.db.session import get_user_session_factory
from anima_server.models import User
from anima_server.services.storage import get_user_data_dir
from conftest import managed_test_client


def _register_user(
    client: TestClient,
    *,
    username: str = "alice",
    password: str = "pw123",
    name: str = "Alice",
) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "name": name},
    )
    assert response.status_code == 201
    return response.json()


def test_export_vault_requires_unlock_session() -> None:
    with managed_test_client("anima-vault-test-") as client:
        response = client.post("/api/vault/export", json={"passphrase": "vault-pass"})

        assert response.status_code == 401
        assert response.json() == {"error": "Session locked. Please sign in again."}


def test_export_and_import_vault_restores_auth_and_files() -> None:
    with managed_test_client("anima-vault-test-") as client:
        alice = _register_user(client)
        _register_user(client, username="bob", password="otherpw", name="Bob")

        user_id = int(alice["id"])
        headers = {"x-anima-unlock": alice["unlockToken"]}
        user_dir = get_user_data_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "memory" / "entry.md").parent.mkdir(parents=True, exist_ok=True)
        (user_dir / "memory" / "entry.md").write_text("hello from vault", encoding="utf-8")

        export_response = client.post(
            "/api/vault/export",
            headers=headers,
            json={"passphrase": "vault-pass"},
        )

        assert export_response.status_code == 200
        export_payload = export_response.json()
        envelope = json.loads(export_payload["vault"])
        assert envelope["version"] == 2
        assert "Alice" not in export_payload["vault"]

        with get_user_session_factory(user_id)() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.display_name = "Changed"
            db.commit()

        (user_dir / "memory" / "entry.md").write_text("changed", encoding="utf-8")

        import_response = client.post(
            "/api/vault/import",
            headers=headers,
            json={"passphrase": "vault-pass", "vault": export_payload["vault"]},
        )

        assert import_response.status_code == 200
        import_payload = import_response.json()
        assert import_payload == {
            "status": "ok",
            "restoredUsers": 1,
            "restoredMemoryFiles": 1,
            "requiresReauth": True,
        }

        with get_user_session_factory(user_id)() as db:
            users = db.query(User).all()
            assert [record.username for record in users] == ["alice"]
            assert users[0].display_name == "Alice"

        assert (user_dir / "memory" / "entry.md").read_text(encoding="utf-8") == "hello from vault"

        stale_session_response = client.get(
            "/api/auth/me",
            headers={"x-anima-unlock": alice["unlockToken"]},
        )
        assert stale_session_response.status_code == 401

        login_response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "pw123"},
        )
        assert login_response.status_code == 200

        bob_login = client.post(
            "/api/auth/login",
            json={"username": "bob", "password": "otherpw"},
        )
        assert bob_login.status_code == 200


def test_import_vault_rejects_wrong_passphrase() -> None:
    with managed_test_client("anima-vault-test-") as client:
        user = _register_user(client)
        token = str(user["unlockToken"])

        export_response = client.post(
            "/api/vault/export",
            headers={"x-anima-unlock": token},
            json={"passphrase": "vault-pass"},
        )
        assert export_response.status_code == 200

        import_response = client.post(
            "/api/vault/import",
            headers={"x-anima-unlock": token},
            json={"passphrase": "wrong-pass", "vault": export_response.json()["vault"]},
        )

        assert import_response.status_code == 400
        assert import_response.json() == {
            "error": "Failed to decrypt vault. Check the passphrase and payload.",
        }
