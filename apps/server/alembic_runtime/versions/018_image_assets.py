"""Add runtime image asset tables.

Revision ID: 018_image_assets
Revises: 017_document_tables
Create Date: 2026-06-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "018_image_assets"
down_revision = "017_document_tables"
branch_labels = None
depends_on = None

JSON = postgresql.JSON(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.DateTime(timezone=True),
    "sqlite",
)


def upgrade() -> None:
    with op.batch_alter_table("runtime_messages") as batch_op:
        batch_op.create_unique_constraint(
            "uq_runtime_messages_id_user",
            ["id", "user_id"],
        )

    op.create_table(
        "runtime_image_assets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'registered'"),
            nullable=False,
        ),
        sa.Column(
            "retention_state",
            sa.String(length=24),
            server_default=sa.text("'transient'"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_image_assets_id_user",
        ),
        sa.UniqueConstraint(
            "user_id",
            "sha256",
            name="uq_runtime_image_assets_user_sha256",
        ),
    )
    op.create_index("ix_runtime_image_assets_user_id", "runtime_image_assets", ["user_id"])
    op.create_index("ix_runtime_image_assets_sha256", "runtime_image_assets", ["sha256"])
    op.create_index(
        "ix_runtime_image_assets_user_status",
        "runtime_image_assets",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_runtime_image_assets_user_created",
        "runtime_image_assets",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_runtime_image_assets_user_retention",
        "runtime_image_assets",
        ["user_id", "retention_state"],
    )

    op.create_table(
        "runtime_image_annotations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("image_asset_id", sa.BigInteger(), nullable=False),
        sa.Column("annotation_kind", sa.String(length=32), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_model", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'active'"),
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
        sa.ForeignKeyConstraint(
            ["image_asset_id", "user_id"],
            ["runtime_image_assets.id", "runtime_image_assets.user_id"],
            name="fk_runtime_image_annotations_asset_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "image_asset_id",
            "annotation_kind",
            "content_hash",
            name="uq_runtime_image_annotations_asset_kind_hash",
        ),
    )
    op.create_index(
        "ix_runtime_image_annotations_user_id",
        "runtime_image_annotations",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_image_annotations_image_asset_id",
        "runtime_image_annotations",
        ["image_asset_id"],
    )
    op.create_index(
        "ix_runtime_image_annotations_content_hash",
        "runtime_image_annotations",
        ["content_hash"],
    )
    op.create_index(
        "ix_runtime_image_annotations_user_kind",
        "runtime_image_annotations",
        ["user_id", "annotation_kind"],
    )
    op.create_index(
        "ix_runtime_image_annotations_user_asset",
        "runtime_image_annotations",
        ["user_id", "image_asset_id"],
    )
    op.create_index(
        "ix_runtime_image_annotations_user_status",
        "runtime_image_annotations",
        ["user_id", "status"],
    )

    op.create_table(
        "runtime_image_message_links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("image_asset_id", sa.BigInteger(), nullable=False),
        sa.Column("attachment_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["runtime_messages.id", "runtime_messages.user_id"],
            name="fk_runtime_image_message_links_message_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["image_asset_id", "user_id"],
            ["runtime_image_assets.id", "runtime_image_assets.user_id"],
            name="fk_runtime_image_message_links_asset_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "attachment_id",
            name="uq_runtime_image_message_links_message_attachment",
        ),
    )
    op.create_index(
        "ix_runtime_image_message_links_user_id",
        "runtime_image_message_links",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_image_message_links_message_id",
        "runtime_image_message_links",
        ["message_id"],
    )
    op.create_index(
        "ix_runtime_image_message_links_image_asset_id",
        "runtime_image_message_links",
        ["image_asset_id"],
    )
    op.create_index(
        "ix_runtime_image_message_links_user_message",
        "runtime_image_message_links",
        ["user_id", "message_id"],
    )
    op.create_index(
        "ix_runtime_image_message_links_user_asset",
        "runtime_image_message_links",
        ["user_id", "image_asset_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_image_message_links_user_asset",
        table_name="runtime_image_message_links",
    )
    op.drop_index(
        "ix_runtime_image_message_links_user_message",
        table_name="runtime_image_message_links",
    )
    op.drop_index(
        "ix_runtime_image_message_links_image_asset_id",
        table_name="runtime_image_message_links",
    )
    op.drop_index(
        "ix_runtime_image_message_links_message_id",
        table_name="runtime_image_message_links",
    )
    op.drop_index(
        "ix_runtime_image_message_links_user_id",
        table_name="runtime_image_message_links",
    )
    op.drop_table("runtime_image_message_links")

    op.drop_index(
        "ix_runtime_image_annotations_user_status",
        table_name="runtime_image_annotations",
    )
    op.drop_index(
        "ix_runtime_image_annotations_user_asset",
        table_name="runtime_image_annotations",
    )
    op.drop_index(
        "ix_runtime_image_annotations_user_kind",
        table_name="runtime_image_annotations",
    )
    op.drop_index(
        "ix_runtime_image_annotations_content_hash",
        table_name="runtime_image_annotations",
    )
    op.drop_index(
        "ix_runtime_image_annotations_image_asset_id",
        table_name="runtime_image_annotations",
    )
    op.drop_index(
        "ix_runtime_image_annotations_user_id",
        table_name="runtime_image_annotations",
    )
    op.drop_table("runtime_image_annotations")

    op.drop_index(
        "ix_runtime_image_assets_user_retention",
        table_name="runtime_image_assets",
    )
    op.drop_index(
        "ix_runtime_image_assets_user_created",
        table_name="runtime_image_assets",
    )
    op.drop_index(
        "ix_runtime_image_assets_user_status",
        table_name="runtime_image_assets",
    )
    op.drop_index("ix_runtime_image_assets_sha256", table_name="runtime_image_assets")
    op.drop_index("ix_runtime_image_assets_user_id", table_name="runtime_image_assets")
    op.drop_table("runtime_image_assets")

    with op.batch_alter_table("runtime_messages") as batch_op:
        batch_op.drop_constraint("uq_runtime_messages_id_user", type_="unique")
