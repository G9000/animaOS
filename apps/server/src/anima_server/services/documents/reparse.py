"""Upgrade preview/legacy documents to Docling-quality artifacts.

Reparse re-extracts through the canonical parser, re-cuts chunks along real
section structure, re-embeds, and re-syncs source spans — then stamps the
document ``parse_quality="docling"``. Only docling-quality output is ever
written; if the pack is not ready the document is left untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeDocument
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.documents.chunking import chunk_pages_structured
from anima_server.services.documents.indexing import EmbeddingFn, embed_document_chunks
from anima_server.services.documents.parsing import (
    PARSE_QUALITY_DOCLING,
    DocumentAwaitingParserError,
    DocumentParsingError,
    extract_document_text,
)
from anima_server.services.documents.parsing_pack import parsing_pack_ready
from anima_server.services.documents.store import (
    get_document_for_user,
    replace_document_chunks,
    resolve_document_storage_path,
)
from anima_server.services.ingestion.adapters.documents import sync_document_source

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReparseResult:
    # "upgraded" | "upgraded_unembedded" | "parse_degraded"
    # | "pack_not_ready" | "parser_unavailable" | "not_found"
    status: str
    chunk_count: int = 0
    # Actionable detail carried alongside "parser_unavailable" so the API
    # route can surface it without re-deriving the exception message.
    detail: str | None = None


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

    # Mirror embed_document_chunks' own default resolution (indexing.py) so a
    # caller that omits embedding_fn (e.g. the reparse API route) still gets
    # dense embeddings on the synced source spans and compiled knowledge
    # concepts, not just the document chunks. sync_document_source/artifacts.py
    # treat a None embedding_fn as "skip embedding" — that contract is correct
    # for callers like pdf_workflow.py that already embedded everything and
    # pass None deliberately, but reparse must never forward a "no embedder
    # given" None into that path.
    effective_embedding_fn = embedding_fn or generate_embedding

    storage_path = resolve_document_storage_path(document.storage_path, user_id=user_id)
    try:
        outcome = extract_document_text(str(storage_path))
    except DocumentAwaitingParserError:
        # Scanned/textless PDF and the parsing pack is still downloading (or
        # just started its first download) — this is the same "wait and
        # retry" condition as the non-raising preview path below, just
        # reached via extract_document_text's exception path instead of a
        # preview-quality outcome. Map it onto the same status so callers
        # don't have to special-case it.
        return ReparseResult(status="pack_not_ready")
    except DocumentParsingError as exc:
        # DocumentAwaitingParserError is a DocumentParsingError subclass, so
        # this branch only catches the *other* variants: the docling extra
        # isn't installed, or a prior pack download failed outright. Those
        # are actionable, not transient — surface the message so the caller
        # knows what to actually do (install the extra / retry the download)
        # instead of getting a generic 503.
        logger.warning(
            "Reparse of document %s cannot run the quality parser: %s",
            document.id,
            exc,
        )
        return ReparseResult(status="parser_unavailable", detail=str(exc))
    if outcome.parse_quality != PARSE_QUALITY_DOCLING:
        if parsing_pack_ready():
            # The pack is ready but docling crashed on this specific file
            # (see parsing.extract_document_text) — the caller needs to know
            # this is a per-document failure, not a "pack still downloading"
            # state, so it can retry rather than wait.
            logger.warning(
                "Reparse of document %s produced preview output despite a ready "
                "parsing pack; docling likely crashed on this file",
                document.id,
            )
            return ReparseResult(status="parse_degraded")
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
        embedding_fn=effective_embedding_fn,
    )
    sync_document_source(
        runtime_db,
        document=document,
        embedding_fn=effective_embedding_fn,
        compile_knowledge=True,
    )
    runtime_db.flush()

    if document.status != "indexed":
        # Docling re-chunked the document (better text, now stored), but at
        # least one chunk failed to embed (embedding provider down/erroring).
        # The document is left at status != "indexed" by embed_document_chunks,
        # which makes it invisible to search — the caller must be told
        # retrieval is degraded rather than hearing a plain "upgraded".
        logger.warning(
            "Reparsed document %s to %d docling chunks but embedding is "
            "incomplete; document remains unembedded (status=%s)",
            document.id,
            len(rows),
            document.status,
        )
        return ReparseResult(status="upgraded_unembedded", chunk_count=len(rows))

    logger.info("Reparsed document %s: %d docling chunks", document.id, len(rows))
    return ReparseResult(status="upgraded", chunk_count=len(rows))


_DOCLING_REPARSE_FAILED_AT_KEY = "docling_reparse_failed_at"


def list_reparse_candidates(
    runtime_db: Session,
    *,
    user_id: int,
    failure_cooldown_hours: int | None = None,
) -> list[int]:
    """Documents eligible for auto-reparse, oldest first.

    A document that Docling failed to parse (``mark_docling_reparse_failed``)
    is excluded while its failure is within ``failure_cooldown_hours`` — this
    stops a single persistently-unparseable file, which sorts first by id and
    stays an indexed non-docling candidate, from consuming the per-cycle
    budget on every cycle and starving valid documents behind it. The
    exclusion is a cooldown, not permanent, so a transient failure still gets
    retried later. ``failure_cooldown_hours=None`` (or 0) disables the filter.
    """
    stmt = (
        select(RuntimeDocument.id, RuntimeDocument.metadata_json)
        .where(
            RuntimeDocument.user_id == user_id,
            RuntimeDocument.status == "indexed",
            RuntimeDocument.parse_quality != PARSE_QUALITY_DOCLING,
        )
        .order_by(RuntimeDocument.id)
    )
    rows = runtime_db.execute(stmt).all()
    if not failure_cooldown_hours:
        return [doc_id for doc_id, _ in rows]

    cutoff = datetime.now(UTC) - timedelta(hours=failure_cooldown_hours)
    candidates: list[int] = []
    for doc_id, metadata in rows:
        failed_at = _parse_failed_at((metadata or {}).get(_DOCLING_REPARSE_FAILED_AT_KEY))
        if failed_at is not None and failed_at > cutoff:
            continue
        candidates.append(doc_id)
    return candidates


def mark_docling_reparse_failed(
    runtime_db: Session, *, user_id: int, document_id: int
) -> None:
    """Record that Docling failed on this document, so the auto-reparse loop
    can apply a cooldown (see ``list_reparse_candidates``).

    Stored in the document's ``metadata_json`` — no schema change — and only
    the marker is written (the caller is expected to have rolled back any
    failed reparse mutations first, then commit this). Idempotent per call:
    it just refreshes the timestamp.
    """
    document = get_document_for_user(
        runtime_db, user_id=user_id, document_id=document_id
    )
    if document is None:
        return
    metadata = dict(document.metadata_json or {})
    metadata[_DOCLING_REPARSE_FAILED_AT_KEY] = datetime.now(UTC).isoformat()
    document.metadata_json = metadata
    runtime_db.add(document)
    runtime_db.flush()


def _parse_failed_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = [
    "ReparseResult",
    "list_reparse_candidates",
    "mark_docling_reparse_failed",
    "reparse_document",
]
