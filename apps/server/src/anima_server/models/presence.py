from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.base import Base


class PresenceConfig(Base):
    __tablename__ = "presence_configs"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_presence_configs_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    main_chat_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    home_greeting_context_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    task_nudges_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    memory_nudges_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    checkin_nudges_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    custom_instruction: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
