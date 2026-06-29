from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.models import User
from anima_server.models.runtime import RuntimeImageMessageLink, RuntimeMessage, RuntimeThread
from anima_server.services.images.capabilities import ImageProcessingCapabilities
from anima_server.services.images.indexing import index_image_asset
from anima_server.services.images.store import register_image_asset
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine
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


def _embedding(_text: str) -> list[float]:
    return [1.0, *([0.0] * (_TEST_DIM - 1))]


def _create_user(db: Session, *, username: str = "proactive-image") -> User:
    user = User(
        username=username,
        password_hash="not-used",
        display_name="Proactive Image",
    )
    db.add(user)
    db.flush()
    return user


def _create_image(
    runtime_db: Session,
    *,
    user_id: int,
    upload_context: str,
    filename: str,
    byte_suffix: bytes,
    status: str = "indexed",
    with_embeddings: bool = True,
    metadata_json: dict[str, object] | None = None,
    embedding_fn: Callable[[str], list[float]] = _embedding,
) -> tuple[int, RuntimeMessage, str]:
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
        embedding_fn=embedding_fn if with_embeddings else None,
        capabilities=ImageProcessingCapabilities(),
    )
    stored.asset.status = status
    stored.asset.metadata_json = metadata_json
    runtime_db.flush()
    return stored.asset.id, message, attachment_id


def test_select_proactive_image_candidate_uses_indexed_owned_unprompted_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.proactive import select_proactive_image_candidate

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with runtime_db_session() as runtime_db:
        valid_id, message, attachment_id = _create_image(
            runtime_db,
            user_id=7,
            upload_context="Alpha screenshot with unresolved setup details.",
            filename="alpha.png",
            byte_suffix=b"alpha",
        )
        _create_image(
            runtime_db,
            user_id=7,
            upload_context="Deleted screenshot should be skipped.",
            filename="deleted.png",
            byte_suffix=b"deleted",
            status="deleted",
        )
        _create_image(
            runtime_db,
            user_id=7,
            upload_context="Unembedded screenshot should be skipped.",
            filename="unembedded.png",
            byte_suffix=b"unembedded",
            with_embeddings=False,
        )
        _create_image(
            runtime_db,
            user_id=7,
            upload_context="Already prompted screenshot should be skipped.",
            filename="prompted.png",
            byte_suffix=b"prompted",
            metadata_json={"proactivePromptedAt": "2026-06-29T00:00:00+00:00"},
        )
        _create_image(
            runtime_db,
            user_id=8,
            upload_context="Other user's screenshot should be skipped.",
            filename="other-user.png",
            byte_suffix=b"other-user",
        )

        candidate = select_proactive_image_candidate(runtime_db, user_id=7)

    assert candidate is not None
    assert candidate.image_asset_id == valid_id
    assert candidate.filename == "alpha.png"
    assert candidate.source_message_id == message.id
    assert candidate.source_thread_id == message.thread_id
    assert candidate.attachment_id == attachment_id
    assert candidate.attachment_url == f"/api/chat/messages/{message.id}/attachments/{attachment_id}"
    assert candidate.pills == [
        {"kind": "image_source", "label": "alpha.png", "ref": f"image:{valid_id}"}
    ]


@pytest.mark.asyncio
async def test_generate_proactive_notice_emits_image_pill_and_suppresses_repeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.proactive import generate_proactive_notice

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "agent_provider", "scaffold")
    with _db_session() as db, runtime_db_session() as runtime_db:
        user = _create_user(db)
        asset_id, _message, _attachment_id = _create_image(
            runtime_db,
            user_id=user.id,
            upload_context="Alpha screenshot with unresolved setup details.",
            filename="alpha.png",
            byte_suffix=b"alpha",
        )

        first = await generate_proactive_notice(
            db,
            user_id=user.id,
            runtime_db=runtime_db,
        )
        second = await generate_proactive_notice(
            db,
            user_id=user.id,
            runtime_db=runtime_db,
        )

    assert first is not None
    assert first.source == "proactive_image"
    assert first.pills == [
        {"kind": "image_source", "label": "alpha.png", "ref": f"image:{asset_id}"}
    ]
    assert "alpha.png" in first.message
    assert second is None
