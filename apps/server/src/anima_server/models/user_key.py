from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.base import Base


class UserKey(Base):
    __tablename__ = "user_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "domain", name="uq_user_keys_user_domain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="memories",
    )
    kdf_salt: Mapped[str] = mapped_column(String(255), nullable=False)
    kdf_time_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_memory_cost_kib: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_parallelism: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_key_length: Mapped[int] = mapped_column(Integer, nullable=False)
    wrap_iv: Mapped[str] = mapped_column(String(255), nullable=False)
    wrap_tag: Mapped[str] = mapped_column(String(255), nullable=False)
    wrapped_dek: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
