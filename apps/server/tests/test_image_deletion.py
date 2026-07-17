from __future__ import annotations

import json
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
from sqlalchemy import delete, func, select

pytest_plugins = ("conftest_runtime",)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)

# Derived from the actual bound column rather than hardcoded: the pgvector
# column dimension is fixed once per process (baked in at first import of
# RuntimeEmbedding from the then-current default embedding provider), so a
# literal here would drift out of sync whenever that default changes.
_TEST_DIM = RuntimeEmbedding.__table__.c.embedding.type.dim


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


def _write_archived_image_transcript(
    tmp_path: Path,
    *,
    thread_id: int,
    attachment_id: str,
    asset_id: int,
    storage_path: str,
    filename: str = "screen.png",
) -> None:
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir(exist_ok=True)
    (transcripts_dir / f"2026-01-01_thread-{thread_id}.jsonl").write_text(
        json.dumps(
            {
                "role": "user",
                "content": "archived screenshot",
                "ts": "2026-01-01T00:00:00+00:00",
                "seq": 1,
                "attachments": [
                    {
                        "id": attachment_id,
                        "kind": "image",
                        "mimeType": "image/png",
                        "filename": filename,
                        "assetId": asset_id,
                        "storagePath": storage_path,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


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


def test_forget_image_asset_removes_matching_image_source_pills(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.deletion import forget_image_asset

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    asset, message, attachment_id, _path = _linked_image(runtime_db, user_id=7)
    assistant = RuntimeMessage(
        thread_id=message.thread_id,
        user_id=7,
        sequence_id=2,
        role="assistant",
        content_text="I used the image.",
        content_json={
            "pills": [
                {"kind": "image_source", "label": "screen.png", "ref": attachment_id},
                {
                    "kind": "image_source",
                    "label": "screen.png",
                    "ref": f"image:{asset.id}",
                },
                {"kind": "document_source", "label": "Plan", "ref": 44},
            ]
        },
    )
    runtime_db.add(assistant)
    runtime_db.flush()

    result = forget_image_asset(runtime_db, user_id=7, image_asset_id=asset.id)

    assert result.forgotten is True
    assert runtime_db.get(RuntimeMessage, assistant.id).content_json == {
        "pills": [{"kind": "document_source", "label": "Plan", "ref": 44}]
    }


@pytest.mark.asyncio
async def test_forget_image_asset_endpoint_invalidates_user_companion_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.api.routes import images as image_routes
    from anima_server.services.images.deletion import ForgetImageResult
    from anima_server.services.sessions import unlock_session_store
    from starlette.requests import Request

    events: list[object] = []

    class FakeRuntimeDb:
        def commit(self) -> None:
            events.append("commit")

    def fake_forget_image_asset(*args: object, **kwargs: object) -> ForgetImageResult:
        assert kwargs["user_id"] == 42
        assert kwargs["image_asset_id"] == 99
        events.append("forget")
        return ForgetImageResult(forgotten=True, image_asset_id=99, file_deleted=True)

    def fake_invalidate_companion(user_id: int) -> None:
        events.append(("invalidate", user_id))

    monkeypatch.setattr(image_routes, "forget_image_asset", fake_forget_image_asset)
    monkeypatch.setattr(
        image_routes,
        "invalidate_companion",
        fake_invalidate_companion,
        raising=False,
    )

    token = unlock_session_store.create(42, {"memories": b"unit-test-dek"})
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-anima-unlock", token.encode("utf-8"))],
        }
    )

    try:
        response = await image_routes.forget_image_asset_endpoint(
            99,
            request,
            runtime_db=FakeRuntimeDb(),
        )
    finally:
        unlock_session_store.revoke(token)

    assert response == {
        "status": "forgotten",
        "imageAssetId": 99,
        "fileDeleted": True,
    }
    assert events == ["forget", "commit", ("invalidate", 42)]


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


def test_remove_message_image_link_removes_matching_image_source_pills(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.deletion import remove_message_image_link

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    asset, message, attachment_id, _path = _linked_image(runtime_db, user_id=7)
    assistant = RuntimeMessage(
        thread_id=message.thread_id,
        user_id=7,
        sequence_id=2,
        role="assistant",
        content_text="I used the image.",
        content_json={
            "pills": [
                {"kind": "image_source", "label": "screen.png", "ref": attachment_id},
                {
                    "kind": "image_source",
                    "label": "screen.png",
                    "ref": f"image:{asset.id}",
                },
                {"kind": "document_source", "label": "Plan", "ref": 44},
            ]
        },
    )
    runtime_db.add(assistant)
    runtime_db.flush()

    result = remove_message_image_link(
        runtime_db,
        user_id=7,
        message_id=message.id,
        attachment_id=attachment_id,
    )

    assert result.removed is True
    assert result.asset_deleted is True
    assert runtime_db.get(RuntimeMessage, assistant.id).content_json == {
        "pills": [{"kind": "document_source", "label": "Plan", "ref": 44}]
    }


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


def test_delete_thread_with_image_cleanup_uses_archive_after_message_pruning(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.deletion import delete_thread_with_image_cleanup

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    asset, message, attachment_id, path = _linked_image(runtime_db, user_id=7)
    asset_id = asset.id
    thread_id = message.thread_id
    storage_path = asset.storage_path
    thread = runtime_db.get(RuntimeThread, thread_id)
    assert thread is not None
    thread.status = "closed"
    thread.is_archived = True

    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    (transcripts_dir / f"2026-01-01_thread-{thread_id}.jsonl").write_text(
        json.dumps(
            {
                "role": "user",
                "content": "archived screenshot",
                "ts": "2026-01-01T00:00:00+00:00",
                "seq": 1,
                "attachments": [
                    {
                        "id": attachment_id,
                        "kind": "image",
                        "mimeType": "image/png",
                        "filename": "screen.png",
                        "assetId": asset_id,
                        "storagePath": storage_path,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_db.execute(
        delete(RuntimeImageMessageLink).where(
            RuntimeImageMessageLink.message_id == message.id
        )
    )
    runtime_db.execute(delete(RuntimeMessage).where(RuntimeMessage.thread_id == thread_id))
    runtime_db.commit()

    result = delete_thread_with_image_cleanup(
        runtime_db,
        user_id=7,
        thread_id=thread_id,
    )

    assert result.deleted is True
    assert result.assets_deleted == [asset_id]
    assert result.files_deleted == [str(path)]
    assert not path.exists()
    assert runtime_db.get(RuntimeImageAsset, asset_id) is None
    assert runtime_db.scalar(select(func.count(RuntimeImageAnnotation.id))) == 0
    assert runtime_db.scalar(
        select(func.count(RuntimeEmbedding.id)).where(
            RuntimeEmbedding.source_type == "image_annotation"
        )
    ) == 0


def test_delete_thread_with_image_cleanup_keeps_asset_referenced_by_other_pruned_archive(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.deletion import delete_thread_with_image_cleanup

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first_asset, first_message, first_attachment_id, path = _linked_image(
        runtime_db,
        user_id=7,
        byte_suffix=b"shared",
    )
    second_asset, second_message, second_attachment_id, second_path = _linked_image(
        runtime_db,
        user_id=7,
        byte_suffix=b"shared",
        upload_context="Second archived screenshot.",
    )
    assert first_asset.id == second_asset.id
    assert path == second_path
    asset_id = first_asset.id
    storage_path = first_asset.storage_path
    first_thread_id = first_message.thread_id
    second_thread_id = second_message.thread_id
    for thread_id in (first_thread_id, second_thread_id):
        thread = runtime_db.get(RuntimeThread, thread_id)
        assert thread is not None
        thread.status = "closed"
        thread.is_archived = True

    _write_archived_image_transcript(
        tmp_path,
        thread_id=first_thread_id,
        attachment_id=first_attachment_id,
        asset_id=asset_id,
        storage_path=storage_path,
    )
    _write_archived_image_transcript(
        tmp_path,
        thread_id=second_thread_id,
        attachment_id=second_attachment_id,
        asset_id=asset_id,
        storage_path=storage_path,
    )
    runtime_db.execute(
        delete(RuntimeImageMessageLink).where(RuntimeImageMessageLink.user_id == 7)
    )
    runtime_db.execute(
        delete(RuntimeMessage).where(
            RuntimeMessage.thread_id.in_([first_thread_id, second_thread_id])
        )
    )
    runtime_db.commit()

    result = delete_thread_with_image_cleanup(
        runtime_db,
        user_id=7,
        thread_id=first_thread_id,
    )

    assert result.deleted is True
    assert result.assets_deleted == []
    assert result.files_deleted == []
    assert path.exists()
    assert runtime_db.get(RuntimeImageAsset, asset_id) is not None
    assert runtime_db.get(RuntimeThread, first_thread_id) is None
    assert runtime_db.get(RuntimeThread, second_thread_id) is not None


def test_delete_thread_with_image_cleanup_ignores_stale_archive_for_deleted_thread(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.deletion import delete_thread_with_image_cleanup

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first_asset, first_message, first_attachment_id, path = _linked_image(
        runtime_db,
        user_id=7,
        byte_suffix=b"shared",
    )
    second_asset, second_message, second_attachment_id, second_path = _linked_image(
        runtime_db,
        user_id=7,
        byte_suffix=b"shared",
        upload_context="Second archived screenshot.",
    )
    assert first_asset.id == second_asset.id
    assert path == second_path
    asset_id = first_asset.id
    storage_path = first_asset.storage_path
    first_thread_id = first_message.thread_id
    second_thread_id = second_message.thread_id
    for thread_id in (first_thread_id, second_thread_id):
        thread = runtime_db.get(RuntimeThread, thread_id)
        assert thread is not None
        thread.status = "closed"
        thread.is_archived = True

    _write_archived_image_transcript(
        tmp_path,
        thread_id=first_thread_id,
        attachment_id=first_attachment_id,
        asset_id=asset_id,
        storage_path=storage_path,
    )
    _write_archived_image_transcript(
        tmp_path,
        thread_id=second_thread_id,
        attachment_id=second_attachment_id,
        asset_id=asset_id,
        storage_path=storage_path,
    )
    runtime_db.execute(
        delete(RuntimeImageMessageLink).where(RuntimeImageMessageLink.user_id == 7)
    )
    runtime_db.execute(
        delete(RuntimeMessage).where(
            RuntimeMessage.thread_id.in_([first_thread_id, second_thread_id])
        )
    )
    runtime_db.commit()

    first_result = delete_thread_with_image_cleanup(
        runtime_db,
        user_id=7,
        thread_id=first_thread_id,
    )
    runtime_db.commit()
    assert first_result.assets_deleted == []
    assert path.exists()
    assert runtime_db.get(RuntimeImageAsset, asset_id) is not None
    assert (tmp_path / "transcripts" / f"2026-01-01_thread-{first_thread_id}.jsonl").exists()

    second_result = delete_thread_with_image_cleanup(
        runtime_db,
        user_id=7,
        thread_id=second_thread_id,
    )

    assert second_result.deleted is True
    assert second_result.assets_deleted == [asset_id]
    assert second_result.files_deleted == [str(path)]
    assert not path.exists()
    assert runtime_db.get(RuntimeImageAsset, asset_id) is None
    assert runtime_db.scalar(select(func.count(RuntimeImageAnnotation.id))) == 0
    assert runtime_db.scalar(
        select(func.count(RuntimeEmbedding.id)).where(
            RuntimeEmbedding.source_type == "image_annotation"
        )
    ) == 0


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
