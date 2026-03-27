"""Baseline Alembic revision for the Python server."""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260311_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Initialize Alembic version tracking."""
    pass


def downgrade() -> None:
    """Remove the baseline revision."""
    pass
