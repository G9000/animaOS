"""Merge the agent-runtime-hardening and knowledge-ingestion heads.

The hardening stack added 022..026 branching from 021, while main added
1c3df376a170 (source knowledge ingestion) also branching from 021.  This is a
no-op merge revision that reunites the two lineages into a single head.

Revision ID: 027_merge_reembed_knowledge
Revises: 026_reembed_completions, 1c3df376a170
Create Date: 2026-07-08
"""

from __future__ import annotations

revision = "027_merge_reembed_knowledge"
down_revision = ("026_reembed_completions", "1c3df376a170")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
