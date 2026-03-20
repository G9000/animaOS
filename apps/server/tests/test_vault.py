from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from anima_server.db.session import get_user_session_factory
from anima_server.models import User
from anima_server.services.storage import get_user_data_dir
from anima_server.services.vault import decrypt_string, encrypt_string
from conftest import managed_test_client


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


def test_export_vault_requires_unlock_session() -> None:
    with managed_test_client("anima-vault-test-") as client:
        response = client.post("/api/vault/export",
                               json={"passphrase": "vault-pass"})

        assert response.status_code == 401
        assert response.json() == {
            "error": "Session locked. Please sign in again."}


def test_export_and_import_vault_restores_auth_and_files() -> None:
    with managed_test_client("anima-vault-test-") as client:
        alice = _register_user(client)

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
            json={"passphrase": "vault-pass",
                  "vault": export_payload["vault"]},
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

        assert (user_dir / "memory" /
                "entry.md").read_text(encoding="utf-8") == "hello from vault"

        stale_session_response = client.get(
            "/api/auth/me",
            headers={"x-anima-unlock": alice["unlockToken"]},
        )
        assert stale_session_response.status_code == 401

        login_response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "pw123456"},
        )
        assert login_response.status_code == 200


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
            json={"passphrase": "wrong-pass",
                  "vault": export_response.json()["vault"]},
        )

        assert import_response.status_code == 400
        assert import_response.json() == {
            "error": "Failed to decrypt vault. Check the passphrase and payload.",
        }


def test_import_vault_preserves_original_password_hash() -> None:
    with managed_test_client("anima-vault-test-") as client:
        user = _register_user(client, username="vault-user", password="pw123456", name="Vault User")
        user_id = int(user["id"])
        token = str(user["unlockToken"])

        with get_user_session_factory(user_id)() as db:
            original_user = db.get(User, user_id)
            assert original_user is not None
            original_password_hash = original_user.password_hash

        export_response = client.post(
            "/api/vault/export",
            headers={"x-anima-unlock": token},
            json={"passphrase": "vault-pass"},
        )
        assert export_response.status_code == 200

        envelope = json.loads(export_response.json()["vault"])
        plaintext = decrypt_string(envelope, "vault-pass")
        payload = json.loads(plaintext)
        payload["database"]["users"][0]["password_hash"] = "$argon2id$v=19$m=65536,t=3,p=4$invalid$invalid"
        tampered_vault = json.dumps(
            encrypt_string(
                json.dumps(payload),
                "vault-pass",
                aad=base64.b64decode(envelope["aad_b64"]),
            )
        )

        import_response = client.post(
            "/api/vault/import",
            headers={"x-anima-unlock": token},
            json={"passphrase": "vault-pass", "vault": tampered_vault},
        )
        assert import_response.status_code == 200

        with get_user_session_factory(user_id)() as db:
            imported_user = db.get(User, user_id)
            assert imported_user is not None
            assert imported_user.password_hash == original_password_hash

        stale_session_response = client.get(
            "/api/auth/me",
            headers={"x-anima-unlock": token},
        )
        assert stale_session_response.status_code == 401

        login_response = client.post(
            "/api/auth/login",
            json={"username": "vault-user", "password": "pw123456"},
        )
        assert login_response.status_code == 200

        bad_login_response = client.post(
            "/api/auth/login",
            json={"username": "vault-user", "password": "tampered-password"},
        )
        assert bad_login_response.status_code == 401


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("timeCost", 11, "Vault KDF timeCost exceeds maximum allowed value of 10."),
        (
            "memoryCostKiB",
            2_097_153,
            "Vault KDF memoryCostKiB exceeds maximum allowed value of 2097152.",
        ),
        (
            "parallelism",
            9,
            "Vault KDF parallelism exceeds maximum allowed value of 8.",
        ),
        ("keyLength", 31, "Vault KDF keyLength must be exactly 32."),
    ],
)
def test_decrypt_string_rejects_out_of_bounds_kdf_parameters(
    field: str,
    value: int,
    message: str,
) -> None:
    envelope = encrypt_string("secret", "vault-pass")
    envelope["kdf"][field] = value

    with pytest.raises(ValueError, match=message):
        decrypt_string(envelope, "vault-pass")


def test_encrypt_string_uses_checksum_and_decrypt_string_accepts_legacy_integrity() -> None:
    envelope = encrypt_string("secret", "vault-pass")

    assert "checksum" in envelope
    assert "integrity" not in envelope

    legacy_envelope = dict(envelope)
    legacy_envelope["integrity"] = legacy_envelope.pop("checksum")

    assert decrypt_string(legacy_envelope, "vault-pass") == "secret"
