from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.documents.models import (
    DocumentRegistration,
    ExtractedDocumentChunk,
)


class DocumentStoragePathError(ValueError):
    pass


def resolve_document_storage_path(storage_path: str, *, user_id: int) -> Path:
    stripped = storage_path.strip()
    windows_path = PureWindowsPath(stripped)
    path = Path(stripped)
    if (
        not stripped
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
    ):
        raise DocumentStoragePathError("Invalid document storage path.")

    try:
        data_root = settings.data_dir.resolve()
        resolved_path = (data_root / path).resolve()
        resolved_path.relative_to(data_root)
        _require_user_document_path(resolved_path, data_root=data_root, user_id=user_id)
    except (OSError, ValueError) as exc:
        raise DocumentStoragePathError("Invalid document storage path.") from exc

    return resolved_path


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


def _require_user_document_path(
    resolved_path: Path,
    *,
    data_root: Path,
    user_id: int,
) -> None:
    user_roots = (
        data_root / ".anima" / "documents" / str(user_id),
        data_root / "users" / str(user_id) / "attachments",
    )
    for root in user_roots:
        try:
            resolved_path.relative_to(root)
        except ValueError:
            continue
        return
    raise ValueError


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

    now = datetime.now(UTC)
    document.status = "registered"
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
    db.add(document)
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
