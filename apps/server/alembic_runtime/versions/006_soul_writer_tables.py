"""Soul Writer pipeline tables: memory_candidates, promotion_journal, memory_access_log.

Revision ID: 006_soul_writer
Revises: 005
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP

revision = "006_soul_writer"
down_revision = "005"
branch_labels = None
depends_on = None

TIMESTAMPTZ = TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("importance", sa.Integer, nullable=False, server_default="3"),
        sa.Column("importance_source", sa.String(32), nullable=False, server_default="'llm'"),
        sa.Column("supersedes_item_id", sa.Integer, nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_message_ids", ARRAY(sa.Integer), nullable=True),
        sa.Column("extraction_model", sa.String(128), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="'extracted'"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", TIMESTAMPTZ, nullable=True),
    )
    op.create_index(
        "ix_memory_candidates_user_status",
        "memory_candidates",
        ["user_id", "status"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_candidates_active_hash "
        "ON memory_candidates(content_hash) "
        "WHERE status NOT IN ('rejected', 'superseded', 'failed')"
    )

    op.create_table(
        "promotion_journal",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("candidate_id", sa.BigInteger, nullable=True),
        sa.Column("pending_op_id", sa.BigInteger, nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("target_table", sa.String(32), nullable=True),
        sa.Column("target_record_id", sa.String(64), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("extraction_model", sa.String(128), nullable=True),
        sa.Column("journal_status", sa.String(16), nullable=False, server_default="'tentative'"),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_promotion_journal_user", "promotion_journal", ["user_id"])
    op.create_index(
        "ix_promotion_journal_hash",
        "promotion_journal",
        ["content_hash", "decision"],
    )
    op.create_index(
        "ix_promotion_journal_status",
        "promotion_journal",
        ["journal_status"],
    )

    op.create_table(
        "memory_access_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("memory_item_id", sa.Integer, nullable=False),
        sa.Column("accessed_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("synced", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_memory_access_log_user_item",
        "memory_access_log",
        ["user_id", "memory_item_id"],
    )
    op.execute(
        "CREATE INDEX ix_memory_access_log_unsynced "
        "ON memory_access_log(user_id) WHERE synced = FALSE"
    )


def downgrade() -> None:
    op.drop_table("memory_access_log")
    op.drop_table("promotion_journal")
    op.drop_table("memory_candidates")
