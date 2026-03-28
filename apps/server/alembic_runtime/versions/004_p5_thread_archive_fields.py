"""P5: add runtime thread archival fields and active-thread constraint."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_runtime_threads_user_id", table_name="runtime_threads")
    op.create_index("ix_runtime_threads_user_id", "runtime_threads", ["user_id"])

    op.add_column(
        "runtime_threads",
        sa.Column("closed_at", TIMESTAMPTZ, nullable=True),
    )
    op.add_column(
        "runtime_threads",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_index(
        "ix_runtime_threads_user_status",
        "runtime_threads",
        ["user_id", "status"],
    )
    op.create_index(
        "uq_runtime_threads_active_user",
        "runtime_threads",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_runtime_threads_active_user", table_name="runtime_threads")
    op.drop_index("ix_runtime_threads_user_status", table_name="runtime_threads")
    op.drop_column("runtime_threads", "is_archived")
    op.drop_column("runtime_threads", "closed_at")

    op.drop_index("ix_runtime_threads_user_id", table_name="runtime_threads")
    op.create_index("ix_runtime_threads_user_id", "runtime_threads", ["user_id"], unique=True)
