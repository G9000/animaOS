from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.runtime_base import RuntimeBase


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CoreFSRuntimeBinding(RuntimeBase):
    """Authoritative database-local claim for one Core Runtime instance."""

    __tablename__ = "corefs_runtime_binding"
    __table_args__ = (
        CheckConstraint("binding_slot = 1", name="ck_corefs_runtime_binding_singleton"),
    )

    binding_slot: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    core_id: Mapped[str] = mapped_column(String(64), nullable=False)
    local_instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class CoreFSIndexEntry(RuntimeBase):
    """Opaque catalog metadata for one rebuildable CoreFS index entry."""

    __tablename__ = "corefs_index_entries"
    __table_args__ = (
        UniqueConstraint(
            "core_id",
            "local_instance_id",
            "family",
            "object_id_hash",
            "revision_hash",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    core_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    local_instance_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class CoreFSIndexCheckpoint(RuntimeBase):
    """Progress marker for a resumable, instance-scoped index family."""

    __tablename__ = "corefs_index_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "core_id",
            "local_instance_id",
            "family",
            "catalog_generation",
            "index_version",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    core_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    local_instance_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    catalog_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    cursor_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class CoreFSBlindToken(RuntimeBase):
    """Keyed lookup token without the indexed CoreFS value."""

    __tablename__ = "corefs_blind_tokens"
    __table_args__ = (
        UniqueConstraint(
            "core_id",
            "local_instance_id",
            "family",
            "generation",
            "token",
            "object_id_hash",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    core_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    local_instance_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    token: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, index=True)
    object_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class CoreFSMigrationJournal(RuntimeBase):
    """Plaintext-free progress journal for forward-only Core converters."""

    __tablename__ = "corefs_migration_journal"
    __table_args__ = (
        UniqueConstraint(
            "core_id",
            "local_instance_id",
            "converter_id",
            "source_id_hash",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    core_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    local_instance_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    converter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_cursor_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    migrated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class CoreFSSealedPayload(RuntimeBase):
    """Crash-durable sensitive Runtime data encrypted outside PostgreSQL."""

    __tablename__ = "corefs_sealed_payloads"
    __table_args__ = (
        UniqueConstraint(
            "core_id",
            "local_instance_id",
            "row_type",
            "row_id_hash",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    core_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    local_instance_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    row_type: Mapped[str] = mapped_column(String(48), nullable=False)
    row_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    aad_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
