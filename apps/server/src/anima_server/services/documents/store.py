from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.documents.models import (
    DocumentRegistration,
    ExtractedDocumentChunk,
)


def register_document(
    db: Session,
    registration: DocumentRegistration,
) -> RuntimeDocument:
    existing = db.scalar(
        select(RuntimeDocument).where(
            RuntimeDocument.user_id == registration.user_id,
            RuntimeDocument.sha256 == registration.sha256,
        )
    )
    if existing is not None:
        return existing

    document = RuntimeDocument(
        user_id=registration.user_id,
        thread_id=registration.thread_id,
        workflow_run_id=registration.workflow_run_id,
        filename=registration.filename,
        mime_type=registration.mime_type,
        storage_path=registration.storage_path,
        sha256=registration.sha256,
        size_bytes=registration.size_bytes,
        status="registered",
        metadata_json=_copy_metadata(registration.metadata_json),
    )
    db.add(document)
    db.flush()
    return document


def set_document_status(
    db: Session,
    *,
    document_id: int,
    status: str,
    indexed: bool = False,
) -> RuntimeDocument | None:
    document = db.get(RuntimeDocument, document_id)
    if document is None:
        return None

    now = datetime.now(UTC)
    document.status = status
    document.updated_at = now
    if indexed:
        document.indexed_at = now

    db.add(document)
    db.flush()
    return document


def replace_document_chunks(
    db: Session,
    *,
    document_id: int,
    chunks: Sequence[ExtractedDocumentChunk],
) -> list[RuntimeDocumentChunk]:
    document = db.get(RuntimeDocument, document_id)
    if document is None:
        raise ValueError(f"Document {document_id} does not exist.")

    old_chunk_ids = list(
        db.scalars(
            select(RuntimeDocumentChunk.id).where(
                RuntimeDocumentChunk.document_id == document_id,
            )
        ).all()
    )
    if old_chunk_ids:
        db.execute(
            delete(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == document.user_id,
                RuntimeEmbedding.source_type == "document_chunk",
                RuntimeEmbedding.source_id.in_(old_chunk_ids),
            )
        )

    db.execute(
        delete(RuntimeDocumentChunk).where(
            RuntimeDocumentChunk.document_id == document_id,
        )
    )
    db.flush()

    inserted = [
        RuntimeDocumentChunk(
            document_id=document.id,
            user_id=document.user_id,
            chunk_index=chunk.chunk_index,
            content_text=chunk.content_text,
            content_hash=_content_hash(chunk.content_text),
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            section_title=chunk.section_title,
            token_count=chunk.token_count,
            metadata_json=_copy_metadata(chunk.metadata_json),
        )
        for chunk in chunks
    ]
    if not inserted:
        return []

    db.add_all(inserted)
    db.flush()
    return sorted(inserted, key=lambda chunk: chunk.chunk_index)


def list_document_chunks(
    db: Session,
    *,
    document_id: int,
) -> list[RuntimeDocumentChunk]:
    return list(
        db.scalars(
            select(RuntimeDocumentChunk)
            .where(RuntimeDocumentChunk.document_id == document_id)
            .order_by(RuntimeDocumentChunk.chunk_index)
        ).all()
    )


def get_document_for_user(
    db: Session,
    *,
    user_id: int,
    document_id: int,
) -> RuntimeDocument | None:
    return db.scalar(
        select(RuntimeDocument).where(
            RuntimeDocument.id == document_id,
            RuntimeDocument.user_id == user_id,
        )
    )


def _content_hash(content_text: str) -> str:
    return hashlib.sha256(content_text.encode()).hexdigest()


def _copy_metadata(
    metadata_json: dict[str, object] | None,
) -> dict[str, object] | None:
    if metadata_json is None:
        return None
    return dict(metadata_json)
