"""Upgrade preview/legacy documents to Docling-quality artifacts.

Reparse re-extracts through the canonical parser, re-cuts chunks along real
section structure, re-embeds, and re-syncs source spans — then stamps the
document ``parse_quality="docling"``. Only docling-quality output is ever
written; if the pack is not ready the document is left untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeDocument
from anima_server.services.documents.chunking import chunk_pages_structured
from anima_server.services.documents.indexing import EmbeddingFn, embed_document_chunks
from anima_server.services.documents.parsing import (
    PARSE_QUALITY_DOCLING,
    extract_document_text,
)
from anima_server.services.documents.store import (
    get_document_for_user,
    replace_document_chunks,
    resolve_document_storage_path,
)
from anima_server.services.ingestion.adapters.documents import sync_document_source

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReparseResult:
    status: str  # "upgraded" | "pack_not_ready" | "not_found"
    chunk_count: int = 0


def reparse_document(
    runtime_db: Session,
    *,
    user_id: int,
    document_id: int,
    embedding_fn: EmbeddingFn | None = None,
) -> ReparseResult:
    document = get_document_for_user(runtime_db, user_id=user_id, document_id=document_id)
    if document is None:
        return ReparseResult(status="not_found")

    storage_path = resolve_document_storage_path(document.storage_path, user_id=user_id)
    outcome = extract_document_text(str(storage_path))
    if outcome.parse_quality != PARSE_QUALITY_DOCLING:
        return ReparseResult(status="pack_not_ready")

    chunks = chunk_pages_structured(outcome.pages)
    rows = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=chunks,
        parse_quality=PARSE_QUALITY_DOCLING,
    )
    embed_document_chunks(
        runtime_db,
        user_id=user_id,
        document_id=document.id,
        embedding_fn=embedding_fn,
    )
    sync_document_source(runtime_db, document=document, embedding_fn=embedding_fn)
    runtime_db.flush()
    logger.info("Reparsed document %s: %d docling chunks", document.id, len(rows))
    return ReparseResult(status="upgraded", chunk_count=len(rows))


def list_reparse_candidates(runtime_db: Session, *, user_id: int) -> list[int]:
    stmt = (
        select(RuntimeDocument.id)
        .where(
            RuntimeDocument.user_id == user_id,
            RuntimeDocument.status == "indexed",
            RuntimeDocument.parse_quality != PARSE_QUALITY_DOCLING,
        )
        .order_by(RuntimeDocument.id)
    )
    return list(runtime_db.scalars(stmt).all())


__all__ = ["ReparseResult", "list_reparse_candidates", "reparse_document"]
