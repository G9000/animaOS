"""Merge affect-state and parse-quality runtime heads.

Revision ID: 028_merge_runtime_heads
Revises: 027_affect_state, 027_parse_quality
Create Date: 2026-07-17
"""

from __future__ import annotations

revision = "028_merge_runtime_heads"
down_revision = ("027_affect_state", "027_parse_quality")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
