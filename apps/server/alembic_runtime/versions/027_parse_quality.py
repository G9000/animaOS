"""Add parse_quality to runtime documents and chunks.

Tracks which extraction pipeline produced a document/chunk's text
(preview | docling | legacy) so lower-quality parses can be identified
for re-parse. Existing rows default to "legacy" since they predate this
column and were not produced by the current Docling-first pipeline.

Revision ID: 027_parse_quality
Revises: 026_reembed_completions
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "027_parse_quality"
down_revision = "026_reembed_completions"
branch_labels = None
depends_on = None


def _existing_columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    # Defensive like 021/022: a badly-stamped DB may be missing these
    # tables entirely (they are then created from the models with this
    # column already present), and re-runs must not re-add the column.
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("runtime_documents"):
        if "parse_quality" not in _existing_columns(inspector, "runtime_documents"):
            op.add_column(
                "runtime_documents",
                sa.Column(
                    "parse_quality", sa.String(16), nullable=False, server_default="legacy"
                ),
            )

    if inspector.has_table("runtime_document_chunks"):
        if "parse_quality" not in _existing_columns(inspector, "runtime_document_chunks"):
            op.add_column(
                "runtime_document_chunks",
                sa.Column(
                    "parse_quality", sa.String(16), nullable=False, server_default="legacy"
                ),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("runtime_document_chunks"):
        if "parse_quality" in _existing_columns(inspector, "runtime_document_chunks"):
            op.drop_column("runtime_document_chunks", "parse_quality")

    if inspector.has_table("runtime_documents"):
        if "parse_quality" in _existing_columns(inspector, "runtime_documents"):
            op.drop_column("runtime_documents", "parse_quality")
