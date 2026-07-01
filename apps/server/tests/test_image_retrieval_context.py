from __future__ import annotations

import base64
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.models import User
from anima_server.models.runtime import RuntimeImageMessageLink, RuntimeMessage, RuntimeThread
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from anima_server.services.images.capabilities import ImageProcessingCapabilities
from anima_server.services.images.indexing import index_image_asset
from anima_server.services.images.store import register_image_asset
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

pytest_plugins = ("conftest_runtime",)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)

_TEST_DIM = 768


@contextmanager
def _db_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _embedding(x: float, y: float = 0.0) -> list[float]:
    return [x, y, *([0.0] * (_TEST_DIM - 2))]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _text_embedding(text: str) -> list[float]:
    lower = text.lower()
    if "alpha" in lower:
        return _embedding(1.0, 0.0)
    if "beta" in lower:
        return _embedding(0.8, 0.2)
    if "gamma" in lower:
        return _embedding(0.7, 0.3)
    if "delta" in lower:
        return _embedding(0.6, 0.4)
    if "deleted" in lower:
        return _embedding(1.0, 0.0)
    return _embedding(0.0, 1.0)


def _create_user(db: Session, *, username: str = "image-retrieval") -> User:
    user = User(
        username=username,
        password_hash="not-used",
        display_name="Image Retrieval",
    )
    db.add(user)
    db.flush()
    return user


def _create_indexed_image(
    runtime_db: Session,
    tmp_path: Path,
    *,
    user_id: int,
    upload_context: str,
    filename: str,
    byte_suffix: bytes,
    status: str = "indexed",
    embedding_fn: Callable[[str], list[float]] = _text_embedding,
) -> tuple[int, RuntimeMessage, str]:
    thread = RuntimeThread(user_id=user_id, status="active")
    runtime_db.add(thread)
    runtime_db.flush()

    data = PNG_BYTES + byte_suffix
    stored = register_image_asset(
        runtime_db,
        user_id=user_id,
        data=data,
        mime_type="image/png",
        filename=filename,
    )
    attachment_id = f"img_{stored.asset.id}"
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
                    "data": _b64(data),
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
    stored.asset.status = status
    runtime_db.flush()
    return stored.asset.id, message, attachment_id


def _link_image_to_new_message(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
    thread_id: int | None = None,
    attachment_id: str | None = None,
    created_at: datetime | None = None,
) -> tuple[RuntimeMessage, str]:
    if thread_id is None:
        thread = RuntimeThread(user_id=user_id, status="active")
        runtime_db.add(thread)
        runtime_db.flush()
        thread_id = thread.id

    if attachment_id is None:
        attachment_id = (
            f"img_{user_id}_{image_asset_id}_{created_at or datetime.now():%Y%m%d%H%M%S%f}"
        )

    max_sequence_id = runtime_db.scalar(
        select(func.max(RuntimeMessage.sequence_id)).where(
            RuntimeMessage.thread_id == thread_id
        )
    )
    sequence_id = int(max_sequence_id or 0) + 1

    message = RuntimeMessage(
        thread_id=thread_id,
        user_id=user_id,
        sequence_id=int(sequence_id),
        role="user",
        content_text="Follow-up image share.",
        content_json={"attachments": []},
    )
    if created_at is not None:
        message.created_at = created_at
    runtime_db.add(message)
    runtime_db.flush()

    runtime_db.add(
        RuntimeImageMessageLink(
            user_id=user_id,
            message_id=message.id,
            image_asset_id=image_asset_id,
            attachment_id=attachment_id,
            created_at=created_at,
        )
    )
    runtime_db.flush()
    return message, attachment_id


def test_search_image_annotations_includes_source_attachment_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.rag import search_image_annotations

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with runtime_db_session() as runtime_db:
        asset_id, message, attachment_id = _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=7,
            upload_context="Alpha invoice screenshot with the final total.",
            filename="alpha-invoice.png",
            byte_suffix=b"alpha",
        )

        results = search_image_annotations(
            runtime_db,
            user_id=7,
            query="invoice total",
            embedding_fn=lambda text: _embedding(1.0, 0.0),
            limit=5,
        )

    assert len(results) == 1
    result = results[0]
    assert result.image_asset_id == asset_id
    assert result.filename == "alpha-invoice.png"
    assert result.source_message_id == message.id
    assert result.source_thread_id == message.thread_id
    assert result.attachment_id == attachment_id
    assert result.attachment_url == f"/api/chat/messages/{message.id}/attachments/{attachment_id}"
    assert "Alpha invoice" in result.snippet


def test_search_image_annotations_includes_related_sources_for_duplicate_image_shares(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.rag import search_image_annotations

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with runtime_db_session() as runtime_db:
        asset_id, message, attachment_id = _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=7,
            upload_context="Alpha invoice screenshot with the final total.",
            filename="alpha-invoice.png",
            byte_suffix=b"alpha",
        )
        followup_message, followup_attachment = _link_image_to_new_message(
            runtime_db,
            user_id=7,
            image_asset_id=asset_id,
            attachment_id="img_7_followup",
            created_at=datetime.now(UTC) + timedelta(hours=1),
        )

        results = search_image_annotations(
            runtime_db,
            user_id=7,
            query="invoice total",
            embedding_fn=lambda text: _embedding(1.0, 0.0),
            limit=5,
        )

    assert len(results) == 1
    result = results[0]
    assert result.image_asset_id == asset_id
    assert result.source_message_id == followup_message.id
    assert result.source_thread_id == followup_message.thread_id
    assert result.attachment_id == followup_attachment
    assert (
        result.attachment_url
        == f"/api/chat/messages/{followup_message.id}/attachments/{followup_attachment}"
    )
    assert len(result.related_sources) == 2
    assert {source.attachment_id for source in result.related_sources} == {
        attachment_id,
        followup_attachment,
    }
    assert result.related_sources[0].message_id == followup_message.id
    assert result.related_sources[1].message_id == message.id


def test_search_image_annotations_by_embedding_skips_non_positive_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.rag import search_image_annotations_by_embedding

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with runtime_db_session() as runtime_db:
        _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=7,
            upload_context="Alpha invoice screenshot with the final total.",
            filename="alpha-invoice.png",
            byte_suffix=b"alpha",
        )

        zero_similarity_results = search_image_annotations_by_embedding(
            runtime_db,
            user_id=7,
            query_embedding=_embedding(0.0, 0.0),
            limit=5,
        )
        negative_similarity_results = search_image_annotations_by_embedding(
            runtime_db,
            user_id=7,
            query_embedding=_embedding(-1.0, 0.0),
            limit=5,
        )

    assert zero_similarity_results == []
    assert negative_similarity_results == []


def test_turn_memory_blocks_include_bounded_relevant_images_and_skip_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.memory_blocks import build_turn_memory_blocks

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with _db_session() as db, runtime_db_session() as runtime_db:
        user = _create_user(db)
        runtime_thread = RuntimeThread(user_id=user.id, status="active")
        runtime_db.add(runtime_thread)
        runtime_db.flush()
        _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id,
            upload_context="Alpha invoice screenshot with final total.",
            filename="alpha.png",
            byte_suffix=b"alpha",
        )
        _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id,
            upload_context="Beta dashboard image with project status.",
            filename="beta.png",
            byte_suffix=b"beta",
        )
        _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id,
            upload_context="Gamma receipt photo for the trip.",
            filename="gamma.png",
            byte_suffix=b"gamma",
        )
        _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id,
            upload_context="Delta whiteboard snapshot.",
            filename="delta.png",
            byte_suffix=b"delta",
        )
        _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id,
            upload_context="Deleted alpha screenshot should not be recalled.",
            filename="deleted.png",
            byte_suffix=b"deleted",
            status="deleted",
        )
        _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id + 1,
            upload_context="Alpha image from another user.",
            filename="other-user.png",
            byte_suffix=b"other-user",
        )

        blocks = build_turn_memory_blocks(
            db,
            user_id=user.id,
            thread_id=runtime_thread.id,
            query="invoice dashboard receipt",
            query_embedding=_embedding(1.0, 0.0),
            runtime_db=runtime_db,
        )

    block = next(block for block in blocks if block.label == "relevant_images")
    assert block.value.count("- image:") == 3
    assert "alpha.png" in block.value
    assert "beta.png" in block.value
    assert "gamma.png" in block.value
    assert "delta.png" not in block.value
    assert "deleted.png" not in block.value
    assert "other-user.png" not in block.value
    assert "thread=" in block.value
    assert "message=" in block.value
    assert "attachment=" in block.value
    assert "/api/chat/messages/" in block.value


def test_turn_memory_blocks_include_related_source_counts_for_duplicate_image_shares(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.memory_blocks import build_turn_memory_blocks

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with _db_session() as db, runtime_db_session() as runtime_db:
        user = _create_user(db)
        runtime_thread = RuntimeThread(user_id=user.id, status="active")
        runtime_db.add(runtime_thread)
        runtime_db.flush()
        asset_id, _, _ = _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id,
            upload_context="Alpha invoice screenshot with final total.",
            filename="alpha.png",
            byte_suffix=b"alpha",
        )
        _link_image_to_new_message(
            runtime_db,
            user_id=user.id,
            image_asset_id=asset_id,
            attachment_id="img_followup",
            thread_id=runtime_thread.id,
            created_at=datetime.now(UTC) + timedelta(hours=1),
        )

        blocks = build_turn_memory_blocks(
            db,
            user_id=user.id,
            thread_id=runtime_thread.id,
            query="invoice",
            query_embedding=_embedding(1.0, 0.0),
            runtime_db=runtime_db,
        )

    block = next(block for block in blocks if block.label == "relevant_images")
    assert block.value.count("- image:") == 1
    assert "alpha.png" in block.value
    assert "related_sources=1" in block.value
    assert "attachment=" in block.value


def test_search_images_tool_returns_bounded_image_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings
    from anima_server.services.agent.tools import get_core_tools, search_images

    async def fake_generate_embedding(text: str) -> list[float]:
        assert text == "invoice"
        return _embedding(1.0, 0.0)

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(embeddings, "generate_embedding", fake_generate_embedding)
    with _db_session() as db, runtime_db_session() as runtime_db:
        user = _create_user(db, username="image-tool")
        _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id,
            upload_context="Alpha invoice screenshot with final total.",
            filename="alpha.png",
            byte_suffix=b"alpha",
        )
        _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id,
            upload_context="Beta dashboard image with project status.",
            filename="beta.png",
            byte_suffix=b"beta",
        )

        set_tool_context(
            ToolContext(
                db=db,
                runtime_db=runtime_db,
                user_id=user.id,
                thread_id=1,
            )
        )
        try:
            result = search_images(query="invoice", limit="1")
        finally:
            clear_tool_context()

    tool_names = [getattr(tool, "name", None) or tool.__name__ for tool in get_core_tools()]
    assert "search_images" in tool_names
    assert "Found 1 image memory match" in result
    assert "image:" in result
    assert "alpha.png" in result
    assert "message=" in result
    assert "/api/chat/messages/" in result
    assert "beta.png" not in result


def test_search_images_tool_includes_related_sources_for_duplicate_shares(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings
    from anima_server.services.agent.tools import get_core_tools, search_images

    async def fake_generate_embedding(text: str) -> list[float]:
        assert text == "invoice"
        return _embedding(1.0, 0.0)

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(embeddings, "generate_embedding", fake_generate_embedding)
    with _db_session() as db, runtime_db_session() as runtime_db:
        user = _create_user(db, username="image-tool-duplicates")
        asset_id, _, _ = _create_indexed_image(
            runtime_db,
            tmp_path,
            user_id=user.id,
            upload_context="Alpha invoice screenshot with final total.",
            filename="alpha.png",
            byte_suffix=b"alpha",
        )
        _link_image_to_new_message(
            runtime_db,
            user_id=user.id,
            image_asset_id=asset_id,
            attachment_id="img_followup_tool",
            created_at=datetime.now(UTC) + timedelta(hours=1),
        )

        set_tool_context(
            ToolContext(
                db=db,
                runtime_db=runtime_db,
                user_id=user.id,
                thread_id=1,
            )
        )
        try:
            result = search_images(query="invoice", limit="5")
        finally:
            clear_tool_context()

    tool_names = [getattr(tool, "name", None) or tool.__name__ for tool in get_core_tools()]
    assert "search_images" in tool_names
    assert "Found 1 image memory match" in result
    assert "related_sources=1" in result
    assert "related:" in result
    assert "img_followup_tool" in result
