from __future__ import annotations

import hashlib

from anima_server.db.session import get_user_session_factory
from anima_server.services.sessions import unlock_session_store
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
    assert response.status_code == 201, response.text
    return response.json()


def test_greenfield_diary_is_immediately_authoritative_and_corefs_only() -> None:
    with managed_test_client("anima-diary-greenfield-") as client:
        registered = _register_user(client, username="diary-greenfield")
        user_id = int(registered["id"])
        token = str(registered["unlockToken"])
        headers = {"x-anima-unlock": token}

        session = unlock_session_store.resolve(token)
        assert session is not None
        assert session.content_authority is not None
        assert session.content_authority["state"] == "authoritative"
        assert session.content_authority["authorityImmutable"] is True

        prepared = client.get("/api/diary/corefs-prepared", headers=headers)
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()["authoritative"] is True

        folder = client.post(
            "/api/diary/folders",
            headers=headers,
            json={"userId": user_id, "name": "Travel"},
        )
        assert folder.status_code == 201, folder.text
        folder_id = int(folder.json()["id"])

        created = client.post(
            "/api/diary",
            headers=headers,
            json={
                "userId": user_id,
                "entryDate": "2026-08-14",
                "title": "A private day",
                "body": "Today I wrote something only I should see.",
                "mood": "calm",
                "folderId": folder_id,
            },
        )
        assert created.status_code == 201, created.text
        entry_id = int(created.json()["id"])
        assert created.json()["body"] == "Today I wrote something only I should see."

        listed = client.get(f"/api/diary?userId={user_id}", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [entry["id"] for entry in listed.json()] == [entry_id]

        renamed = client.patch(
            f"/api/diary/folders/{folder_id}",
            headers=headers,
            json={"name": "Travel 2026"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "Travel 2026"

        updated = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"body": "Updated private day.", "clearFolder": True},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["body"] == "Updated private day."
        assert updated.json()["folderId"] is None

        attachment_bytes = b"\x89PNG\r\n\x1a\ncanonical-diary-attachment"
        attachment = client.post(
            f"/api/diary/{entry_id}/attachments",
            headers=headers,
            files={"file": ("canonical.png", attachment_bytes, "image/png")},
            data={"caption": "Canonical cover"},
        )
        assert attachment.status_code == 201, attachment.text
        attachment_id = int(attachment.json()["id"])
        assert attachment.json()["sha256"] == hashlib.sha256(attachment_bytes).hexdigest()

        downloaded = client.get(
            f"/api/diary/{entry_id}/attachments/{attachment_id}",
            headers=headers,
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == attachment_bytes

        cover = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"coverAttachmentId": attachment_id},
        )
        assert cover.status_code == 200, cover.text
        assert cover.json()["coverAttachmentId"] == attachment_id

        assert client.delete(f"/api/diary/folders/{folder_id}", headers=headers).status_code == 200
        assert client.delete(f"/api/diary/{entry_id}", headers=headers).status_code == 200

        with get_user_session_factory(user_id)() as db:
            counts = db.execute(
                text(
                    "select "
                    "(select count(*) from diary_folders where user_id = :user_id), "
                    "(select count(*) from diary_entries where user_id = :user_id), "
                    "(select count(*) from diary_attachments where user_id = :user_id)"
                ),
                {"user_id": user_id},
            ).one()
        assert counts == (0, 0, 0)


def test_diary_cover_attachment_must_be_own_image() -> None:
    with managed_test_client("anima-diary-cover-") as client:
        registered = _register_user(client, username="diary-cover")
        user_id = int(registered["id"])
        headers = {"x-anima-unlock": str(registered["unlockToken"])}

        first = client.post(
            "/api/diary",
            headers=headers,
            json={"userId": user_id, "entryDate": "2026-07-01", "body": "Photos."},
        )
        second = client.post(
            "/api/diary",
            headers=headers,
            json={"userId": user_id, "entryDate": "2026-07-02", "body": "Other."},
        )
        entry_id = int(first.json()["id"])
        other_entry_id = int(second.json()["id"])

        image_id = int(
            client.post(
                f"/api/diary/{entry_id}/attachments",
                headers=headers,
                files={"file": ("photo.png", b"png", "image/png")},
            ).json()["id"]
        )
        audio_id = int(
            client.post(
                f"/api/diary/{entry_id}/attachments",
                headers=headers,
                files={"file": ("clip.wav", b"audio", "audio/wav")},
            ).json()["id"]
        )
        foreign_id = int(
            client.post(
                f"/api/diary/{other_entry_id}/attachments",
                headers=headers,
                files={"file": ("other.png", b"other", "image/png")},
            ).json()["id"]
        )

        assert (
            client.patch(
                f"/api/diary/{entry_id}", headers=headers, json={"coverAttachmentId": audio_id}
            ).status_code
            == 400
        )
        assert (
            client.patch(
                f"/api/diary/{entry_id}", headers=headers, json={"coverAttachmentId": foreign_id}
            ).status_code
            == 400
        )
        accepted = client.patch(
            f"/api/diary/{entry_id}", headers=headers, json={"coverAttachmentId": image_id}
        )
        assert accepted.status_code == 200
        assert accepted.json()["coverAttachmentId"] == image_id


def test_diary_rejects_cross_user_access() -> None:
    with managed_test_client("anima-diary-access-") as client:
        owner = _register_user(client, username="diary-owner", name="Diary Owner")
        owner_id = int(owner["id"])
        headers = {"x-anima-unlock": str(owner["unlockToken"])}
        created = client.post(
            "/api/diary",
            headers=headers,
            json={"userId": owner_id, "entryDate": "2026-06-05", "body": "Mine."},
        )
        assert created.status_code == 201
        entry_id = int(created.json()["id"])

        assert client.get(f"/api/diary?userId={owner_id + 999}", headers=headers).status_code == 403
        assert client.delete(f"/api/diary/{entry_id + 999}", headers=headers).status_code == 404
