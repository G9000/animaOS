from __future__ import annotations

from anima_server.config import settings
from anima_server.db.session import get_user_session_factory
from conftest import managed_test_client
from fastapi.testclient import TestClient
from sqlalchemy import text


def _register_user(
    client: TestClient,
    *,
    username: str = "diarytest",
    name: str = "Diary Test",
) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pw123456", "name": name},
    )
    assert response.status_code == 201
    return response.json()


def test_diary_create_list_and_encrypts_text_fields() -> None:
    with managed_test_client("anima-diary-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post(
            "/api/diary",
            headers=headers,
            json={
                "userId": user_id,
                "entryDate": "2026-06-05",
                "title": "A private day",
                "body": "Today I wrote something only I should see.",
                "mood": "calm",
            },
        )

        assert response.status_code == 201
        created = response.json()
        assert created["title"] == "A private day"
        assert created["body"] == "Today I wrote something only I should see."
        assert created["mood"] == "calm"
        assert created["entryDate"] == "2026-06-05"
        assert created["attachments"] == []

        list_response = client.get(
            f"/api/diary?userId={user_id}&limit=10",
            headers=headers,
        )
        assert list_response.status_code == 200
        entries = list_response.json()
        assert len(entries) == 1
        assert entries[0]["title"] == "A private day"
        assert entries[0]["body"] == "Today I wrote something only I should see."

        with get_user_session_factory(user_id)() as db:
            raw = db.execute(
                text("select title, body, mood from diary_entries where user_id = :user_id"),
                {"user_id": user_id},
            ).mappings().one()

        assert raw["title"].startswith("enc2:")
        assert raw["body"].startswith("enc2:")
        assert raw["mood"].startswith("enc2:")
        assert raw["title"] != "A private day"
        assert "only I should see" not in raw["body"]


def test_diary_attachment_upload_stores_encrypted_blob_and_downloads_for_owner() -> None:
    payload = b"private audio bytes"

    with managed_test_client("anima-diary-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        create_response = client.post(
            "/api/diary",
            headers=headers,
            json={
                "userId": user_id,
                "entryDate": "2026-06-05",
                "body": "Voice note attached.",
            },
        )
        assert create_response.status_code == 201
        entry_id = int(create_response.json()["id"])

        upload_response = client.post(
            f"/api/diary/{entry_id}/attachments",
            headers=headers,
            data={"caption": "private voice note"},
            files={"file": ("voice note.wav", payload, "audio/wav")},
        )

        assert upload_response.status_code == 201
        attachment = upload_response.json()
        assert attachment["kind"] == "audio"
        assert attachment["mimeType"] == "audio/wav"
        assert attachment["filename"] == "voice note.wav"
        assert attachment["caption"] == "private voice note"
        assert attachment["sizeBytes"] == len(payload)
        attachment_id = int(attachment["id"])

        with get_user_session_factory(user_id)() as db:
            raw = db.execute(
                text(
                    "select original_filename, caption, storage_path "
                    "from diary_attachments where id = :attachment_id"
                ),
                {"attachment_id": attachment_id},
            ).mappings().one()

        assert raw["original_filename"].startswith("enc2:")
        assert raw["caption"].startswith("enc2:")
        blob_path = (settings.data_dir / str(raw["storage_path"])).resolve()
        assert blob_path.exists()
        assert blob_path.read_bytes() != payload
        assert payload not in blob_path.read_bytes()

        download_response = client.get(
            f"/api/diary/{entry_id}/attachments/{attachment_id}",
            headers=headers,
        )
        assert download_response.status_code == 200
        assert download_response.content == payload
        assert download_response.headers["content-type"].startswith("audio/wav")

        locked_response = client.get(f"/api/diary/{entry_id}/attachments/{attachment_id}")
        assert locked_response.status_code == 401


def test_diary_rejects_cross_user_access() -> None:
    with managed_test_client("anima-diary-test-") as client:
        owner = _register_user(client, username="diary-owner", name="Diary Owner")
        owner_id = int(owner["id"])
        owner_headers = {"x-anima-unlock": str(owner["unlockToken"])}

        response = client.post(
            "/api/diary",
            headers=owner_headers,
            json={"userId": owner_id, "entryDate": "2026-06-05", "body": "Mine."},
        )
        assert response.status_code == 201
        entry_id = int(response.json()["id"])

        list_response = client.get(
            f"/api/diary?userId={owner_id + 999}",
            headers=owner_headers,
        )
        assert list_response.status_code == 403

        delete_response = client.delete(f"/api/diary/{entry_id + 999}", headers=owner_headers)
        assert delete_response.status_code == 404
