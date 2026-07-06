"""add source knowledge ingestion

Revision ID: 1c3df376a170
Revises: 021_repair_profile_update_candidates
Create Date: 2026-07-07 00:23:28.485692
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "1c3df376a170"
down_revision = "021_repair_profile_update_candidates"
branch_labels = None
depends_on = None

JSON = postgresql.JSON(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.DateTime(timezone=True),
    "sqlite",
)


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "runtime_sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("source_uri", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("media_type", sa.String(length=128), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_runtime_sources_id_user"),
        sa.UniqueConstraint(
            "user_id",
            "source_uri",
            "content_hash",
            name="uq_runtime_sources_user_uri_hash",
        ),
    )
    op.create_index("ix_runtime_sources_user_id", "runtime_sources", ["user_id"])
    op.create_index(
        "ix_runtime_sources_content_hash",
        "runtime_sources",
        ["content_hash"],
    )
    op.create_index(
        "ix_runtime_sources_user_kind_status",
        "runtime_sources",
        ["user_id", "kind", "status"],
    )
    op.create_index(
        "ix_runtime_sources_user_created",
        "runtime_sources",
        ["user_id", "created_at"],
    )

    op.create_table(
        "runtime_source_artifacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("artifact_kind", sa.String(length=48), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
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
            ["source_id", "user_id"],
            ["runtime_sources.id", "runtime_sources.user_id"],
            name="fk_runtime_source_artifacts_source_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_source_artifacts_id_user",
        ),
        sa.UniqueConstraint(
            "source_id",
            "artifact_kind",
            "content_hash",
            name="uq_runtime_source_artifacts_source_kind_hash",
        ),
    )
    op.create_index(
        "ix_runtime_source_artifacts_user_id",
        "runtime_source_artifacts",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_source_artifacts_source_id",
        "runtime_source_artifacts",
        ["source_id"],
    )
    op.create_index(
        "ix_runtime_source_artifacts_content_hash",
        "runtime_source_artifacts",
        ["content_hash"],
    )
    op.create_index(
        "ix_runtime_source_artifacts_user_source",
        "runtime_source_artifacts",
        ["user_id", "source_id"],
    )
    op.create_index(
        "ix_runtime_source_artifacts_user_kind",
        "runtime_source_artifacts",
        ["user_id", "artifact_kind"],
    )

    op.create_table(
        "runtime_source_spans",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("artifact_id", sa.BigInteger(), nullable=False),
        sa.Column("span_kind", sa.String(length=48), nullable=False),
        sa.Column("locator_json", JSON, nullable=False),
        sa.Column("locator_hash", sa.String(length=64), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
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
            ["source_id", "user_id"],
            ["runtime_sources.id", "runtime_sources.user_id"],
            name="fk_runtime_source_spans_source_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "user_id"],
            ["runtime_source_artifacts.id", "runtime_source_artifacts.user_id"],
            name="fk_runtime_source_spans_artifact_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_runtime_source_spans_id_user"),
        sa.UniqueConstraint(
            "artifact_id",
            "locator_hash",
            "content_hash",
            name="uq_runtime_source_spans_artifact_locator_hash",
        ),
    )
    op.create_index(
        "ix_runtime_source_spans_user_id",
        "runtime_source_spans",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_source_spans_source_id",
        "runtime_source_spans",
        ["source_id"],
    )
    op.create_index(
        "ix_runtime_source_spans_artifact_id",
        "runtime_source_spans",
        ["artifact_id"],
    )
    op.create_index(
        "ix_runtime_source_spans_locator_hash",
        "runtime_source_spans",
        ["locator_hash"],
    )
    op.create_index(
        "ix_runtime_source_spans_content_hash",
        "runtime_source_spans",
        ["content_hash"],
    )
    op.create_index(
        "ix_runtime_source_spans_user_source",
        "runtime_source_spans",
        ["user_id", "source_id"],
    )
    op.create_index(
        "ix_runtime_source_spans_user_artifact",
        "runtime_source_spans",
        ["user_id", "artifact_id"],
    )
    op.create_index(
        "ix_runtime_source_spans_user_kind",
        "runtime_source_spans",
        ["user_id", "span_kind"],
    )

    op.create_table(
        "runtime_knowledge_concepts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_type", sa.String(length=48), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("frontmatter_json", JSON, nullable=False),
        sa.Column("metadata_json", JSON, nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
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
        sa.Column("compiled_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_knowledge_concepts_id_user",
        ),
        sa.UniqueConstraint(
            "user_id",
            "slug",
            name="uq_runtime_knowledge_concepts_user_slug",
        ),
    )
    op.create_index(
        "ix_runtime_knowledge_concepts_user_id",
        "runtime_knowledge_concepts",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_concepts_content_hash",
        "runtime_knowledge_concepts",
        ["content_hash"],
    )
    op.create_index(
        "ix_runtime_knowledge_concepts_user_type_status",
        "runtime_knowledge_concepts",
        ["user_id", "concept_type", "status"],
    )
    op.create_index(
        "ix_runtime_knowledge_concepts_user_title",
        "runtime_knowledge_concepts",
        ["user_id", "title"],
    )

    op.create_table(
        "runtime_knowledge_concept_sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("span_id", sa.BigInteger(), nullable=False),
        sa.Column("citation_label", sa.String(length=64), nullable=True),
        sa.Column("quote_text", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSON, nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["concept_id", "user_id"],
            ["runtime_knowledge_concepts.id", "runtime_knowledge_concepts.user_id"],
            name="fk_runtime_knowledge_concept_sources_concept_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "user_id"],
            ["runtime_sources.id", "runtime_sources.user_id"],
            name="fk_runtime_knowledge_concept_sources_source_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["span_id", "user_id"],
            ["runtime_source_spans.id", "runtime_source_spans.user_id"],
            name="fk_runtime_knowledge_concept_sources_span_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "concept_id",
            "span_id",
            name="uq_runtime_knowledge_concept_sources_concept_span",
        ),
    )
    op.create_index(
        "ix_runtime_knowledge_concept_sources_user_id",
        "runtime_knowledge_concept_sources",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_concept_sources_concept_id",
        "runtime_knowledge_concept_sources",
        ["concept_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_concept_sources_source_id",
        "runtime_knowledge_concept_sources",
        ["source_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_concept_sources_span_id",
        "runtime_knowledge_concept_sources",
        ["span_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_concept_sources_user_concept",
        "runtime_knowledge_concept_sources",
        ["user_id", "concept_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_concept_sources_user_source",
        "runtime_knowledge_concept_sources",
        ["user_id", "source_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_concept_sources_user_span",
        "runtime_knowledge_concept_sources",
        ["user_id", "span_id"],
    )

    op.create_table(
        "runtime_knowledge_links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_concept_id", sa.BigInteger(), nullable=False),
        sa.Column("target_concept_id", sa.BigInteger(), nullable=False),
        sa.Column("link_type", sa.String(length=48), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
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
            ["source_concept_id", "user_id"],
            ["runtime_knowledge_concepts.id", "runtime_knowledge_concepts.user_id"],
            name="fk_runtime_knowledge_links_source_concept_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_concept_id", "user_id"],
            ["runtime_knowledge_concepts.id", "runtime_knowledge_concepts.user_id"],
            name="fk_runtime_knowledge_links_target_concept_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source_concept_id",
            "target_concept_id",
            "link_type",
            name="uq_runtime_knowledge_links_user_source_target_type",
        ),
    )
    op.create_index(
        "ix_runtime_knowledge_links_user_id",
        "runtime_knowledge_links",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_links_source_concept_id",
        "runtime_knowledge_links",
        ["source_concept_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_links_target_concept_id",
        "runtime_knowledge_links",
        ["target_concept_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_links_user_source",
        "runtime_knowledge_links",
        ["user_id", "source_concept_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_links_user_target",
        "runtime_knowledge_links",
        ["user_id", "target_concept_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_links_user_type",
        "runtime_knowledge_links",
        ["user_id", "link_type"],
    )

    op.create_table(
        "runtime_knowledge_bundle_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("run_type", sa.String(length=48), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("input_json", JSON, nullable=True),
        sa.Column("result_json", JSON, nullable=True),
        sa.Column("error_json", JSON, nullable=True),
        sa.Column("started_at", TIMESTAMPTZ, nullable=True),
        sa.Column("completed_at", TIMESTAMPTZ, nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_knowledge_bundle_runs_user_id",
        "runtime_knowledge_bundle_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_bundle_runs_source_id",
        "runtime_knowledge_bundle_runs",
        ["source_id"],
    )
    op.create_index(
        "ix_runtime_knowledge_bundle_runs_user_type_status",
        "runtime_knowledge_bundle_runs",
        ["user_id", "run_type", "status"],
    )
    op.create_index(
        "ix_runtime_knowledge_bundle_runs_user_source",
        "runtime_knowledge_bundle_runs",
        ["user_id", "source_id"],
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        "ix_runtime_knowledge_bundle_runs_user_source",
        table_name="runtime_knowledge_bundle_runs",
    )
    op.drop_index(
        "ix_runtime_knowledge_bundle_runs_user_type_status",
        table_name="runtime_knowledge_bundle_runs",
    )
    op.drop_index(
        "ix_runtime_knowledge_bundle_runs_source_id",
        table_name="runtime_knowledge_bundle_runs",
    )
    op.drop_index(
        "ix_runtime_knowledge_bundle_runs_user_id",
        table_name="runtime_knowledge_bundle_runs",
    )
    op.drop_table("runtime_knowledge_bundle_runs")

    op.drop_index("ix_runtime_knowledge_links_user_type", table_name="runtime_knowledge_links")
    op.drop_index("ix_runtime_knowledge_links_user_target", table_name="runtime_knowledge_links")
    op.drop_index("ix_runtime_knowledge_links_user_source", table_name="runtime_knowledge_links")
    op.drop_index(
        "ix_runtime_knowledge_links_target_concept_id",
        table_name="runtime_knowledge_links",
    )
    op.drop_index(
        "ix_runtime_knowledge_links_source_concept_id",
        table_name="runtime_knowledge_links",
    )
    op.drop_index("ix_runtime_knowledge_links_user_id", table_name="runtime_knowledge_links")
    op.drop_table("runtime_knowledge_links")

    op.drop_index(
        "ix_runtime_knowledge_concept_sources_user_span",
        table_name="runtime_knowledge_concept_sources",
    )
    op.drop_index(
        "ix_runtime_knowledge_concept_sources_user_source",
        table_name="runtime_knowledge_concept_sources",
    )
    op.drop_index(
        "ix_runtime_knowledge_concept_sources_user_concept",
        table_name="runtime_knowledge_concept_sources",
    )
    op.drop_index(
        "ix_runtime_knowledge_concept_sources_span_id",
        table_name="runtime_knowledge_concept_sources",
    )
    op.drop_index(
        "ix_runtime_knowledge_concept_sources_source_id",
        table_name="runtime_knowledge_concept_sources",
    )
    op.drop_index(
        "ix_runtime_knowledge_concept_sources_concept_id",
        table_name="runtime_knowledge_concept_sources",
    )
    op.drop_index(
        "ix_runtime_knowledge_concept_sources_user_id",
        table_name="runtime_knowledge_concept_sources",
    )
    op.drop_table("runtime_knowledge_concept_sources")

    op.drop_index(
        "ix_runtime_knowledge_concepts_user_title",
        table_name="runtime_knowledge_concepts",
    )
    op.drop_index(
        "ix_runtime_knowledge_concepts_user_type_status",
        table_name="runtime_knowledge_concepts",
    )
    op.drop_index(
        "ix_runtime_knowledge_concepts_content_hash",
        table_name="runtime_knowledge_concepts",
    )
    op.drop_index(
        "ix_runtime_knowledge_concepts_user_id",
        table_name="runtime_knowledge_concepts",
    )
    op.drop_table("runtime_knowledge_concepts")

    op.drop_index("ix_runtime_source_spans_user_kind", table_name="runtime_source_spans")
    op.drop_index("ix_runtime_source_spans_user_artifact", table_name="runtime_source_spans")
    op.drop_index("ix_runtime_source_spans_user_source", table_name="runtime_source_spans")
    op.drop_index("ix_runtime_source_spans_content_hash", table_name="runtime_source_spans")
    op.drop_index("ix_runtime_source_spans_locator_hash", table_name="runtime_source_spans")
    op.drop_index("ix_runtime_source_spans_artifact_id", table_name="runtime_source_spans")
    op.drop_index("ix_runtime_source_spans_source_id", table_name="runtime_source_spans")
    op.drop_index("ix_runtime_source_spans_user_id", table_name="runtime_source_spans")
    op.drop_table("runtime_source_spans")

    op.drop_index(
        "ix_runtime_source_artifacts_user_kind",
        table_name="runtime_source_artifacts",
    )
    op.drop_index(
        "ix_runtime_source_artifacts_user_source",
        table_name="runtime_source_artifacts",
    )
    op.drop_index(
        "ix_runtime_source_artifacts_content_hash",
        table_name="runtime_source_artifacts",
    )
    op.drop_index(
        "ix_runtime_source_artifacts_source_id",
        table_name="runtime_source_artifacts",
    )
    op.drop_index(
        "ix_runtime_source_artifacts_user_id",
        table_name="runtime_source_artifacts",
    )
    op.drop_table("runtime_source_artifacts")

    op.drop_index("ix_runtime_sources_user_created", table_name="runtime_sources")
    op.drop_index("ix_runtime_sources_user_kind_status", table_name="runtime_sources")
    op.drop_index("ix_runtime_sources_content_hash", table_name="runtime_sources")
    op.drop_index("ix_runtime_sources_user_id", table_name="runtime_sources")
    op.drop_table("runtime_sources")
