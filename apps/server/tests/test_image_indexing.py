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
from sqlalchemy import func, select

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


def _runtime_image_projection(db, *, user_id, data, mime_type, filename=None, metadata_json=None):
    """Disposable Runtime projection; original bytes are supplied by a mocked CoreFS boundary."""
    from types import SimpleNamespace

    from anima_server.services.corefs.catalog_reader import stable_opaque_id

    digest = hashlib.sha256(data).hexdigest()
    stable_id = stable_opaque_id("test-original-image", f"{user_id}:{digest}")
    asset = RuntimeImageAsset(
        user_id=user_id,
        filename=filename,
        mime_type=mime_type,
        storage_path=f"corefs://object/{stable_id}",
        sha256=digest,
        size_bytes=len(data),
        status="registered",
        retention_state="transient",
        metadata_json=metadata_json,
    )
    asset._test_original_bytes = data
    db.add(asset)
    db.flush()
    return SimpleNamespace(asset=asset)


def _mock_authoritative_projections(monkeypatch):
    """Mock authenticated bytes, while testing actual Runtime indexing and ownership queries."""
    import sys
    from types import SimpleNamespace

    from anima_server.services.corefs import asset_authority
    from anima_server.services.documents import store as documents_store
    from anima_server.services.images import indexing
    from anima_server.services.images import store as images_store
    from anima_server.services.ingestion import sources

    authenticated_sources = {}
    original_register = sources.register_source
    original_document = documents_store.register_document

    def register_verified_source(db, identity):
        authenticated_sources[identity.source_uri] = SimpleNamespace(
            content_sha256=identity.content_hash,
            stable_id=identity.source_uri.rsplit("/", 1)[-1],
        )
        return original_register(db, identity)

    def register_verified_document(db, registration):
        authenticated_sources[registration.storage_path] = SimpleNamespace(
            content_sha256=registration.sha256,
            content_type=registration.mime_type,
            size=registration.size_bytes,
            stable_id=registration.storage_path.rsplit("/", 1)[-1],
        )
        return original_document(db, registration)

    register_verified_source._test_original_registration = original_register
    register_verified_document._test_original_registration = original_document
    # Earlier imports and imports made during a test see the same explicit boundary.
    for module in list(sys.modules.values()):
        if module is None:
            continue
        if (
            getattr(
                getattr(module, "register_source", None),
                "_test_original_registration",
                getattr(module, "register_source", None),
            )
            is original_register
        ):
            monkeypatch.setattr(module, "register_source", register_verified_source)
        if (
            getattr(
                getattr(module, "register_document", None),
                "_test_original_registration",
                getattr(module, "register_document", None),
            )
            is original_document
        ):
            monkeypatch.setattr(module, "register_document", register_verified_document)
    monkeypatch.setattr(
        asset_authority,
        "require_canonical_asset_session",
        lambda user_id: SimpleNamespace(user_id=user_id),
    )
    monkeypatch.setattr(
        asset_authority,
        "open_corefs_byte_source",
        lambda *, session, object_uri, **kwargs: authenticated_sources[object_uri],
    )

    def image_source(asset, *, user_id):
        assert asset.user_id == user_id
        return SimpleNamespace(
            stable_id=asset.storage_path.rsplit("/", 1)[-1],
            content_sha256=asset.sha256,
            content_type=asset.mime_type,
            size=asset.size_bytes,
            read_all=lambda **kwargs: asset._test_original_bytes,
        )

    monkeypatch.setattr(indexing, "resolve_image_byte_source", image_source)
    monkeypatch.setattr(images_store, "resolve_image_byte_source", image_source)
    monkeypatch.setattr(
        documents_store,
        "resolve_document_byte_source",
        lambda document, *, user_id: authenticated_sources[document.storage_path],
    )

    def cleanup_imported_aliases():
        for module in list(sys.modules.values()):
            if module is None:
                continue
            for name, original in (
                ("register_source", original_register),
                ("register_document", original_document),
            ):
                value = getattr(module, name, None)
                if getattr(value, "_test_original_registration", None) is original:
                    setattr(module, name, original)

    return cleanup_imported_aliases


@pytest.fixture(autouse=True)
def _authoritative_byte_source_boundary(monkeypatch):
    cleanup = _mock_authoritative_projections(monkeypatch)
    yield
    cleanup()


def _embedding(seed: float) -> list[float]:
    return [seed, *([0.0] * (_TEST_DIM - 1))]


def _asset(runtime_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, user_id: int = 7):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return _runtime_image_projection(
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
            RuntimeSource.source_uri == asset.storage_path,
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
    assert (
        runtime_db.scalar(
            select(func.count(RuntimeEmbedding.id)).where(
                RuntimeEmbedding.source_type == "image_annotation"
            )
        )
        == 2
    )
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
    assert (
        runtime_db.scalar(
            select(func.count(RuntimeEmbedding.id)).where(
                RuntimeEmbedding.source_type == "image_annotation"
            )
        )
        == 2
    )


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
    assert (
        runtime_db.scalar(
            select(RuntimeEmbedding.id).where(
                RuntimeEmbedding.source_type == "image_annotation",
                RuntimeEmbedding.source_id == active_context.id,
                RuntimeEmbedding.content_hash == active_context.content_hash,
            )
        )
        is not None
    )


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
        assert path.read_all().startswith(b"\x89PNG")
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
