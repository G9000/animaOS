"""PostgreSQL-native runtime models.

These mirror the soul models in ``agent_runtime.py`` but target PostgreSQL
via :class:`RuntimeBase` instead of the per-user SQLCipher :class:`Base`.

Key differences from the soul models:
- ``BigInteger`` primary keys
- ``TIMESTAMP(timezone=True)`` instead of ``DateTime(timezone=True)``
- ``postgresql.JSON`` instead of generic ``JSON``
- Table names prefixed with ``runtime_``
- ``user_id`` is a plain indexed column (no FK to soul tables)
- ForeignKeys within runtime tables ARE enforced
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP

# TIMESTAMPTZ shorthand — ``TIMESTAMP(timezone=True)`` is the portable
# spelling that works across all SQLAlchemy versions & PG backends.
TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship
from sqlalchemy.orm.attributes import set_committed_value

from anima_server.db.runtime_base import RuntimeBase


class RuntimeThread(RuntimeBase):
    __tablename__ = "runtime_threads"
    __table_args__ = (Index("ix_runtime_threads_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    # Archival retry state: closed-but-unarchived threads are retried by
    # the inactivity sweep with exponential backoff instead of once per
    # minute forever; archive_failed marks a terminal give-up.
    archive_retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    archive_next_retry_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    archive_failed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    next_message_sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    messages: Mapped[list[RuntimeMessage]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="RuntimeMessage.sequence_id",
    )
    runs: Mapped[list[RuntimeRun]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="RuntimeRun.started_at",
    )


class RuntimeRun(RuntimeBase):
    __tablename__ = "runtime_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_approval_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )

    thread: Mapped[RuntimeThread] = relationship(back_populates="runs")
    steps: Mapped[list[RuntimeStep]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RuntimeStep.step_index",
    )


class RuntimeWorkflowRun(RuntimeBase):
    __tablename__ = "runtime_workflow_runs"
    __table_args__ = (
        Index("ix_runtime_workflow_runs_user_status", "user_id", "status"),
        Index("ix_runtime_workflow_runs_user_type", "user_id", "workflow_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    thread_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="created", server_default=text("'created'")
    )
    current_state: Mapped[str] = mapped_column(
        String(64), nullable=False, default="created", server_default=text("'created'")
    )
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)


class RuntimeWorkflowCheckpoint(RuntimeBase):
    __tablename__ = "runtime_workflow_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "checkpoint_index",
            name="uq_runtime_workflow_checkpoint_index",
        ),
        UniqueConstraint(
            "workflow_run_id",
            "idempotency_key",
            name="uq_runtime_workflow_checkpoint_idempotency",
        ),
        Index(
            "ix_runtime_workflow_checkpoints_run_created",
            "workflow_run_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checkpoint_index: Mapped[int] = mapped_column(Integer, nullable=False)
    state_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifact_refs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )


class RuntimeDocument(RuntimeBase):
    __tablename__ = "runtime_documents"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "sha256",
            name="uq_runtime_documents_user_sha256",
        ),
        UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_documents_id_user",
        ),
        Index("ix_runtime_documents_user_status", "user_id", "status"),
        Index("ix_runtime_documents_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    thread_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runtime_workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="registered",
        server_default=text("'registered'"),
    )
    parse_quality: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="legacy", default="legacy"
    )
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    indexed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)


class RuntimeDocumentChunk(RuntimeBase):
    __tablename__ = "runtime_document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "user_id"],
            ["runtime_documents.id", "runtime_documents.user_id"],
            name="fk_runtime_document_chunks_document_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_runtime_document_chunks_document_index",
        ),
        Index(
            "ix_runtime_document_chunks_document_index",
            "document_id",
            "chunk_index",
        ),
        Index(
            "ix_runtime_document_chunks_user_document",
            "user_id",
            "document_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_char_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_quality: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="legacy", default="legacy"
    )
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )


class RuntimeImageAsset(RuntimeBase):
    __tablename__ = "runtime_image_assets"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "sha256",
            name="uq_runtime_image_assets_user_sha256",
        ),
        UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_image_assets_id_user",
        ),
        Index("ix_runtime_image_assets_user_status", "user_id", "status"),
        Index("ix_runtime_image_assets_user_created", "user_id", "created_at"),
        Index("ix_runtime_image_assets_user_retention", "user_id", "retention_state"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="registered",
        server_default=text("'registered'"),
    )
    retention_state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="transient",
        server_default=text("'transient'"),
    )
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    indexed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)

    message_links: Mapped[list[RuntimeImageMessageLink]] = relationship(
        back_populates="image_asset",
        cascade="all, delete-orphan",
        overlaps="image_links,message",
    )
    annotations: Mapped[list[RuntimeImageAnnotation]] = relationship(
        back_populates="image_asset",
        cascade="all, delete-orphan",
        order_by="RuntimeImageAnnotation.created_at",
    )


class RuntimeImageAnnotation(RuntimeBase):
    __tablename__ = "runtime_image_annotations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["image_asset_id", "user_id"],
            ["runtime_image_assets.id", "runtime_image_assets.user_id"],
            name="fk_runtime_image_annotations_asset_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "image_asset_id",
            "annotation_kind",
            "content_hash",
            name="uq_runtime_image_annotations_asset_kind_hash",
        ),
        Index(
            "ix_runtime_image_annotations_user_kind",
            "user_id",
            "annotation_kind",
        ),
        Index(
            "ix_runtime_image_annotations_user_asset",
            "user_id",
            "image_asset_id",
        ),
        Index(
            "ix_runtime_image_annotations_user_status",
            "user_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    image_asset_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    annotation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    image_asset: Mapped[RuntimeImageAsset] = relationship(back_populates="annotations")

    @staticmethod
    def compute_content_hash(plaintext: str) -> str:
        """SHA-256 hex digest for annotation embedding staleness checks."""
        return hashlib.sha256(plaintext.encode()).hexdigest()


class RuntimeSource(RuntimeBase):
    __tablename__ = "runtime_sources"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_sources_id_user",
        ),
        UniqueConstraint(
            "user_id",
            "kind",
            "source_uri",
            "content_hash",
            name="uq_runtime_sources_user_kind_uri_hash",
        ),
        Index("ix_runtime_sources_user_kind_status", "user_id", "kind", "status"),
        Index("ix_runtime_sources_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="registered",
        server_default=text("'registered'"),
    )
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    indexed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)

    artifacts: Mapped[list[RuntimeSourceArtifact]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="RuntimeSourceArtifact.created_at",
    )
    spans: Mapped[list[RuntimeSourceSpan]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="RuntimeSourceSpan.created_at",
        overlaps="artifact,spans",
    )


class RuntimeSourceArtifact(RuntimeBase):
    __tablename__ = "runtime_source_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "user_id"],
            ["runtime_sources.id", "runtime_sources.user_id"],
            name="fk_runtime_source_artifacts_source_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_source_artifacts_id_user",
        ),
        UniqueConstraint(
            "source_id",
            "artifact_kind",
            "content_hash",
            name="uq_runtime_source_artifacts_source_kind_hash",
        ),
        Index(
            "ix_runtime_source_artifacts_user_source",
            "user_id",
            "source_id",
        ),
        Index(
            "ix_runtime_source_artifacts_user_kind",
            "user_id",
            "artifact_kind",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    artifact_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    source: Mapped[RuntimeSource] = relationship(back_populates="artifacts")
    spans: Mapped[list[RuntimeSourceSpan]] = relationship(
        back_populates="artifact",
        cascade="all, delete-orphan",
        order_by="RuntimeSourceSpan.created_at",
        overlaps="source,spans",
    )


class RuntimeSourceSpan(RuntimeBase):
    __tablename__ = "runtime_source_spans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "user_id"],
            ["runtime_sources.id", "runtime_sources.user_id"],
            name="fk_runtime_source_spans_source_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["artifact_id", "user_id"],
            ["runtime_source_artifacts.id", "runtime_source_artifacts.user_id"],
            name="fk_runtime_source_spans_artifact_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_source_spans_id_user",
        ),
        UniqueConstraint(
            "artifact_id",
            "locator_hash",
            "content_hash",
            name="uq_runtime_source_spans_artifact_locator_hash",
        ),
        Index("ix_runtime_source_spans_user_source", "user_id", "source_id"),
        Index("ix_runtime_source_spans_user_artifact", "user_id", "artifact_id"),
        Index("ix_runtime_source_spans_user_kind", "user_id", "span_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    artifact_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    span_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    locator_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    locator_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    source: Mapped[RuntimeSource] = relationship(
        back_populates="spans",
        overlaps="artifact,spans",
    )
    artifact: Mapped[RuntimeSourceArtifact] = relationship(
        back_populates="spans",
        overlaps="source,spans",
    )

    @staticmethod
    def compute_locator_hash(locator_json: dict[str, object]) -> str:
        payload = json.dumps(locator_json, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class RuntimeKnowledgeConcept(RuntimeBase):
    __tablename__ = "runtime_knowledge_concepts"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "user_id",
            name="uq_runtime_knowledge_concepts_id_user",
        ),
        UniqueConstraint(
            "user_id",
            "slug",
            name="uq_runtime_knowledge_concepts_user_slug",
        ),
        Index(
            "ix_runtime_knowledge_concepts_user_type_status",
            "user_id",
            "concept_type",
            "status",
        ),
        Index("ix_runtime_knowledge_concepts_user_title", "user_id", "title"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    concept_type: Mapped[str] = mapped_column(String(48), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    frontmatter_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    compiled_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)

    source_links: Mapped[list[RuntimeKnowledgeConceptSource]] = relationship(
        back_populates="concept",
        cascade="all, delete-orphan",
        order_by="RuntimeKnowledgeConceptSource.created_at",
        foreign_keys="RuntimeKnowledgeConceptSource.concept_id",
    )
    outgoing_links: Mapped[list[RuntimeKnowledgeLink]] = relationship(
        back_populates="source_concept",
        cascade="all, delete-orphan",
        foreign_keys="RuntimeKnowledgeLink.source_concept_id",
    )
    incoming_links: Mapped[list[RuntimeKnowledgeLink]] = relationship(
        back_populates="target_concept",
        cascade="all, delete-orphan",
        foreign_keys="RuntimeKnowledgeLink.target_concept_id",
    )


class RuntimeKnowledgeConceptSource(RuntimeBase):
    __tablename__ = "runtime_knowledge_concept_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["concept_id", "user_id"],
            ["runtime_knowledge_concepts.id", "runtime_knowledge_concepts.user_id"],
            name="fk_runtime_knowledge_concept_sources_concept_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_id", "user_id"],
            ["runtime_sources.id", "runtime_sources.user_id"],
            name="fk_runtime_knowledge_concept_sources_source_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["span_id", "user_id"],
            ["runtime_source_spans.id", "runtime_source_spans.user_id"],
            name="fk_runtime_knowledge_concept_sources_span_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "concept_id",
            "span_id",
            name="uq_runtime_knowledge_concept_sources_concept_span",
        ),
        Index(
            "ix_runtime_knowledge_concept_sources_user_concept",
            "user_id",
            "concept_id",
        ),
        Index(
            "ix_runtime_knowledge_concept_sources_user_source",
            "user_id",
            "source_id",
        ),
        Index(
            "ix_runtime_knowledge_concept_sources_user_span",
            "user_id",
            "span_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    concept_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    span_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    citation_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quote_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    concept: Mapped[RuntimeKnowledgeConcept] = relationship(
        back_populates="source_links",
        foreign_keys=[concept_id],
    )


class RuntimeKnowledgeLink(RuntimeBase):
    __tablename__ = "runtime_knowledge_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_concept_id", "user_id"],
            ["runtime_knowledge_concepts.id", "runtime_knowledge_concepts.user_id"],
            name="fk_runtime_knowledge_links_source_concept_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_concept_id", "user_id"],
            ["runtime_knowledge_concepts.id", "runtime_knowledge_concepts.user_id"],
            name="fk_runtime_knowledge_links_target_concept_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "user_id",
            "source_concept_id",
            "target_concept_id",
            "link_type",
            name="uq_runtime_knowledge_links_user_source_target_type",
        ),
        Index("ix_runtime_knowledge_links_user_source", "user_id", "source_concept_id"),
        Index("ix_runtime_knowledge_links_user_target", "user_id", "target_concept_id"),
        Index("ix_runtime_knowledge_links_user_type", "user_id", "link_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_concept_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    target_concept_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    link_type: Mapped[str] = mapped_column(String(48), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    source_concept: Mapped[RuntimeKnowledgeConcept] = relationship(
        back_populates="outgoing_links",
        foreign_keys=[source_concept_id],
    )
    target_concept: Mapped[RuntimeKnowledgeConcept] = relationship(
        back_populates="incoming_links",
        foreign_keys=[target_concept_id],
    )


class RuntimeKnowledgeBundleRun(RuntimeBase):
    __tablename__ = "runtime_knowledge_bundle_runs"
    __table_args__ = (
        Index("ix_runtime_knowledge_bundle_runs_user_type_status", "user_id", "run_type", "status"),
        Index("ix_runtime_knowledge_bundle_runs_user_source", "user_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    run_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    input_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )


class RuntimeStep(RuntimeBase):
    __tablename__ = "runtime_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_index", name="uq_runtime_steps_run_id_step_index"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    response_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    tool_calls_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    usage_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[RuntimeRun] = relationship(back_populates="steps")


@event.listens_for(RuntimeStep, "load")
def _hydrate_sealed_runtime_step(
    step: RuntimeStep,
    _context: object,
) -> None:
    runtime_db = object_session(step)
    if runtime_db is None or step.id is None:
        return
    owner_id = runtime_db.scalar(
        select(RuntimeThread.user_id).where(RuntimeThread.id == step.thread_id)
    )
    if owner_id is None:
        raise ValueError("sealed Runtime step owner is missing")
    from anima_server.services.corefs.sealed_runtime import load_runtime_record

    payload = load_runtime_record(
        runtime_db,
        row_type="runtime_step",
        row_id=int(step.id),
        owner_id=int(owner_id),
    )
    if payload is None:
        return
    request_json = payload.get("request_json")
    response_json = payload.get("response_json")
    tool_calls_json = payload.get("tool_calls_json")
    if not isinstance(request_json, dict):
        raise ValueError("sealed Runtime step request is invalid")
    if not isinstance(response_json, dict):
        raise ValueError("sealed Runtime step response is invalid")
    if tool_calls_json is not None and not isinstance(tool_calls_json, list):
        raise ValueError("sealed Runtime step tool calls are invalid")
    set_committed_value(step, "request_json", request_json)
    set_committed_value(step, "response_json", response_json)
    set_committed_value(step, "tool_calls_json", tool_calls_json)


class RuntimeMessage(RuntimeBase):
    __tablename__ = "runtime_messages"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_runtime_messages_id_user"),
        UniqueConstraint(
            "thread_id", "sequence_id", name="uq_runtime_messages_thread_id_sequence_id"
        ),
        Index("ix_runtime_messages_user_created", "user_id", "created_at"),
        Index("ix_runtime_messages_thread_context", "thread_id", "is_in_context"),
        Index("ix_runtime_messages_thread_archived_history", "thread_id", "is_archived_history"),
        Index("ix_runtime_messages_corefs_message_id", "corefs_message_id"),
        Index("ix_runtime_messages_corefs_sequence_id", "corefs_sequence_id"),
        Index("ux_runtime_messages_corefs_event_id", "corefs_event_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runtime_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    step_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runtime_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_args_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    is_in_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_archived_history: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    corefs_message_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    corefs_event_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    corefs_sequence_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    thread: Mapped[RuntimeThread] = relationship(back_populates="messages")
    image_links: Mapped[list[RuntimeImageMessageLink]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="RuntimeImageMessageLink.created_at",
        overlaps="image_asset,message_links",
    )

    @property
    def is_internal(self) -> bool:
        """True if this message is agent-loop machinery, not user-visible content.

        The agent loop produces an assistant row (tool-call wrapper) paired with
        a tool-result row for every tool call.  Only `send_message` tool results
        carry user-facing text; everything else is internal plumbing.
        """
        if (
            self.role == "assistant"
            and isinstance(self.content_json, dict)
            and "tool_calls" in self.content_json
        ):
            return True
        return (
            self.role == "tool" and self.tool_name is not None and self.tool_name != "send_message"
        )


@event.listens_for(RuntimeMessage, "load")
def _hydrate_sealed_runtime_message(
    message: RuntimeMessage,
    _context: object,
) -> None:
    runtime_db = object_session(message)
    if runtime_db is None or message.id is None:
        return
    from anima_server.services.corefs.sealed_runtime import load_runtime_record

    payload = load_runtime_record(
        runtime_db,
        row_type="runtime_message",
        row_id=int(message.id),
        owner_id=int(message.user_id),
    )
    if payload is None:
        return
    content_text = payload.get("content_text")
    content_json = payload.get("content_json")
    tool_args_json = payload.get("tool_args_json")
    if content_text is not None and not isinstance(content_text, str):
        raise ValueError("sealed Runtime message text is invalid")
    if content_json is not None and not isinstance(content_json, dict):
        raise ValueError("sealed Runtime message content JSON is invalid")
    if tool_args_json is not None and not isinstance(tool_args_json, dict):
        raise ValueError("sealed Runtime message tool arguments are invalid")
    set_committed_value(message, "content_text", content_text)
    set_committed_value(message, "content_json", content_json)
    set_committed_value(message, "tool_args_json", tool_args_json)


class RuntimeImageMessageLink(RuntimeBase):
    __tablename__ = "runtime_image_message_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "user_id"],
            ["runtime_messages.id", "runtime_messages.user_id"],
            name="fk_runtime_image_message_links_message_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["image_asset_id", "user_id"],
            ["runtime_image_assets.id", "runtime_image_assets.user_id"],
            name="fk_runtime_image_message_links_asset_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "message_id",
            "attachment_id",
            name="uq_runtime_image_message_links_message_attachment",
        ),
        Index(
            "ix_runtime_image_message_links_user_message",
            "user_id",
            "message_id",
        ),
        Index(
            "ix_runtime_image_message_links_user_asset",
            "user_id",
            "image_asset_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    image_asset_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    attachment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    message: Mapped[RuntimeMessage] = relationship(
        back_populates="image_links",
        overlaps="image_asset,message_links",
    )
    image_asset: Mapped[RuntimeImageAsset] = relationship(
        back_populates="message_links",
        overlaps="image_links,message",
    )


class RuntimeBackgroundTaskRun(RuntimeBase):
    """Tracked background task execution for debugging and monitoring."""

    __tablename__ = "runtime_background_task_runs"
    __table_args__ = (Index("ix_runtime_bg_task_runs_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    task_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # consolidation, graph_ingestion, heat_decay, episode_gen, etc.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
    )  # pending, running, completed, failed
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )


class RuntimeConsolidationCursor(RuntimeBase):
    """Restart cursor for background memory consolidation.

    Records the last runtime-message id processed per ``(user_id, thread_id)``
    scope.  This replaces scanning + mutating
    ``RuntimeBackgroundTaskRun.result_json``: the cursor now survives task-run
    pruning and lookups are a single indexed row rather than a full scan of
    every completed consolidation run.  ``thread_id`` is nullable for the
    thread-agnostic ("global") scope; uniqueness of that scope is enforced in
    the accessor's select-then-upsert since SQL treats NULLs as distinct.
    """

    __tablename__ = "runtime_consolidation_cursors"
    __table_args__ = (
        Index(
            "ix_runtime_consolidation_cursor_scope",
            "user_id",
            "thread_id",
            unique=True,
        ),
        # NULLs are distinct in a unique index, so the composite index above
        # does not constrain the thread-agnostic scope; a partial unique index
        # enforces one row per user for thread_id IS NULL.
        Index(
            "ix_runtime_consolidation_cursor_global",
            "user_id",
            unique=True,
            postgresql_where=text("thread_id IS NULL"),
            sqlite_where=text("thread_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_processed_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    messages_processed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
