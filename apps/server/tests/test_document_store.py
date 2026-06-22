from __future__ import annotations

from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
from sqlalchemy import ForeignKeyConstraint, inspect

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
