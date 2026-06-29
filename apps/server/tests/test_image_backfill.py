from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.config import settings
from anima_server.models.runtime import (
    RuntimeImageAsset,
    RuntimeImageMessageLink,
    RuntimeMessage,
    RuntimeThread,
)
from anima_server.services.agent.attachments import resolve_message_attachment
from sqlalchemy import func, select

pytest_plugins = ("conftest_runtime",)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)


def _legacy_message(
    runtime_db,
    *,
    user_id: int = 7,
    attachment_id: str = "legacy_img",
    storage_path: str = "users/7/attachments/chat/legacy_img.png",
    filename: str = "legacy.png",
) -> RuntimeMessage:
    thread = RuntimeThread(user_id=user_id, status="active")
    runtime_db.add(thread)
    runtime_db.flush()
    message = RuntimeMessage(
        thread_id=thread.id,
        user_id=user_id,
        sequence_id=1,
        role="user",
        content_text="legacy image",
        content_json={
            "attachments": [
                {
                    "id": attachment_id,
                    "kind": "image",
                    "mimeType": "image/png",
                    "filename": filename,
                    "storagePath": storage_path,
                }
            ]
        },
    )
    runtime_db.add(message)
    runtime_db.flush()
    return message


def test_backfill_legacy_chat_images_is_idempotent_and_preserves_fetch(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.backfill import backfill_legacy_chat_images

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    legacy_path = tmp_path / "users/7/attachments/chat/legacy_img.png"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(PNG_BYTES)
    message = _legacy_message(runtime_db)

    first = backfill_legacy_chat_images(runtime_db, user_id=7)
    second = backfill_legacy_chat_images(runtime_db, user_id=7)

    assert first.messages_scanned == 1
    assert first.assets_created == 1
    assert first.links_created == 1
    assert first.missing_files == []
    assert second.assets_created == 0
    assert second.links_created == 0
    assert runtime_db.scalar(select(func.count(RuntimeImageAsset.id))) == 1
    assert runtime_db.scalar(select(func.count(RuntimeImageMessageLink.id))) == 1

    refreshed = runtime_db.get(RuntimeMessage, message.id)
    attachment = refreshed.content_json["attachments"][0]
    asset = runtime_db.scalar(select(RuntimeImageAsset))
    assert attachment["assetId"] == asset.id
    assert attachment["storagePath"] == asset.storage_path
    assert attachment["retentionState"] == "transient"

    resolved = resolve_message_attachment(
        runtime_db,
        message=refreshed,
        attachment_id="legacy_img",
    )
    assert resolved is not None
    assert resolved[0].read_bytes() == PNG_BYTES
    assert resolved[1] == "image/png"


def test_backfill_legacy_chat_images_reports_missing_files_without_aborting(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.backfill import backfill_legacy_chat_images

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _legacy_message(
        runtime_db,
        attachment_id="missing",
        storage_path="users/7/attachments/chat/missing.png",
    )

    report = backfill_legacy_chat_images(runtime_db, user_id=7)

    assert report.messages_scanned == 1
    assert report.assets_created == 0
    assert report.links_created == 0
    assert report.missing_files == ["message=1 attachment=missing"]
    assert runtime_db.scalar(select(func.count(RuntimeImageAsset.id))) == 0
