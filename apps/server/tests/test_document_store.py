from __future__ import annotations

import hashlib

from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
from anima_server.services.documents.models import (
    DocumentRegistration,
    ExtractedDocumentChunk,
)
from anima_server.services.documents.store import (
    get_document_for_user,
    list_document_chunks,
    register_document,
    replace_document_chunks,
    set_document_status,
)
from sqlalchemy import ForeignKeyConstraint, func, inspect, select

pytest_plugins = ("conftest_runtime",)


def _constraint_columns(model: type, name: str) -> tuple[str, ...]:
    constraint = next(
        constraint
        for constraint in model.__table__.constraints
        if constraint.name == name
    )
    return tuple(column.name for column in constraint.columns)


def _foreign_key_constraint(model: type, name: str) -> ForeignKeyConstraint:
    constraint = next(
        constraint
        for constraint in model.__table__.constraints
        if constraint.name == name
    )
    assert isinstance(constraint, ForeignKeyConstraint)
    return constraint


def _registration(
    *,
    user_id: int = 1,
    filename: str = "notes.pdf",
    sha256: str = "a" * 64,
    metadata_json: dict[str, object] | None = None,
) -> DocumentRegistration:
    return DocumentRegistration(
        user_id=user_id,
        filename=filename,
        mime_type="application/pdf",
        storage_path=f".anima/documents/{user_id}/{filename}",
        sha256=sha256,
        size_bytes=1024,
        metadata_json=metadata_json,
    )


def test_document_tables_registered(runtime_engine) -> None:
    RuntimeBase.metadata.create_all(runtime_engine)

    names = set(inspect(runtime_engine).get_table_names())

    assert RuntimeDocument.__tablename__ in names
    assert RuntimeDocumentChunk.__tablename__ in names


def test_document_hash_is_unique_per_user_constraint_registered() -> None:
    assert _constraint_columns(
        RuntimeDocument,
        "uq_runtime_documents_user_sha256",
    ) == ("user_id", "sha256")


def test_document_id_user_unique_target_constraint_registered() -> None:
    assert _constraint_columns(
        RuntimeDocument,
        "uq_runtime_documents_id_user",
    ) == ("id", "user_id")


def test_document_chunk_index_is_unique_per_document_constraint_registered() -> None:
    assert _constraint_columns(
        RuntimeDocumentChunk,
        "uq_runtime_document_chunks_document_index",
    ) == ("document_id", "chunk_index")


def test_document_chunk_ownership_foreign_key_constraint_registered() -> None:
    constraint = _foreign_key_constraint(
        RuntimeDocumentChunk,
        "fk_runtime_document_chunks_document_user",
    )

    assert tuple(element.parent.name for element in constraint.elements) == (
        "document_id",
        "user_id",
    )
    assert tuple(
        f"{element.column.table.name}.{element.column.name}"
        for element in constraint.elements
    ) == (
        "runtime_documents.id",
        "runtime_documents.user_id",
    )
    assert constraint.ondelete == "CASCADE"


def test_document_status_has_python_and_server_defaults() -> None:
    status = RuntimeDocument.__table__.c.status

    assert status.default is not None
    assert status.default.arg == "registered"
    assert str(status.server_default.arg) == "'registered'"


def test_register_document_creates_document(runtime_db) -> None:
    document = register_document(
        runtime_db,
        _registration(metadata_json={"source": "upload"}),
    )

    assert document.id is not None
    assert document.user_id == 1
    assert document.filename == "notes.pdf"
    assert document.mime_type == "application/pdf"
    assert document.storage_path == ".anima/documents/1/notes.pdf"
    assert document.sha256 == "a" * 64
    assert document.size_bytes == 1024
    assert document.status == "registered"
    assert document.metadata_json == {"source": "upload"}


def test_register_document_is_idempotent_by_user_and_sha(runtime_db) -> None:
    original = register_document(
        runtime_db,
        _registration(metadata_json={"source": "first"}),
    )

    duplicate = register_document(
        runtime_db,
        _registration(filename="renamed.pdf", metadata_json={"source": "second"}),
    )

    assert duplicate.id == original.id
    assert duplicate.filename == "notes.pdf"
    assert duplicate.metadata_json == {"source": "first"}
    assert runtime_db.scalar(select(func.count(RuntimeDocument.id))) == 1


def test_get_document_for_user_filters_user(runtime_db) -> None:
    document = register_document(runtime_db, _registration(user_id=1))

    assert get_document_for_user(runtime_db, user_id=1, document_id=document.id) == document
    assert get_document_for_user(runtime_db, user_id=2, document_id=document.id) is None


def test_replace_document_chunks_replaces_existing_chunks_and_hashes_content(
    runtime_db,
) -> None:
    document = register_document(runtime_db, _registration())
    old_chunks = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(chunk_index=0, content_text="old text"),
        ],
    )

    chunks = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=2,
                content_text="third chunk",
                page_start=5,
                page_end=6,
                section_title="Later",
                token_count=12,
                metadata_json={"kind": "body"},
            ),
            ExtractedDocumentChunk(chunk_index=1, content_text="second chunk"),
        ],
    )

    assert [chunk.chunk_index for chunk in chunks] == [1, 2]
    assert old_chunks[0].content_text == "old text"
    assert [chunk.content_text for chunk in chunks] == ["second chunk", "third chunk"]
    assert runtime_db.scalar(select(func.count(RuntimeDocumentChunk.id))) == 2
    assert chunks[0].content_hash == hashlib.sha256(b"second chunk").hexdigest()
    assert chunks[1].content_hash == hashlib.sha256(b"third chunk").hexdigest()
    assert chunks[1].page_start == 5
    assert chunks[1].page_end == 6
    assert chunks[1].section_title == "Later"
    assert chunks[1].token_count == 12
    assert chunks[1].metadata_json == {"kind": "body"}


def test_replace_document_chunks_uses_document_user_id(runtime_db) -> None:
    document = register_document(runtime_db, _registration(user_id=77))

    chunks = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(chunk_index=0, content_text="owned chunk"),
        ],
    )

    assert chunks[0].document_id == document.id
    assert chunks[0].user_id == 77


def test_list_document_chunks_orders_by_chunk_index(runtime_db) -> None:
    document = register_document(runtime_db, _registration())
    replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(chunk_index=5, content_text="last"),
            ExtractedDocumentChunk(chunk_index=0, content_text="first"),
            ExtractedDocumentChunk(chunk_index=2, content_text="middle"),
        ],
    )

    chunks = list_document_chunks(runtime_db, document_id=document.id)

    assert [chunk.chunk_index for chunk in chunks] == [0, 2, 5]


def test_set_document_status_can_mark_indexed(runtime_db) -> None:
    document = register_document(runtime_db, _registration())

    indexed = set_document_status(
        runtime_db,
        document_id=document.id,
        status="indexed",
        indexed=True,
    )

    assert indexed is not None
    assert indexed.status == "indexed"
    assert indexed.indexed_at is not None

    indexed_at = indexed.indexed_at
    updated = set_document_status(
        runtime_db,
        document_id=document.id,
        status="stale",
    )

    assert updated is not None
    assert updated.status == "stale"
    assert updated.indexed_at == indexed_at
