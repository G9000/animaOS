from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from anima_server.config import settings
from anima_server.models.runtime import RuntimeDocument
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent import pgvec_store as pgvec_module
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.documents import (
    DocumentRegistration,
    ExtractedDocumentChunk,
    embed_document_chunks,
    register_document,
    reparse,
    replace_document_chunks,
)
from anima_server.services.documents.parsing import (
    DocumentAwaitingParserError,
    DocumentParsingError,
    ExtractionOutcome,
)
from anima_server.services.documents.pdf_text import PageText
from sqlalchemy import select
from sqlalchemy.orm import Session

pytest_plugins = ("conftest_runtime",)

# Derived from the actual bound column rather than hardcoded: the pgvector
# column dimension is fixed once per process (baked in at first import of
# RuntimeEmbedding from the then-current default embedding provider), so a
# literal here would drift out of sync whenever that default changes.
_TEST_EMBEDDING_DIM = RuntimeEmbedding.__table__.c.embedding.type.dim


def _embedding(*values: float) -> list[float]:
    return [*values, *([0.0] * (_TEST_EMBEDDING_DIM - len(values)))]


def _patch_pgvec_upsert(monkeypatch: Any) -> None:
    def fake_upsert_source(
        self: Any,
        user_id: int,
        *,
        source_type: str,
        source_id: int,
        content: str,
        embedding: list[float],
        category: str = "document",
        importance: int = 3,
    ) -> None:
        row = self._db.scalar(
            select(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == source_type,
                RuntimeEmbedding.source_id == source_id,
            )
        )
        if row is None:
            row = RuntimeEmbedding(
                user_id=user_id,
                source_type=source_type,
                source_id=source_id,
                content_hash=RuntimeEmbedding.compute_content_hash(content),
                embedding_checksum=compute_embedding_checksum(embedding),
                embedding=embedding,
                content_preview=content[:200],
                category=category,
                importance=importance,
            )
            self._db.add(row)
        else:
            row.content_hash = RuntimeEmbedding.compute_content_hash(content)
            row.embedding_checksum = compute_embedding_checksum(embedding)
            row.embedding = embedding
            row.content_preview = content[:200]
            row.category = category
            row.importance = importance
        self._db.flush()

    monkeypatch.setattr(pgvec_module.PgVecStore, "upsert_source", fake_upsert_source)


@pytest.fixture()
def preview_document(
    runtime_db: Session,
    tmp_path: Path,
    monkeypatch: Any,
) -> RuntimeDocument:
    """An indexed document whose chunks were produced at preview quality."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _patch_pgvec_upsert(monkeypatch)

    user_id = 7
    document = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=user_id,
            filename="manual.pdf",
            mime_type="application/pdf",
            storage_path=f".anima/documents/{user_id}/manual.pdf",
            sha256="a" * 64,
            size_bytes=2048,
        ),
    )
    replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=0,
                content_text="preview body text",
                page_start=1,
                page_end=1,
            ),
        ],
        parse_quality="preview",
    )
    embed_document_chunks(
        runtime_db,
        user_id=user_id,
        document_id=document.id,
        embedding_fn=lambda text: _embedding(float(len(text)), 1.0),
    )
    runtime_db.flush()
    assert document.status == "indexed"
    assert document.parse_quality == "preview"
    return document


def test_reparse_upgrades_preview_document(
    runtime_db: Session,
    preview_document: RuntimeDocument,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        reparse,
        "extract_document_text",
        lambda path: ExtractionOutcome(
            pages=[PageText(page_number=1, text="# Section\n\nUpgraded body")],
            parse_quality="docling",
        ),
    )

    result = reparse.reparse_document(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
        embedding_fn=lambda text: _embedding(0.1, 0.2, 0.3),
    )

    assert result.status == "upgraded"
    assert result.chunk_count >= 1
    assert preview_document.parse_quality == "docling"
    assert preview_document.status == "indexed"


def test_reparse_uses_default_embedder_for_source_sync_when_none_passed(
    runtime_db: Session,
    preview_document: RuntimeDocument,
    monkeypatch: Any,
) -> None:
    """The API route calls reparse_document() without an embedding_fn.
    embed_document_chunks() falls back to the default embedder internally,
    but sync_document_source()/replace_source_artifacts_and_spans() treat a
    None embedding_fn as "skip embedding" (see artifacts.py). If reparse
    forwards the caller's None straight through, the new source spans and
    compiled knowledge concepts are left without dense embeddings even
    though the chunks themselves got upgraded embeddings. reparse_document
    must resolve its own default embedder once and pass that (non-None)
    function to sync_document_source too."""
    monkeypatch.setattr(
        reparse,
        "extract_document_text",
        lambda path: ExtractionOutcome(
            pages=[PageText(page_number=1, text="# Section\n\nUpgraded body")],
            parse_quality="docling",
        ),
    )

    recorded: dict[str, Any] = {}
    real_sync_document_source = reparse.sync_document_source

    def recording_sync_document_source(*args: Any, **kwargs: Any) -> Any:
        recorded["embedding_fn"] = kwargs.get("embedding_fn")
        return real_sync_document_source(*args, **kwargs)

    monkeypatch.setattr(
        reparse, "sync_document_source", recording_sync_document_source
    )
    monkeypatch.setattr(
        reparse, "generate_embedding", lambda text: _embedding(0.4, 0.5, 0.6)
    )

    result = reparse.reparse_document(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
    )

    assert result.status == "upgraded"
    assert recorded["embedding_fn"] is not None


def test_reparse_returns_upgraded_unembedded_when_embedding_provider_down(
    runtime_db: Session,
    preview_document: RuntimeDocument,
    monkeypatch: Any,
) -> None:
    """If any docling chunk fails to embed, the document must not be
    reported as a plain "upgraded" success: it stays search-invisible
    (status != "indexed") and the caller needs to know retrieval is
    degraded."""
    monkeypatch.setattr(
        reparse,
        "extract_document_text",
        lambda path: ExtractionOutcome(
            pages=[PageText(page_number=1, text="# Section\n\nUpgraded body")],
            parse_quality="docling",
        ),
    )

    result = reparse.reparse_document(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
        embedding_fn=lambda text: None,
    )

    assert result.status == "upgraded_unembedded"
    assert result.chunk_count >= 1
    assert preview_document.parse_quality == "docling"
    assert preview_document.status != "indexed"


def test_reparse_returns_parse_degraded_when_docling_crashes_with_pack_ready(
    runtime_db: Session,
    preview_document: RuntimeDocument,
    monkeypatch: Any,
) -> None:
    """A docling crash on this specific file (pack fully ready) is a
    different failure than "pack still downloading" and must be reported
    distinctly so the caller knows a retry (not a wait) is warranted."""
    monkeypatch.setattr(reparse, "parsing_pack_ready", lambda: True)
    monkeypatch.setattr(
        reparse,
        "extract_document_text",
        lambda path: ExtractionOutcome(
            pages=[PageText(page_number=1, text="still preview")],
            parse_quality="preview",
        ),
    )

    result = reparse.reparse_document(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
    )

    assert result.status == "parse_degraded"
    assert preview_document.parse_quality == "preview"


def test_reparse_noop_when_pack_not_ready(
    runtime_db: Session,
    preview_document: RuntimeDocument,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        reparse,
        "extract_document_text",
        lambda path: ExtractionOutcome(
            pages=[PageText(page_number=1, text="still preview")],
            parse_quality="preview",
        ),
    )

    result = reparse.reparse_document(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
    )

    assert result.status == "pack_not_ready"
    assert preview_document.parse_quality == "preview"


def test_reparse_returns_pack_not_ready_when_extraction_raises_awaiting_parser(
    runtime_db: Session,
    preview_document: RuntimeDocument,
    monkeypatch: Any,
) -> None:
    """A scanned/textless PDF makes extract_document_text raise
    DocumentAwaitingParserError (pack still downloading) instead of
    returning a preview outcome. reparse_document must translate that into
    the same "pack_not_ready" status as the non-raising preview path, and
    must leave the document's existing chunks/quality untouched — the
    caller is expected to wait and retry, not to see any partial update."""

    def raise_awaiting(path: str) -> Any:
        raise DocumentAwaitingParserError("no extractable text; pack downloading")

    monkeypatch.setattr(reparse, "extract_document_text", raise_awaiting)

    result = reparse.reparse_document(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
    )

    assert result.status == "pack_not_ready"
    assert preview_document.parse_quality == "preview"
    assert preview_document.status == "indexed"


def test_reparse_returns_parser_unavailable_when_extraction_raises_parsing_error(
    runtime_db: Session,
    preview_document: RuntimeDocument,
    monkeypatch: Any,
) -> None:
    """When the pack is absent/errored (docling extra not installed, or a
    prior download failed), extract_document_text raises a plain
    DocumentParsingError with an actionable message. reparse_document must
    surface that as a distinct "parser_unavailable" status carrying the
    message, rather than letting the exception bypass the structured
    status mapping."""

    def raise_parsing_error(path: str) -> Any:
        raise DocumentParsingError(
            "the quality parser is not installed; install the docling extra"
        )

    monkeypatch.setattr(reparse, "extract_document_text", raise_parsing_error)

    result = reparse.reparse_document(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
    )

    assert result.status == "parser_unavailable"
    assert result.detail is not None
    assert "docling extra" in result.detail
    assert preview_document.parse_quality == "preview"
    assert preview_document.status == "indexed"


def test_reparse_returns_not_found_for_missing_document(
    runtime_db: Session,
) -> None:
    result = reparse.reparse_document(
        runtime_db,
        user_id=999,
        document_id=424242,
    )

    assert result.status == "not_found"
    assert result.chunk_count == 0


def test_list_reparse_candidates_returns_non_docling_indexed(
    runtime_db: Session,
    preview_document: RuntimeDocument,
) -> None:
    assert reparse.list_reparse_candidates(
        runtime_db, user_id=preview_document.user_id
    ) == [preview_document.id]


def test_list_reparse_candidates_excludes_docling_documents(
    runtime_db: Session,
    preview_document: RuntimeDocument,
) -> None:
    preview_document.parse_quality = "docling"
    runtime_db.flush()

    assert (
        reparse.list_reparse_candidates(runtime_db, user_id=preview_document.user_id)
        == []
    )


def test_list_reparse_candidates_excludes_unindexed_documents(
    runtime_db: Session,
    preview_document: RuntimeDocument,
) -> None:
    preview_document.status = "registered"
    runtime_db.flush()

    assert (
        reparse.list_reparse_candidates(runtime_db, user_id=preview_document.user_id)
        == []
    )


def test_mark_docling_reparse_failed_excludes_within_cooldown(
    runtime_db: Session,
    preview_document: RuntimeDocument,
) -> None:
    # Baseline: an indexed preview document is a candidate.
    assert reparse.list_reparse_candidates(
        runtime_db, user_id=preview_document.user_id, failure_cooldown_hours=24
    ) == [preview_document.id]

    # After Docling fails on it, the cooldown excludes it so it can't
    # re-consume the per-cycle budget and starve valid documents behind it.
    reparse.mark_docling_reparse_failed(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
    )
    runtime_db.flush()

    assert reparse.list_reparse_candidates(
        runtime_db, user_id=preview_document.user_id, failure_cooldown_hours=24
    ) == []


def test_list_reparse_candidates_retries_failed_document_after_cooldown(
    runtime_db: Session,
    preview_document: RuntimeDocument,
) -> None:
    # A failure recorded 25h ago is past a 24h cooldown, so the document is
    # eligible again — a transient Docling failure gets retried, not
    # permanently abandoned.
    from datetime import UTC, datetime, timedelta

    stale = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    preview_document.metadata_json = {"docling_reparse_failed_at": stale}
    runtime_db.add(preview_document)
    runtime_db.flush()

    assert reparse.list_reparse_candidates(
        runtime_db, user_id=preview_document.user_id, failure_cooldown_hours=24
    ) == [preview_document.id]


def test_list_reparse_candidates_ignores_failure_marker_when_cooldown_disabled(
    runtime_db: Session,
    preview_document: RuntimeDocument,
) -> None:
    # cooldown of 0/None disables the filter — a marked document is still a
    # candidate (retry every cycle).
    reparse.mark_docling_reparse_failed(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
    )
    runtime_db.flush()

    assert reparse.list_reparse_candidates(
        runtime_db, user_id=preview_document.user_id, failure_cooldown_hours=0
    ) == [preview_document.id]
    assert reparse.list_reparse_candidates(
        runtime_db, user_id=preview_document.user_id
    ) == [preview_document.id]
