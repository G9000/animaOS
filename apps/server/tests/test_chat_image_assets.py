from __future__ import annotations

import base64
from pathlib import Path

import pytest
from anima_server.config import settings
from anima_server.models.runtime import (
    RuntimeImageAsset,
    RuntimeImageMessageLink,
    RuntimeMessage,
    RuntimeThread,
)
from anima_server.schemas.chat import ChatRequestAttachment
from anima_server.services.agent.attachments import (
    prepare_chat_attachments,
    resolve_message_attachment,
)
from anima_server.services.agent.persistence import (
    append_user_message,
    link_message_image_assets,
)
from sqlalchemy import func, select

pytest_plugins = ("conftest_runtime",)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _attachment(filename: str = "pixel.png") -> ChatRequestAttachment:
    return ChatRequestAttachment(
        kind="image",
        filename=filename,
        mimeType="image/png",
        data=_b64(PNG_BYTES),
    )


def _thread(runtime_db, *, user_id: int = 7) -> RuntimeThread:
    thread = RuntimeThread(user_id=user_id, status="active")
    runtime_db.add(thread)
    runtime_db.flush()
    return thread


def test_prepare_chat_attachments_with_runtime_db_returns_asset_backed_attachment(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    prepared = prepare_chat_attachments(
        user_id=7,
        attachments=[_attachment()],
        runtime_db=runtime_db,
    )

    asset = runtime_db.scalar(select(RuntimeImageAsset))
    assert asset is not None
    assert len(prepared) == 1
    assert prepared[0].asset_id == asset.id
    assert prepared[0].storage_path == asset.storage_path
    assert prepared[0].path == str(tmp_path / asset.storage_path)
    assert prepared[0].to_content_dict()["assetId"] == asset.id
    assert prepared[0].to_public_dict(message_id=99)["assetId"] == asset.id
    assert asset.storage_path.startswith("users/7/media/images/")


def test_failed_turn_cleanup_keeps_reused_asset_file(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.service import _delete_prepared_attachments

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = prepare_chat_attachments(
        user_id=7,
        attachments=[_attachment()],
        runtime_db=runtime_db,
    )
    reused = prepare_chat_attachments(
        user_id=7,
        attachments=[_attachment()],
        runtime_db=runtime_db,
    )
    path = Path(reused[0].path)
    assert first[0].asset_id == reused[0].asset_id
    assert path.exists()

    _delete_prepared_attachments(reused)

    assert path.exists()


def test_persisted_user_message_creates_image_asset_links(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    thread = _thread(runtime_db, user_id=7)
    prepared = prepare_chat_attachments(
        user_id=7,
        attachments=[_attachment()],
        runtime_db=runtime_db,
    )

    message = append_user_message(
        runtime_db,
        thread=thread,
        run_id=None,
        content="what is this?",
        sequence_id=1,
        attachments=prepared,
    )
    links = link_message_image_assets(runtime_db, message=message, attachments=prepared)

    assert len(links) == 1
    assert links[0].message_id == message.id
    assert links[0].image_asset_id == prepared[0].asset_id
    assert links[0].attachment_id == prepared[0].id
    assert runtime_db.scalar(select(func.count(RuntimeImageMessageLink.id))) == 1


def test_duplicate_image_attachments_keep_distinct_links(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    thread = _thread(runtime_db, user_id=7)
    prepared = prepare_chat_attachments(
        user_id=7,
        attachments=[_attachment("first.png"), _attachment("second.png")],
        runtime_db=runtime_db,
    )

    message = append_user_message(
        runtime_db,
        thread=thread,
        run_id=None,
        content="compare these",
        sequence_id=1,
        attachments=prepared,
    )
    links = link_message_image_assets(runtime_db, message=message, attachments=prepared)

    assert len({attachment.asset_id for attachment in prepared}) == 1
    assert len(links) == 2
    assert {link.attachment_id for link in links} == {attachment.id for attachment in prepared}
    assert runtime_db.scalar(select(func.count(RuntimeImageMessageLink.id))) == 2


def test_resolve_message_attachment_uses_asset_link_for_new_metadata(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    thread = _thread(runtime_db, user_id=7)
    prepared = prepare_chat_attachments(
        user_id=7,
        attachments=[_attachment()],
        runtime_db=runtime_db,
    )
    message = append_user_message(
        runtime_db,
        thread=thread,
        run_id=None,
        content="what is this?",
        sequence_id=1,
        attachments=prepared,
    )
    link_message_image_assets(runtime_db, message=message, attachments=prepared)

    resolved = resolve_message_attachment(
        runtime_db,
        message=message,
        attachment_id=prepared[0].id,
    )

    assert resolved is not None
    path, mime_type = resolved
    assert path.read_bytes() == PNG_BYTES
    assert mime_type == "image/png"


def test_resolve_message_attachment_keeps_legacy_storage_path_fallback(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    legacy_path = tmp_path / "users/7/attachments/chat/img_legacy.png"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(PNG_BYTES)

    message = RuntimeMessage(
        thread_id=1,
        user_id=7,
        sequence_id=1,
        role="user",
        content_text="legacy",
        content_json={
            "attachments": [
                {
                    "id": "img_legacy",
                    "kind": "image",
                    "mimeType": "image/png",
                    "storagePath": "users/7/attachments/chat/img_legacy.png",
                }
            ]
        },
    )

    resolved = resolve_message_attachment(
        runtime_db,
        message=message,
        attachment_id="img_legacy",
    )

    assert resolved is not None
    assert resolved[0] == legacy_path
    assert resolved[1] == "image/png"
