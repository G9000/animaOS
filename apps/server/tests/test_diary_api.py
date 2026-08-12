from __future__ import annotations

from anima_server.config import settings
from anima_server.db.session import get_user_session_factory
from anima_server.services.corefs.diary_migration import (
    InactiveFolder,
    LegacyDiaryDraft,
    LegacyNote,
    build_inactive_diary_catalog,
    migration_opaque_id,
    prepare_diary_validation_catalog,
    read_prepared_writing_objects,
    read_prepared_writing_snapshot,
    resolve_prepared_role,
)
from anima_server.services.corefs.formats import decode_draft_document, decode_note_document
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
    assert response.status_code == 201
    return response.json()


def _writing_generation(user_id: int) -> int | None:
    with get_user_session_factory(user_id)() as db:
        return db.scalar(
            text("select generation from corefs_writing_source_state where user_id = :user_id"),
            {"user_id": user_id},
        )


def test_every_diary_api_writer_advances_the_source_generation() -> None:
    with managed_test_client("anima-diary-generation-") as client:
        reg = _register_user(client, username="diary-generation")
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}
        assert _writing_generation(user_id) is None

        folder_response = client.post(
            "/api/diary/folders",
            headers=headers,
            json={"userId": user_id, "name": "Generation"},
        )
        assert folder_response.status_code == 201
        folder_id = int(folder_response.json()["id"])
        generation = _writing_generation(user_id)
        assert generation == 1

        rename_response = client.patch(
            f"/api/diary/folders/{folder_id}",
            headers=headers,
            json={"name": "Generation renamed"},
        )
        assert rename_response.status_code == 200
        assert _writing_generation(user_id) == generation + 1
        generation += 1

        entry_response = client.post(
            "/api/diary",
            headers=headers,
            json={
                "userId": user_id,
                "entryDate": "2026-08-12",
                "body": "generation body",
                "folderId": folder_id,
            },
        )
        assert entry_response.status_code == 201
        entry_id = int(entry_response.json()["id"])
        assert _writing_generation(user_id) == generation + 1
        generation += 1

        update_response = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"body": "generation body changed"},
        )
        assert update_response.status_code == 200
        assert _writing_generation(user_id) == generation + 1
        generation += 1

        upload_response = client.post(
            f"/api/diary/{entry_id}/attachments",
            headers=headers,
            files={"file": ("generation.bin", b"generation", "application/octet-stream")},
        )
        assert upload_response.status_code == 201
        assert _writing_generation(user_id) == generation + 1
        generation += 1

        delete_entry_response = client.delete(f"/api/diary/{entry_id}", headers=headers)
        assert delete_entry_response.status_code == 200
        after_entry_delete = _writing_generation(user_id)
        assert after_entry_delete is not None and after_entry_delete >= generation + 2

        delete_folder_response = client.delete(f"/api/diary/folders/{folder_id}", headers=headers)
        assert delete_folder_response.status_code == 200
        assert _writing_generation(user_id) == after_entry_delete + 1


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


def test_diary_cover_attachment_must_be_own_image() -> None:
    with managed_test_client("anima-diary-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        create_response = client.post(
            "/api/diary",
            headers=headers,
            json={"userId": user_id, "entryDate": "2026-07-01", "body": "Photos from today."},
        )
        entry_id = int(create_response.json()["id"])
        assert create_response.json()["coverAttachmentId"] is None

        other_entry_response = client.post(
            "/api/diary",
            headers=headers,
            json={"userId": user_id, "entryDate": "2026-07-02", "body": "Different entry."},
        )
        other_entry_id = int(other_entry_response.json()["id"])

        image_upload = client.post(
            f"/api/diary/{entry_id}/attachments",
            headers=headers,
            files={"file": ("photo.png", b"fake-png-bytes", "image/png")},
        )
        image_attachment_id = int(image_upload.json()["id"])

        audio_upload = client.post(
            f"/api/diary/{entry_id}/attachments",
            headers=headers,
            files={"file": ("clip.wav", b"fake-audio-bytes", "audio/wav")},
        )
        audio_attachment_id = int(audio_upload.json()["id"])

        foreign_image_upload = client.post(
            f"/api/diary/{other_entry_id}/attachments",
            headers=headers,
            files={"file": ("other.png", b"other-png-bytes", "image/png")},
        )
        foreign_attachment_id = int(foreign_image_upload.json()["id"])

        # Non-image attachment can't become the cover.
        audio_cover_response = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"coverAttachmentId": audio_attachment_id},
        )
        assert audio_cover_response.status_code == 400

        # Attachment belonging to a different entry can't become the cover.
        foreign_cover_response = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"coverAttachmentId": foreign_attachment_id},
        )
        assert foreign_cover_response.status_code == 400

        # A same-entry image attachment is accepted.
        set_cover_response = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"coverAttachmentId": image_attachment_id},
        )
        assert set_cover_response.status_code == 200
        assert set_cover_response.json()["coverAttachmentId"] == image_attachment_id

        clear_cover_response = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"clearCover": True},
        )
        assert clear_cover_response.status_code == 200
        assert clear_cover_response.json()["coverAttachmentId"] is None


def test_diary_update_edits_fields_and_reencrypts() -> None:
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
                "title": "Original title",
                "body": "Original body.",
                "mood": "calm",
            },
        )
        assert create_response.status_code == 201
        entry_id = int(create_response.json()["id"])

        update_response = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={
                "entryDate": "2026-06-06",
                "title": "Updated title",
                "body": "Updated body.",
                "mood": "reflective",
            },
        )
        assert update_response.status_code == 200
        updated = update_response.json()
        assert updated["entryDate"] == "2026-06-06"
        assert updated["title"] == "Updated title"
        assert updated["body"] == "Updated body."
        assert updated["mood"] == "reflective"

        with get_user_session_factory(user_id)() as db:
            raw = db.execute(
                text("select title, body, mood from diary_entries where id = :entry_id"),
                {"entry_id": entry_id},
            ).mappings().one()
        assert raw["title"].startswith("enc2:")
        assert raw["body"].startswith("enc2:")
        assert raw["mood"].startswith("enc2:")

        clear_response = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"clearTitle": True, "clearMood": True},
        )
        assert clear_response.status_code == 200
        cleared = clear_response.json()
        assert cleared["title"] is None
        assert cleared["mood"] is None
        assert cleared["body"] == "Updated body."

        missing_response = client.patch(
            f"/api/diary/{entry_id + 999}",
            headers=headers,
            json={"title": "Nope"},
        )
        assert missing_response.status_code == 404


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


def test_diary_folders_crud_and_filing() -> None:
    with managed_test_client("anima-diary-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        empty_list_response = client.get(f"/api/diary/folders?userId={user_id}", headers=headers)
        assert empty_list_response.status_code == 200
        assert empty_list_response.json() == []

        create_folder_response = client.post(
            "/api/diary/folders",
            headers=headers,
            json={"userId": user_id, "name": "Travel"},
        )
        assert create_folder_response.status_code == 201
        folder = create_folder_response.json()
        assert folder["name"] == "Travel"
        assert folder["entryCount"] == 0
        folder_id = int(folder["id"])

        with get_user_session_factory(user_id)() as db:
            raw = db.execute(
                text("select name from diary_folders where id = :folder_id"),
                {"folder_id": folder_id},
            ).mappings().one()
        assert raw["name"].startswith("enc2:")

        # Entry can be filed into the folder at creation time.
        create_entry_response = client.post(
            "/api/diary",
            headers=headers,
            json={
                "userId": user_id,
                "entryDate": "2026-07-01",
                "body": "Landed in Tokyo.",
                "folderId": folder_id,
            },
        )
        assert create_entry_response.status_code == 201
        entry = create_entry_response.json()
        assert entry["folderId"] == folder_id
        entry_id = int(entry["id"])

        list_after_create = client.get(f"/api/diary/folders?userId={user_id}", headers=headers)
        assert list_after_create.json()[0]["entryCount"] == 1

        # Rename the folder.
        rename_response = client.patch(
            f"/api/diary/folders/{folder_id}",
            headers=headers,
            json={"name": "Travel 2026"},
        )
        assert rename_response.status_code == 200
        assert rename_response.json()["name"] == "Travel 2026"

        # An entry can also be filed/unfiled via update.
        second_entry_response = client.post(
            "/api/diary",
            headers=headers,
            json={"userId": user_id, "entryDate": "2026-07-02", "body": "Unfiled for now."},
        )
        second_entry_id = int(second_entry_response.json()["id"])
        assert second_entry_response.json()["folderId"] is None

        file_response = client.patch(
            f"/api/diary/{second_entry_id}",
            headers=headers,
            json={"folderId": folder_id},
        )
        assert file_response.status_code == 200
        assert file_response.json()["folderId"] == folder_id

        unfile_response = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"clearFolder": True},
        )
        assert unfile_response.status_code == 200
        assert unfile_response.json()["folderId"] is None

        # Filing into a folder that doesn't belong to the user fails.
        bad_folder_response = client.patch(
            f"/api/diary/{entry_id}",
            headers=headers,
            json={"folderId": folder_id + 999},
        )
        assert bad_folder_response.status_code == 400

        # Deleting a folder unfiles its entries rather than deleting them.
        delete_folder_response = client.delete(f"/api/diary/folders/{folder_id}", headers=headers)
        assert delete_folder_response.status_code == 200

        surviving_entry_response = client.get(f"/api/diary?userId={user_id}&limit=10", headers=headers)
        surviving = {e["id"]: e for e in surviving_entry_response.json()}
        assert surviving[second_entry_id]["folderId"] is None

        missing_folder_response = client.patch(
            f"/api/diary/folders/{folder_id + 999}",
            headers=headers,
            json={"name": "Nope"},
        )
        assert missing_folder_response.status_code == 404


def test_legacy_browser_draft_import_is_encrypted_verified_and_idempotent() -> None:
    with managed_test_client("anima-diary-draft-import-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        token = str(reg["unlockToken"])
        headers = {"x-anima-unlock": token}
        created = client.post(
            "/api/diary",
            headers=headers,
            json={
                "userId": user_id,
                "entryDate": "2026-08-02",
                "title": "Canonical",
                "body": "<p>legacy authority</p>",
                "mood": "calm",
            },
        )
        assert created.status_code == 201
        uploaded = client.post(
            f"/api/diary/{created.json()['id']}/attachments",
            headers=headers,
            files={"file": ("private.bin", b"native encrypted bytes", "application/octet-stream")},
        )
        assert uploaded.status_code == 201
        payload = {
            "userId": user_id,
            "draftId": f"anima:diary:draft:{user_id}:edit-{created.json()['id']}",
            "targetEntryId": created.json()["id"],
            "html": "<p>unsaved private draft</p>",
            "title": "Unsaved",
            "mood": "hopeful",
            "entryDate": "2026-08-03",
            "updatedAt": "2026-08-02T05:00:00Z",
        }

        first = client.post("/api/diary/drafts/import", headers=headers, json=payload)
        second = client.post("/api/diary/drafts/import", headers=headers, json=payload)
        assert first.status_code == second.status_code == 200
        assert first.json()["verified"] is True
        assert first.json()["authoritative"] is False
        assert second.json()["generation"] == first.json()["generation"]
        assert second.json()["revision"] == first.json()["revision"] == 1

        prepared = client.get("/api/diary/corefs-prepared", headers=headers)
        assert prepared.status_code == 200
        assert prepared.json()["authoritative"] is False
        session = unlock_session_store.resolve(token)
        assert session is not None
        objects = read_prepared_writing_objects(session=session)
        encrypted_draft = next(
            item for item in objects if item.stable_id == first.json()["stableId"]
        )
        decoded = decode_draft_document(encrypted_draft.content)
        assert decoded.body == "<p>unsaved private draft</p>"
        assert decoded.metadata == {
            "entryDate": "2026-08-03",
            "legacyStorageKey": payload["draftId"],
            "mood": "hopeful",
            "title": "Unsaved",
        }
        native_attachment = next(item for item in objects if item.kind == "attachment")
        assert native_attachment.content == b"native encrypted bytes"

        legacy = client.get(f"/api/diary?userId={user_id}", headers=headers)
        assert legacy.status_code == 200
        assert legacy.json()[0]["body"] == "<p>legacy authority</p>"


def test_note_is_read_back_through_authorized_stable_role() -> None:
    with managed_test_client("anima-corefs-note-readback-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        token = str(reg["unlockToken"])
        session = unlock_session_store.resolve(token)
        assert session is not None
        with get_user_session_factory(user_id)() as db:
            result = prepare_diary_validation_catalog(
                session=session,
                db=db,
                staged_notes=(
                    LegacyNote(
                        id="note-1",
                        title="Native",
                        body="# encrypted note",
                        content_type="text/markdown",
                        updated_at="2026-08-02T00:00:00Z",
                    ),
                ),
            )
        assert result.published is True
        role = resolve_prepared_role(session=session, role="core.notes")
        assert role["stableId"]
        note = next(
            item for item in read_prepared_writing_objects(session=session) if item.kind == "note"
        )
        decoded = decode_note_document(note.content)
        assert decoded.title == "Native"
        assert decoded.body == "# encrypted note"


def test_unlock_lifecycle_rerun_preserves_native_layout_and_is_a_noop() -> None:
    with managed_test_client("anima-corefs-lifecycle-noop-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        token = str(reg["unlockToken"])
        session = unlock_session_store.resolve(token)
        assert session is not None
        draft_id = migration_opaque_id("native", "lifecycle-draft")
        note_id = migration_opaque_id("native", "lifecycle-note")
        with get_user_session_factory(user_id)() as db:
            prepare_diary_validation_catalog(
                session=session,
                db=db,
                staged_drafts=(
                    LegacyDiaryDraft(
                        id="lifecycle-draft",
                        target_entry_id=None,
                        body="<p>native draft</p>",
                        content_type="text/html",
                        updated_at="2026-08-01T02:00:00Z",
                        stable_id=draft_id,
                        created_at="2026-08-01T01:00:00Z",
                        native_metadata={"origin": "lifecycle"},
                    ),
                ),
                staged_notes=(
                    LegacyNote(
                        id="lifecycle-note",
                        title="Native note",
                        body="# native",
                        content_type="text/markdown",
                        updated_at="2026-08-01T04:00:00Z",
                        stable_id=note_id,
                        created_at="2026-08-01T03:00:00Z",
                        native_metadata={"origin": "lifecycle"},
                    ),
                ),
            )

        prepared = read_prepared_writing_snapshot(session=session)
        journal = next(item for item in prepared.folders if item.role == "core.journal")
        notes = next(item for item in prepared.folders if item.role == "core.notes")
        preserved = tuple(
            InactiveFolder(
                stable_id=item.stable_id,
                parent_id=(journal.stable_id if item.role == "core.notes" else item.parent_id),
                name=(
                    "Renamed Journal"
                    if item.role == "core.journal"
                    else "Moved Notes"
                    if item.role == "core.notes"
                    else item.name
                ),
                order=0,
                role=item.role,
                owner="user",
                agent_access="write",
                policy="user-write" if item.role is not None else "inherit",
            )
            for item in prepared.folders
        )
        draft = next(item for item in prepared.objects if item.stable_id == draft_id)
        note = next(item for item in prepared.objects if item.stable_id == note_id)
        decoded_draft = decode_draft_document(draft.content)
        decoded_note = decode_note_document(note.content)
        moved = build_inactive_diary_catalog(
            user_id=user_id,
            folders=(),
            entries=(),
            drafts=(
                LegacyDiaryDraft(
                    id=draft_id,
                    target_entry_id=None,
                    body=decoded_draft.body,
                    content_type=decoded_draft.content_type,
                    updated_at=draft.updated_at,
                    stable_id=draft_id,
                    metadata=decoded_draft.metadata,
                    target_stable_id=decoded_draft.target_id,
                    created_at=draft.created_at,
                    native_metadata=draft.metadata,
                ),
            ),
            notes=(
                LegacyNote(
                    id=note_id,
                    title=decoded_note.title,
                    body=decoded_note.body,
                    content_type=decoded_note.content_type,
                    updated_at=note.updated_at,
                    stable_id=note_id,
                    created_at=note.created_at,
                    native_metadata=note.metadata,
                ),
            ),
            preserved_folders=preserved,
        ).with_expected_revisions({draft_id: draft.revision, note_id: note.revision})
        head = session.corefs_session.validation_snapshot(session.corefs_keys)
        moved_result = moved.publish_native(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            expected_head=(int(head["generation"]), str(head["catalogHash"])),
        )
        assert moved_result["published"] is True

        with get_user_session_factory(user_id)() as db:
            rerun = prepare_diary_validation_catalog(session=session, db=db)
        assert rerun.published is False
        assert rerun.generation == moved_result["generation"]
        after = read_prepared_writing_snapshot(session=session)
        after_journal = next(item for item in after.folders if item.role == "core.journal")
        after_notes = next(item for item in after.folders if item.role == "core.notes")
        assert (after_journal.stable_id, after_journal.name, after_journal.parent_id) == (
            journal.stable_id,
            "Renamed Journal",
            journal.parent_id,
        )
        assert (after_notes.stable_id, after_notes.name, after_notes.parent_id) == (
            notes.stable_id,
            "Moved Notes",
            journal.stable_id,
        )
        after_draft = next(item for item in after.objects if item.stable_id == draft_id)
        after_note = next(item for item in after.objects if item.stable_id == note_id)
        assert (after_draft.created_at, after_draft.updated_at, after_draft.metadata) == (
            "2026-08-01T01:00:00Z",
            "2026-08-01T02:00:00Z",
            {"origin": "lifecycle"},
        )
        assert (after_note.created_at, after_note.updated_at, after_note.metadata) == (
            "2026-08-01T03:00:00Z",
            "2026-08-01T04:00:00Z",
            {"origin": "lifecycle"},
        )
