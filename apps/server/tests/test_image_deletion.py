from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from anima_server.config import settings
from anima_server.models.runtime import (
    RuntimeImageAnnotation,
    RuntimeImageAsset,
    RuntimeImageMessageLink,
    RuntimeMessage,
    RuntimeThread,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.images.capabilities import ImageProcessingCapabilities
from anima_server.services.images.indexing import index_image_asset
from anima_server.services.images.store import register_image_asset, resolve_image_storage_path
from sqlalchemy import func, select

pytest_plugins = ("conftest_runtime",)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)

_TEST_DIM = 768


def _embedding(_text: str) -> list[float]:
    return [1.0, *([0.0] * (_TEST_DIM - 1))]


def _linked_image(
    runtime_db,
    *,
    user_id: int,
    filename: str = "screen.png",
    byte_suffix: bytes = b"image",
    upload_context: str = "Screenshot with setup details.",
    retention_state: str = "transient",
    embedding_fn: Callable[[str], list[float]] = _embedding,
) -> tuple[RuntimeImageAsset, RuntimeMessage, str, Path]:
    thread = RuntimeThread(user_id=user_id, status="active")
    runtime_db.add(thread)
    runtime_db.flush()
    stored = register_image_asset(
        runtime_db,
        user_id=user_id,
        data=PNG_BYTES + byte_suffix,
        mime_type="image/png",
        filename=filename,
    )
    stored.asset.retention_state = retention_state
    attachment_id = f"img_{stored.asset.id}_{thread.id}"
    message = RuntimeMessage(
        thread_id=thread.id,
        user_id=user_id,
        sequence_id=1,
        role="user",
        content_text=upload_context,
        content_json={
            "attachments": [
                {
                    "id": attachment_id,
                    "kind": "image",
                    "mimeType": "image/png",
                    "filename": filename,
                    "assetId": stored.asset.id,
                    "storagePath": stored.asset.storage_path,
                }
            ]
        },
    )
    runtime_db.add(message)
    runtime_db.flush()
    runtime_db.add(
        RuntimeImageMessageLink(
            user_id=user_id,
            message_id=message.id,
            image_asset_id=stored.asset.id,
            attachment_id=attachment_id,
        )
    )
    index_image_asset(
        runtime_db,
        user_id=user_id,
        image_asset_id=stored.asset.id,
        upload_context=upload_context,
        embedding_fn=embedding_fn,
        capabilities=ImageProcessingCapabilities(),
    )
    runtime_db.flush()
    path = resolve_image_storage_path(stored.asset.storage_path, user_id=user_id)
    return stored.asset, message, attachment_id, path


def test_forget_image_asset_removes_links_annotations_embeddings_row_and_file(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.deletion import forget_image_asset

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    asset, message, _attachment_id, path = _linked_image(runtime_db, user_id=7)
    assert path.exists()

    result = forget_image_asset(runtime_db, user_id=7, image_asset_id=asset.id)

    assert result.forgotten is True
    assert result.file_deleted is True
    assert not path.exists()
    assert runtime_db.get(RuntimeImageAsset, asset.id) is None
    assert runtime_db.scalar(select(func.count(RuntimeImageMessageLink.id))) == 0
    assert runtime_db.scalar(select(func.count(RuntimeImageAnnotation.id))) == 0
    assert runtime_db.scalar(
        select(func.count(RuntimeEmbedding.id)).where(
            RuntimeEmbedding.source_type == "image_annotation"
        )
    ) == 0
    assert runtime_db.get(RuntimeMessage, message.id).content_json == {"attachments": []}


def test_remove_message_image_link_updates_message_but_keeps_reused_asset(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.deletion import remove_message_image_link

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first_asset, first_message, first_attachment_id, path = _linked_image(
        runtime_db,
        user_id=7,
        byte_suffix=b"shared",
    )
    second_asset, _second_message, _second_attachment_id, _path = _linked_image(
        runtime_db,
        user_id=7,
        byte_suffix=b"shared",
    )
    assert first_asset.id == second_asset.id

    result = remove_message_image_link(
        runtime_db,
        user_id=7,
        message_id=first_message.id,
        attachment_id=first_attachment_id,
    )

    assert result.removed is True
    assert result.asset_deleted is False
    assert result.file_deleted is False
    assert path.exists()
    assert runtime_db.get(RuntimeImageAsset, first_asset.id) is not None
    assert runtime_db.scalar(select(func.count(RuntimeImageMessageLink.id))) == 1
    refreshed_message = runtime_db.get(RuntimeMessage, first_message.id)
    assert refreshed_message.content_json == {"attachments": []}


def test_delete_thread_with_image_cleanup_removes_orphaned_transient_asset(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.deletion import delete_thread_with_image_cleanup

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    asset, message, _attachment_id, path = _linked_image(runtime_db, user_id=7)

    result = delete_thread_with_image_cleanup(
        runtime_db,
        user_id=7,
        thread_id=message.thread_id,
    )

    assert result.deleted is True
    assert result.assets_deleted == [asset.id]
    assert result.files_deleted == [str(path)]
    assert not path.exists()
    assert runtime_db.get(RuntimeImageAsset, asset.id) is None
    assert runtime_db.get(RuntimeThread, message.thread_id) is None


def test_delete_thread_with_image_cleanup_keeps_retained_asset(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.deletion import delete_thread_with_image_cleanup

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    asset, message, _attachment_id, path = _linked_image(
        runtime_db,
        user_id=7,
        retention_state="retained",
    )

    result = delete_thread_with_image_cleanup(
        runtime_db,
        user_id=7,
        thread_id=message.thread_id,
    )

    assert result.deleted is True
    assert result.assets_deleted == []
    assert result.files_deleted == []
    assert path.exists()
    assert runtime_db.get(RuntimeImageAsset, asset.id) is not None
    assert runtime_db.scalar(select(func.count(RuntimeImageMessageLink.id))) == 0
