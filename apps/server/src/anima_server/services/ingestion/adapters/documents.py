from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeDocument,
    RuntimeDocumentChunk,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.models import (
    SourceArtifactInput,
    SourceIdentity,
    SourceSpanInput,
)
from anima_server.services.ingestion.retrieval import EmbeddingFn
from anima_server.services.ingestion.sources import register_source

DOCUMENT_ARTIFACT_KIND = "document_text"


def sync_document_source(
    db: Session,
    *,
    document: RuntimeDocument,
    embedding_fn: EmbeddingFn | None = None,
) -> tuple[RuntimeSource, list[RuntimeSourceArtifact], list[RuntimeSourceSpan]]:
    chunks = list(
        db.scalars(
            select(RuntimeDocumentChunk)
            .where(
                RuntimeDocumentChunk.document_id == document.id,
                RuntimeDocumentChunk.user_id == document.user_id,
            )
            .order_by(RuntimeDocumentChunk.chunk_index)
        ).all()
    )
    joined_text = "\n\n".join(chunk.content_text for chunk in chunks)
    artifact_hash = _content_hash(joined_text or document.sha256)
    source = register_source(
        db,
        SourceIdentity(
            user_id=document.user_id,
            kind="document",
            source_uri=f"runtime-document://{document.id}",
            content_hash=document.sha256,
            title=document.filename,
            media_type=document.mime_type,
            metadata_json={
                "runtime_document_id": document.id,
                "runtime_thread_id": document.thread_id,
                "runtime_workflow_run_id": document.workflow_run_id,
                "storage_path": document.storage_path,
                "size_bytes": document.size_bytes,
                "source_metadata": dict(document.metadata_json or {}),
            },
        ),
    )
    artifacts = [
        SourceArtifactInput(
            artifact_kind=DOCUMENT_ARTIFACT_KIND,
            content_text=joined_text,
            content_hash=artifact_hash,
            metadata_json={
                "runtime_document_id": document.id,
                "chunk_count": len(chunks),
            },
        )
    ]
    spans = [
        SourceSpanInput(
            artifact_kind=DOCUMENT_ARTIFACT_KIND,
            span_kind="document_chunk",
            locator_json=_document_chunk_locator(document=document, chunk=chunk),
            content_text=chunk.content_text,
            content_hash=chunk.content_hash,
            metadata_json=_document_chunk_metadata(chunk),
        )
        for chunk in chunks
    ]
    return (
        source,
        *replace_source_artifacts_and_spans(
            db,
            source=source,
            artifacts=artifacts,
            spans=spans,
            embedding_fn=embedding_fn,
            compile_knowledge=True,
        ),
    )


def _document_chunk_locator(
    *,
    document: RuntimeDocument,
    chunk: RuntimeDocumentChunk,
) -> dict[str, object]:
    locator: dict[str, object] = {
        "runtime_document_id": document.id,
        "runtime_document_chunk_id": chunk.id,
        "chunk_index": chunk.chunk_index,
    }
    if chunk.page_start is not None:
        locator["page_start"] = chunk.page_start
    if chunk.page_end is not None:
        locator["page_end"] = chunk.page_end
    return locator


def _document_chunk_metadata(chunk: RuntimeDocumentChunk) -> dict[str, object]:
    metadata: dict[str, object] = {
        "runtime_document_chunk_id": chunk.id,
        "chunk_index": chunk.chunk_index,
    }
    if chunk.section_title is not None:
        metadata["section_title"] = chunk.section_title
    if chunk.token_count is not None:
        metadata["token_count"] = chunk.token_count
    if chunk.metadata_json:
        metadata["source_metadata"] = dict(chunk.metadata_json)
    return metadata


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
