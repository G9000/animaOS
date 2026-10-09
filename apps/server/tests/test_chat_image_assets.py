from __future__ import annotations

import base64

import pytest
from anima_server.config import settings
from anima_server.schemas.chat import ChatRequestAttachment
from anima_server.services.agent.attachments import (
    AttachmentReadError,
    prepare_corefs_chat_attachments,
    read_attachment_bytes,
    resolve_corefs_chat_attachment,
)
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client

PNG_BYTES = b"\x89PNG\r\n\x1a\nprivate-attachment"


def _attachment(filename: str) -> ChatRequestAttachment:
    return ChatRequestAttachment(
        kind="image",
        filename=filename,
        mimeType="image/png",
        data=base64.b64encode(PNG_BYTES).decode(),
    )


def test_native_chat_attachments_keep_distinct_identity_and_per_upload_names() -> None:
    with managed_test_client("native-chat-images-") as client:
        response = client.post(
            "/api/auth/register",
            json={
                "username": "attachment-owner",
                "name": "Attachment owner",
                "password": "pw123456",
            },
        )
        assert response.status_code == 201, response.text
        session = unlock_session_store.resolve(response.json()["unlockToken"])
        assert session is not None
        first, second = prepare_corefs_chat_attachments(
            session=session,
            attachments=[_attachment("first.png"), _attachment("../second final.png")],
        )
        assert first.id != second.id
        assert first.filename == "first.png"
        assert second.filename == "second final.png"
        assert first.path.startswith("corefs-user://")
        assert first.storage_path.startswith("corefs://object/")
        assert first.asset_id is None and second.asset_id is None
        assert first.delete_on_error is False
        assert "storagePath" not in first.to_public_dict(message_id=123)
        assert read_attachment_bytes(first.path) == PNG_BYTES
        restored = resolve_corefs_chat_attachment(session=session, object_uri=second.storage_path)
        assert restored.id == second.id
        assert read_attachment_bytes(restored.path) == PNG_BYTES
        assert not (settings.data_dir / "users").exists()


def test_attachment_provider_rejects_plaintext_host_path(tmp_path) -> None:
    physical = tmp_path / "legacy.png"
    physical.write_bytes(PNG_BYTES)
    with pytest.raises(AttachmentReadError, match="Only canonical CoreFS"):
        read_attachment_bytes(str(physical))
