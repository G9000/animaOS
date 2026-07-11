"""Contextual retrieval blurbs for document chunks.

Anthropic-style contextual chunks: at ingestion (post-chunking,
pre-embedding) each chunk gets a short LLM-generated context line — "this
chunk is from section X of document Y and covers Z" — stored in the chunk's
metadata and prepended to the chunk text for embedding and lexical indexing
only. Evidence text shown to the model or user never includes the blurb.

Gated by ``ANIMA_CONTEXTUAL_CHUNKS`` (default off).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime import RuntimeDocumentChunk
from anima_server.services.documents.store import (
    get_document_for_user,
    list_document_chunks,
)

logger = logging.getLogger(__name__)

CONTEXT_BLURB_METADATA_KEY = "context_blurb"

_BLURB_MAX_CHARS = 600
_BLURB_CHUNK_EXCERPT_CHARS = 1200

_BLURB_SYSTEM_PROMPT = (
    "You write retrieval context lines for document chunks. Given a chunk "
    "and its document, respond with ONE plain sentence of 50-100 tokens "
    "situating the chunk: which document and section it is from and what it "
    "covers, naming the key entities and terms a searcher would use. No "
    "preamble, no quotes, no markdown."
)


def generate_document_chunk_blurbs(
    runtime_db: Session,
    *,
    user_id: int,
    document_id: int,
    llm_client: Any | None = None,
) -> int:
    """Generate and store context blurbs for a document's chunks.

    Sync contract (callable from the ingestion workflow); the async LLM is
    driven through the same bridge the embedding path uses. Returns the
    number of blurbs written; a no-op when the flag is off, the document is
    over the chunk budget (logged), or the model is unavailable.
    """
    if settings.contextual_chunks != "on":
        return 0
    document = get_document_for_user(
        runtime_db, user_id=user_id, document_id=document_id
    )
    if document is None:
        return 0
    chunks = list_document_chunks(runtime_db, document_id=document.id)
    if not chunks:
        return 0
    if len(chunks) > settings.contextual_chunks_max_chunks:
        logger.info(
            "Skipping contextual blurbs for document %s: %d chunks exceeds "
            "the %d-chunk budget",
            document.id,
            len(chunks),
            settings.contextual_chunks_max_chunks,
        )
        return 0

    written = 0
    for chunk in chunks:
        metadata = dict(chunk.metadata_json or {})
        if isinstance(metadata.get(CONTEXT_BLURB_METADATA_KEY), str):
            continue
        try:
            blurb = _generate_blurb(document.filename, chunk, llm_client)
        except Exception:
            logger.warning(
                "Contextual blurb generation failed for document %s chunk %s; "
                "continuing without blurbs",
                document.id,
                chunk.id,
                exc_info=True,
            )
            break
        if not blurb:
            continue
        metadata[CONTEXT_BLURB_METADATA_KEY] = blurb[:_BLURB_MAX_CHARS]
        chunk.metadata_json = metadata
        runtime_db.add(chunk)
        written += 1
    runtime_db.flush()
    return written


def chunk_context_blurb(chunk: RuntimeDocumentChunk) -> str | None:
    """The stored context blurb for *chunk*, honoring the feature flag."""
    if settings.contextual_chunks != "on":
        return None
    blurb = (chunk.metadata_json or {}).get(CONTEXT_BLURB_METADATA_KEY)
    if isinstance(blurb, str) and blurb.strip():
        return blurb.strip()
    return None


def chunk_index_text(chunk: RuntimeDocumentChunk) -> str:
    """Chunk text for embedding/lexical indexing: blurb-prefixed when present."""
    blurb = chunk_context_blurb(chunk)
    if blurb:
        return f"{blurb}\n\n{chunk.content_text}"
    return chunk.content_text


def _generate_blurb(
    filename: str,
    chunk: RuntimeDocumentChunk,
    llm_client: Any | None,
) -> str:
    from anima_server.services.agent.llm_json import call_llm_for_text
    from anima_server.services.documents.indexing import _run_awaitable

    section = f"\nSection: {chunk.section_title}" if chunk.section_title else ""
    pages = ""
    if chunk.page_start is not None:
        pages = (
            f"\nPages: {chunk.page_start}"
            if chunk.page_end in (None, chunk.page_start)
            else f"\nPages: {chunk.page_start}-{chunk.page_end}"
        )
    excerpt = " ".join(chunk.content_text.split())[:_BLURB_CHUNK_EXCERPT_CHARS]
    prompt = (
        f"Document: {filename}{section}{pages}\n"
        f"Chunk {chunk.chunk_index}:\n{excerpt}\n\n"
        "Write the context sentence now."
    )
    blurb = _run_awaitable(
        call_llm_for_text(_BLURB_SYSTEM_PROMPT, prompt, client=llm_client)
    )
    return " ".join((blurb or "").split())


__all__ = [
    "CONTEXT_BLURB_METADATA_KEY",
    "chunk_context_blurb",
    "chunk_index_text",
    "generate_document_chunk_blurbs",
]
