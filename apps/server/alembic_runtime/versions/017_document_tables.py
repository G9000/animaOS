"""Add runtime document tables.

Revision ID: 017_document_tables
Revises: 016_workflow_checkpoints_documents
Create Date: 2026-06-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "017_document_tables"
down_revision = "016_workflow_checkpoints_documents"
branch_labels = None
depends_on = None

JSON = postgresql.JSON(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.DateTime(timezone=True),
    "sqlite",
)


def upgrade() -> None:
    op.create_table(
        "runtime_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'registered'"),
            nullable=False,
        ),
        sa.Column("metadata_json", JSON, nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("indexed_at", TIMESTAMPTZ, nullable=True),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["runtime_threads.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["runtime_workflow_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_documents_id_user",
        ),
        sa.UniqueConstraint(
            "user_id",
            "sha256",
            name="uq_runtime_documents_user_sha256",
        ),
    )
    op.create_index("ix_runtime_documents_user_id", "runtime_documents", ["user_id"])
    op.create_index("ix_runtime_documents_thread_id", "runtime_documents", ["thread_id"])
    op.create_index(
        "ix_runtime_documents_workflow_run_id",
        "runtime_documents",
        ["workflow_run_id"],
    )
    op.create_index("ix_runtime_documents_sha256", "runtime_documents", ["sha256"])
    op.create_index(
        "ix_runtime_documents_user_status",
        "runtime_documents",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_runtime_documents_user_created",
        "runtime_documents",
        ["user_id", "created_at"],
    )

    op.create_table(
        "runtime_document_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("section_title", sa.String(length=255), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("metadata_json", JSON, nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["runtime_documents.id", "runtime_documents.user_id"],
            name="fk_runtime_document_chunks_document_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_runtime_document_chunks_document_index",
        ),
    )
    op.create_index(
        "ix_runtime_document_chunks_document_id",
        "runtime_document_chunks",
        ["document_id"],
    )
    op.create_index(
        "ix_runtime_document_chunks_user_id",
        "runtime_document_chunks",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_document_chunks_content_hash",
        "runtime_document_chunks",
        ["content_hash"],
    )
    op.create_index(
        "ix_runtime_document_chunks_document_index",
        "runtime_document_chunks",
        ["document_id", "chunk_index"],
    )
    op.create_index(
        "ix_runtime_document_chunks_user_document",
        "runtime_document_chunks",
        ["user_id", "document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_document_chunks_user_document",
        table_name="runtime_document_chunks",
    )
    op.drop_index(
        "ix_runtime_document_chunks_document_index",
        table_name="runtime_document_chunks",
    )
    op.drop_index(
        "ix_runtime_document_chunks_content_hash",
        table_name="runtime_document_chunks",
    )
    op.drop_index(
        "ix_runtime_document_chunks_user_id",
        table_name="runtime_document_chunks",
    )
    op.drop_index(
        "ix_runtime_document_chunks_document_id",
        table_name="runtime_document_chunks",
    )
    op.drop_table("runtime_document_chunks")

    op.drop_index(
        "ix_runtime_documents_user_created",
        table_name="runtime_documents",
    )
    op.drop_index(
        "ix_runtime_documents_user_status",
        table_name="runtime_documents",
    )
    op.drop_index("ix_runtime_documents_sha256", table_name="runtime_documents")
    op.drop_index(
        "ix_runtime_documents_workflow_run_id",
        table_name="runtime_documents",
    )
    op.drop_index("ix_runtime_documents_thread_id", table_name="runtime_documents")
    op.drop_index("ix_runtime_documents_user_id", table_name="runtime_documents")
    op.drop_table("runtime_documents")
