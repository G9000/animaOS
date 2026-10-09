from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
from anima_server.services.corefs.sealed_runtime import (
    delete_runtime_embedding_records,
    delete_sealed_runtime_records,
    seal_runtime_fields,
)
from anima_server.services.documents.models import (
    DocumentRegistration,
    ExtractedDocumentChunk,
)

if TYPE_CHECKING:
    from anima_server.services.corefs.asset_authority import CoreFsByteSource


def resolve_document_byte_source(
    document: RuntimeDocument,
    *,
    user_id: int,
) -> CoreFsByteSource:
    """Read original bytes through the authenticated canonical catalog."""
    from anima_server.services.corefs.asset_authority import (
        open_corefs_byte_source,
        require_canonical_asset_session,
    )

    session = require_canonical_asset_session(user_id)
    object_uri = document.storage_path
    return open_corefs_byte_source(
        session=session,
        object_uri=object_uri,
        expected_kinds=frozenset({"attachment"}),
    )


def register_document(
    db: Session,
    registration: DocumentRegistration,
) -> RuntimeDocument:
    from anima_server.services.corefs.asset_authority import (
        CoreFsSourceError,
        open_corefs_byte_source,
        require_canonical_asset_session,
    )

    session = require_canonical_asset_session(registration.user_id)
    source = open_corefs_byte_source(
        session=session,
        object_uri=registration.storage_path,
        expected_kinds=frozenset({"attachment"}),
    )
    if (
        source.content_type != registration.mime_type
        or source.content_sha256 != registration.sha256
        or source.size != registration.size_bytes
    ):
        raise CoreFsSourceError("Canonical document registration changed identity.")
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
        filename="",
        mime_type="",
        storage_path="",
        sha256=registration.sha256,
        size_bytes=registration.size_bytes,
        status="registered",
        metadata_json=None,
    )
    seal_runtime_fields(
        db,
        row=document,
        row_type="runtime_document",
        owner_id=registration.user_id,
        payload={
            "filename": registration.filename,
            "mime_type": registration.mime_type,
            "storage_path": registration.storage_path,
            "metadata_json": _copy_metadata(registration.metadata_json),
        },
        placeholders={
            "filename": "",
            "mime_type": "",
            "storage_path": "",
            "metadata_json": None,
        },
    )
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
    parse_quality: str,
) -> list[RuntimeDocumentChunk]:
    document = db.get(RuntimeDocument, document_id)
    if document is None:
        raise ValueError(f"Document {document_id} does not exist.")

    now = datetime.now(UTC)
    document.status = "registered"
    document.parse_quality = parse_quality
    document.indexed_at = None
    document.updated_at = now

    old_chunk_ids = list(
        db.scalars(
            select(RuntimeDocumentChunk.id).where(
                RuntimeDocumentChunk.document_id == document_id,
            )
        ).all()
    )
    if old_chunk_ids:
        delete_runtime_embedding_records(
            db,
            owner_id=int(document.user_id),
            source_type="document_chunk",
            source_ids=old_chunk_ids,
        )
        delete_sealed_runtime_records(
            db,
            row_type="runtime_document_chunk",
            row_ids=old_chunk_ids,
            owner_id=int(document.user_id),
        )

    db.execute(
        delete(RuntimeDocumentChunk).where(
            RuntimeDocumentChunk.document_id == document_id,
        )
    )
    db.add(document)
    db.flush()

    inserted = [
        RuntimeDocumentChunk(
            document_id=document.id,
            user_id=document.user_id,
            chunk_index=chunk.chunk_index,
            content_text="",
            content_char_count=len(chunk.content_text),
            content_hash=_content_hash(chunk.content_text),
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            section_title=None,
            token_count=chunk.token_count,
            parse_quality=parse_quality,
            metadata_json=None,
        )
        for chunk in chunks
    ]
    if not inserted:
        return []

    for row, chunk in zip(inserted, chunks, strict=True):
        section_title = _bounded_section_title(chunk.section_title)
        metadata_json = _copy_metadata(chunk.metadata_json)
        seal_runtime_fields(
            db,
            row=row,
            row_type="runtime_document_chunk",
            owner_id=int(document.user_id),
            payload={
                "content_text": chunk.content_text,
                "section_title": section_title,
                "metadata_json": metadata_json,
            },
            placeholders={
                "content_text": "",
                "section_title": None,
                "metadata_json": None,
            },
        )
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


# RuntimeDocumentChunk.section_title is String(255); deep Docling heading
# paths must not fail chunk insert on length-enforcing backends.
_SECTION_TITLE_MAX_LENGTH = 255


def _bounded_section_title(value: str | None) -> str | None:
    if value is None or len(value) <= _SECTION_TITLE_MAX_LENGTH:
        return value
    return value[:_SECTION_TITLE_MAX_LENGTH].rstrip()


def _content_hash(content_text: str) -> str:
    return hashlib.sha256(content_text.encode()).hexdigest()


def _copy_metadata(
    metadata_json: dict[str, object] | None,
) -> dict[str, object] | None:
    if metadata_json is None:
        return None
    return dict(metadata_json)
