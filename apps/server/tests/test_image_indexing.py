from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from anima_server.config import settings
from anima_server.models.runtime import (
    RuntimeImageAnnotation,
    RuntimeImageAsset,
    RuntimeSource,
    RuntimeSourceSpan,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.images.store import register_image_asset
from sqlalchemy import func, select

pytest_plugins = ("conftest_runtime",)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)

_TEST_DIM = 768


def _embedding(seed: float) -> list[float]:
    return [seed, *([0.0] * (_TEST_DIM - 1))]


def _asset(runtime_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, user_id: int = 7):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return register_image_asset(
        runtime_db,
        user_id=user_id,
        data=PNG_BYTES + str(user_id).encode(),
        mime_type="image/png",
        filename="screen.png",
    ).asset


def test_index_image_asset_creates_context_metadata_and_current_embeddings(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.capabilities import ImageProcessingCapabilities
    from anima_server.services.images.indexing import index_image_asset

    asset = _asset(runtime_db, tmp_path, monkeypatch)

    result = index_image_asset(
        runtime_db,
        user_id=7,
        image_asset_id=asset.id,
        upload_context="User asked about the dashboard screenshot.",
        embedding_fn=lambda text: _embedding(1.0),
        capabilities=ImageProcessingCapabilities(),
    )

    kinds = set(
        runtime_db.scalars(
            select(RuntimeImageAnnotation.annotation_kind).where(
                RuntimeImageAnnotation.image_asset_id == asset.id,
                RuntimeImageAnnotation.status == "active",
            )
        ).all()
    )
    embeddings = list(
        runtime_db.scalars(
            select(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == 7,
                RuntimeEmbedding.source_type == "image_annotation",
            )
        ).all()
    )

    assert result.annotations_indexed == 2
    assert kinds == {"upload_context", "metadata"}
    assert len(embeddings) == 2
    assert {row.category for row in embeddings} == {"image"}
    assert {row.importance for row in embeddings} == {3}
    assert runtime_db.get(RuntimeImageAsset, asset.id).status == "indexed"
    assert runtime_db.get(RuntimeImageAsset, asset.id).indexed_at is not None
    source = runtime_db.scalar(
        select(RuntimeSource).where(
            RuntimeSource.user_id == 7,
            RuntimeSource.kind == "image",
            RuntimeSource.source_uri == f"runtime-image://{asset.id}",
        )
    )
    assert source is not None
    spans = list(
        runtime_db.scalars(
            select(RuntimeSourceSpan)
            .where(RuntimeSourceSpan.source_id == source.id)
            .order_by(RuntimeSourceSpan.id)
        ).all()
    )
    assert [span.span_kind for span in spans] == ["image_annotation", "image_annotation"]
    assert {span.locator_json["annotation_kind"] for span in spans} == {
        "upload_context",
        "metadata",
    }


def test_index_image_asset_defaults_to_configured_embedding_function(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images import indexing
    from anima_server.services.images.capabilities import ImageProcessingCapabilities

    asset = _asset(runtime_db, tmp_path, monkeypatch)
    monkeypatch.setattr(indexing, "generate_embedding", lambda text: _embedding(2.0), raising=False)

    result = indexing.index_image_asset(
        runtime_db,
        user_id=7,
        image_asset_id=asset.id,
        upload_context="User uploaded a dashboard screenshot.",
        capabilities=ImageProcessingCapabilities(),
    )

    assert result.embedding_count == 2
    assert runtime_db.scalar(
        select(func.count(RuntimeEmbedding.id)).where(
            RuntimeEmbedding.source_type == "image_annotation"
        )
    ) == 2
    assert runtime_db.get(RuntimeImageAsset, asset.id).status == "indexed"


def test_reindexing_unchanged_image_annotations_is_idempotent(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.capabilities import ImageProcessingCapabilities
    from anima_server.services.images.indexing import index_image_asset

    asset = _asset(runtime_db, tmp_path, monkeypatch)

    for _ in range(2):
        index_image_asset(
            runtime_db,
            user_id=7,
            image_asset_id=asset.id,
            upload_context="User asked about the dashboard screenshot.",
            embedding_fn=lambda text: _embedding(1.0),
            capabilities=ImageProcessingCapabilities(),
        )

    assert runtime_db.scalar(select(func.count(RuntimeImageAnnotation.id))) == 2
    assert runtime_db.scalar(
        select(func.count(RuntimeEmbedding.id)).where(
            RuntimeEmbedding.source_type == "image_annotation"
        )
    ) == 2


def test_reindexing_reactivates_reused_annotation_text(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.capabilities import ImageProcessingCapabilities
    from anima_server.services.images.indexing import index_image_asset

    asset = _asset(runtime_db, tmp_path, monkeypatch)

    for context in ("first context", "second context", "first context"):
        index_image_asset(
            runtime_db,
            user_id=7,
            image_asset_id=asset.id,
            upload_context=context,
            embedding_fn=lambda text: _embedding(1.0),
            capabilities=ImageProcessingCapabilities(),
        )

    active_context = runtime_db.scalar(
        select(RuntimeImageAnnotation).where(
            RuntimeImageAnnotation.image_asset_id == asset.id,
            RuntimeImageAnnotation.annotation_kind == "upload_context",
            RuntimeImageAnnotation.status == "active",
        )
    )
    assert active_context is not None
    assert "first context" in active_context.content_text
    assert runtime_db.scalar(
        select(RuntimeEmbedding.id).where(
            RuntimeEmbedding.source_type == "image_annotation",
            RuntimeEmbedding.source_id == active_context.id,
            RuntimeEmbedding.content_hash == active_context.content_hash,
        )
    ) is not None


def test_ocr_text_annotation_is_created_only_when_capability_is_declared(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.capabilities import ImageProcessingCapabilities
    from anima_server.services.images.indexing import index_image_asset

    asset = _asset(runtime_db, tmp_path, monkeypatch)
    calls = 0

    def extract_text(path: Path, image: RuntimeImageAsset) -> str:
        nonlocal calls
        assert path.exists()
        assert image.id == asset.id
        calls += 1
        return "Visible text: Q3 revenue dashboard"

    index_image_asset(
        runtime_db,
        user_id=7,
        image_asset_id=asset.id,
        upload_context="dashboard",
        embedding_fn=lambda text: _embedding(1.0),
        capabilities=ImageProcessingCapabilities(image_text_extraction=False),
        text_extraction_fn=extract_text,
    )
    assert calls == 0
    assert "ocr_text" not in set(
        runtime_db.scalars(select(RuntimeImageAnnotation.annotation_kind)).all()
    )

    index_image_asset(
        runtime_db,
        user_id=7,
        image_asset_id=asset.id,
        upload_context="dashboard",
        embedding_fn=lambda text: _embedding(1.0),
        capabilities=ImageProcessingCapabilities(image_text_extraction=True),
        text_extraction_fn=extract_text,
    )

    assert calls == 1
    assert "ocr_text" in set(
        runtime_db.scalars(select(RuntimeImageAnnotation.annotation_kind)).all()
    )


def test_caption_failure_does_not_fail_indexing(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.capabilities import ImageProcessingCapabilities
    from anima_server.services.images.indexing import index_image_asset

    asset = _asset(runtime_db, tmp_path, monkeypatch)

    def fail_caption(path: Path, image: RuntimeImageAsset) -> str:
        raise RuntimeError("vision offline")

    result = index_image_asset(
        runtime_db,
        user_id=7,
        image_asset_id=asset.id,
        upload_context="dashboard",
        embedding_fn=lambda text: _embedding(1.0),
        capabilities=ImageProcessingCapabilities(vision_caption=True),
        caption_fn=fail_caption,
    )

    assert result.annotations_indexed == 2
    assert "vision_caption" not in set(
        runtime_db.scalars(select(RuntimeImageAnnotation.annotation_kind)).all()
    )


def test_search_image_annotations_returns_owned_parent_assets(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.capabilities import ImageProcessingCapabilities
    from anima_server.services.images.indexing import index_image_asset
    from anima_server.services.images.rag import search_image_annotations

    owned = _asset(runtime_db, tmp_path, monkeypatch, user_id=7)
    other = _asset(runtime_db, tmp_path, monkeypatch, user_id=8)

    index_image_asset(
        runtime_db,
        user_id=7,
        image_asset_id=owned.id,
        upload_context="Invoice screenshot with Alpha Project total.",
        embedding_fn=lambda text: _embedding(1.0),
        capabilities=ImageProcessingCapabilities(),
    )
    index_image_asset(
        runtime_db,
        user_id=8,
        image_asset_id=other.id,
        upload_context="Invoice screenshot with Alpha Project total.",
        embedding_fn=lambda text: _embedding(1.0),
        capabilities=ImageProcessingCapabilities(),
    )

    results = search_image_annotations(
        runtime_db,
        user_id=7,
        query="Alpha Project invoice",
        embedding_fn=lambda text: _embedding(1.0),
        limit=5,
    )

    assert [(result.image_asset_id, result.user_id) for result in results] == [(owned.id, 7)]
    assert "Alpha Project" in results[0].snippet


def test_annotation_hash_matches_runtime_embedding_content_hash(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.capabilities import ImageProcessingCapabilities
    from anima_server.services.images.indexing import index_image_asset

    asset = _asset(runtime_db, tmp_path, monkeypatch)
    index_image_asset(
        runtime_db,
        user_id=7,
        image_asset_id=asset.id,
        upload_context="screen text",
        embedding_fn=lambda text: _embedding(1.0),
        capabilities=ImageProcessingCapabilities(),
    )

    annotation = runtime_db.scalar(
        select(RuntimeImageAnnotation).where(
            RuntimeImageAnnotation.annotation_kind == "upload_context"
        )
    )
    embedding = runtime_db.scalar(
        select(RuntimeEmbedding).where(
            RuntimeEmbedding.source_type == "image_annotation",
            RuntimeEmbedding.source_id == annotation.id,
        )
    )

    assert annotation.content_hash == hashlib.sha256(annotation.content_text.encode()).hexdigest()
    assert embedding.content_hash == annotation.content_hash
