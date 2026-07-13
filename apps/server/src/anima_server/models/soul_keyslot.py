from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.base import Base


class SoulKeyslot(Base):
    __tablename__ = "soul_keyslots"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "domain",
            "wrapping_path",
            "key_version",
            "credential_generation",
            "status",
            name="uq_soul_keyslots_identity_status",
        ),
        CheckConstraint(
            "wrapping_path IN ('password', 'recovery')",
            name="ck_soul_keyslots_wrapping_path",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'decrypt-only')",
            name="ck_soul_keyslots_status",
        ),
        CheckConstraint("key_version > 0", name="ck_soul_keyslots_key_version"),
        CheckConstraint(
            "credential_generation > 0",
            name="ck_soul_keyslots_credential_generation",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(64), nullable=False)
    wrapping_path: Mapped[str] = mapped_column(String(16), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    credential_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    kdf_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    wrap_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    envelope_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_salt: Mapped[str] = mapped_column(String(255), nullable=False)
    kdf_time_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_memory_cost_kib: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_parallelism: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_key_length: Mapped[int] = mapped_column(Integer, nullable=False)
    wrap_iv: Mapped[str] = mapped_column(String(255), nullable=False)
    wrap_tag: Mapped[str] = mapped_column(String(255), nullable=False)
    wrapped_dek: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
