"""Exclude folded candidates from the active-hash unique index.

IL-004 added the terminal status ``folded`` for weak candidates absorbed
into latent traces. The Python-side active lookup already ignores them
(``_TERMINAL_STATUSES``), but the partial unique index from
019_candidate_salience still treated folded rows as active — so a repeated
identical weak signal hit IntegrityError instead of creating the new
candidate row needed to fold again, preventing exact repeats from ever
accumulating to crystallization.

Revision ID: 029_folded_candidate_hash
Revises: 028_presence_catchup
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op

revision = "029_folded_candidate_hash"
down_revision = "028_presence_catchup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS: repair paths replay migrations over metadata-created
    # schemas where the 019 index was never present.
    op.execute("DROP INDEX IF EXISTS uq_memory_candidates_active_hash")
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_candidates_active_hash "
        "ON memory_candidates(content_hash) "
        "WHERE status NOT IN ('rejected', 'reinforced', 'superseded', 'failed', 'folded')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_memory_candidates_active_hash")
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_candidates_active_hash "
        "ON memory_candidates(content_hash) "
        "WHERE status NOT IN ('rejected', 'reinforced', 'superseded', 'failed')"
    )
