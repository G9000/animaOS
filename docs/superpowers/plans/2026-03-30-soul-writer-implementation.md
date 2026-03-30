# Soul Writer Architecture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate `database is locked` errors by moving all automated conversation-path writes from SQLCipher to PostgreSQL, with a single Soul Writer component promoting memories to SQLCipher on signal-based triggers.

**Architecture:** Three-layer split — PG runtime (live mind), Soul Writer (single serialized promoter), SQLCipher vault (settled identity). Background extraction writes MemoryCandidates to PG. Soul Writer promotes them to SQLCipher on inactivity, pre-turn check, compaction, shutdown, or threshold triggers. Per-item transactions. Idempotent via content hashing.

**Tech Stack:** Python/FastAPI, SQLAlchemy, Alembic (dual-engine: PG runtime + SQLCipher soul), asyncio

**Spec:** `docs/superpowers/specs/2026-03-29-soul-writer-architecture-design.md`

---

## File Map

### New files
| File | Responsibility |
|------|---------------|
| `alembic_runtime/versions/006_soul_writer_tables.py` | PG migration: memory_candidates, promotion_journal, memory_access_log |
| `alembic_runtime/versions/007_pending_ops_content_hash.py` | PG migration: add content_hash to pending_memory_ops |
| `alembic_core/versions/YYYYMMDD_drop_daily_logs.py` | SQLCipher migration: drop memory_daily_logs |
| `models/runtime_memory.py` | SQLAlchemy models: MemoryCandidate, PromotionJournal, MemoryAccessLog |
| `services/agent/soul_writer.py` | Soul Writer orchestrator (new — existing CRUD file renamed to soul_blocks.py) |
| `tests/test_soul_writer.py` | Soul Writer unit tests |
| `tests/test_memory_candidates.py` | Candidate creation, dedup, lifecycle tests |
| `tests/test_daily_log_removal.py` | Verify DailyLog readers migrated to RuntimeMessage |

### Renamed files
| From | To | Reason |
|------|-----|--------|
| `services/agent/soul_writer.py` | `services/agent/soul_blocks.py` | Free up `soul_writer.py` for the orchestrator |

### Modified files (key changes)
| File | Change |
|------|--------|
| `services/agent/consolidation.py` | Replace per-turn soul writes with `run_background_extraction` |
| `services/agent/memory_store.py` | Delete `add_daily_log`, modify `touch_memory_items`, add `dry_run` to `store_memory_item` |
| `services/agent/memory_blocks.py` | Thread `runtime_db` to `touch_memory_items` and `get_memory_items_scored` |
| `services/agent/reflection.py` | Call `run_soul_writer` before orchestrator tasks |
| `services/agent/sleep_agent.py` | Remove `SLEEPTIME_FREQUENCY`, `_commit_with_retry`, rewire `_task_consolidation` |
| `services/agent/service.py` | Add pre-turn check, compaction trigger |
| `services/agent/tools.py` | `save_to_memory` → MemoryCandidate |
| `services/agent/session_memory.py` | `promote_session_note` → MemoryCandidate |
| `services/agent/feedback_signals.py` | Split growth log (PendingMemoryOp) from corrections (MemoryCandidate) |
| `services/agent/episodes.py` | Read RuntimeMessage instead of MemoryDailyLog |
| `services/agent/conversation_search.py` | Remove `_search_daily_logs` branch |
| `services/agent/inner_monologue.py` | Read RuntimeMessage instead of MemoryDailyLog |
| `api/routes/chat.py` | Journal stats from RuntimeMessage |
| `services/vault.py` | Remove memoryDailyLogs from export/import |
| `main.py` | Shutdown trigger |

All paths are relative to `apps/server/src/anima_server/`.

---

## Phase 1: Foundation (no behavior change)

### Task 1: Create PG runtime models for Soul Writer tables

**Files:**
- Create: `apps/server/src/anima_server/models/runtime_memory.py`
- Test: `apps/server/tests/test_memory_candidates.py`

- [ ] **Step 1: Write the model file**

```python
# apps/server/src/anima_server/models/runtime_memory.py
"""PostgreSQL runtime models for the Soul Writer pipeline.

MemoryCandidate: extracted observations awaiting promotion to soul.
PromotionJournal: audit trail for Soul Writer decisions.
MemoryAccessLog: access tracking (replaces per-turn touch_memory_items writes to SQLCipher).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP as _PG_TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.runtime_base import RuntimeBase

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)


class MemoryCandidate(RuntimeBase):
    """Extracted observation awaiting promotion to SQLCipher soul."""

    __tablename__ = "memory_candidates"
    __table_args__ = (
        Index("ix_memory_candidates_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    importance_source: Mapped[str] = mapped_column(String(32), nullable=False, default="llm")
    supersedes_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_message_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), nullable=True
    )
    extraction_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="extracted")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)


class PromotionJournal(RuntimeBase):
    """Audit trail for Soul Writer promotion decisions."""

    __tablename__ = "promotion_journal"
    __table_args__ = (
        Index("ix_promotion_journal_user", "user_id"),
        Index("ix_promotion_journal_hash", "content_hash", "decision"),
        Index("ix_promotion_journal_status", "journal_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pending_op_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_table: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    journal_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="tentative"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )


class MemoryAccessLog(RuntimeBase):
    """PG-side access tracking, replaces per-turn touch_memory_items SQLCipher writes."""

    __tablename__ = "memory_access_log"
    __table_args__ = (
        Index("ix_memory_access_log_user_item", "user_id", "memory_item_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    accessed_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

- [ ] **Step 2: Write the basic model tests**

```python
# apps/server/tests/test_memory_candidates.py
"""Tests for Soul Writer runtime models and candidate lifecycle."""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime_memory import (
    MemoryAccessLog,
    MemoryCandidate,
    PromotionJournal,
)


@pytest.fixture()
def pg_session():
    """In-memory PG-like SQLite session for model tests."""
    engine = create_engine("sqlite:///:memory:")
    RuntimeBase.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    RuntimeBase.metadata.drop_all(bind=engine)


def _content_hash(user_id: int, category: str, importance_source: str, content: str) -> str:
    normalized = content.strip().lower()
    return hashlib.sha256(f"{user_id}:{category}:{importance_source}:{normalized}".encode()).hexdigest()


def test_create_memory_candidate(pg_session: Session) -> None:
    candidate = MemoryCandidate(
        user_id=1,
        content="Has a dog named Biscuit",
        category="fact",
        importance=3,
        importance_source="llm",
        source="llm",
        content_hash=_content_hash(1, "fact", "llm", "Has a dog named Biscuit"),
        status="extracted",
    )
    pg_session.add(candidate)
    pg_session.flush()
    assert candidate.id is not None
    assert candidate.status == "extracted"
    assert candidate.retry_count == 0


def test_create_promotion_journal(pg_session: Session) -> None:
    entry = PromotionJournal(
        user_id=1,
        decision="promoted",
        reason="new memory",
        target_table="memory_items",
        target_record_id="42",
        content_hash="abc123",
        journal_status="confirmed",
    )
    pg_session.add(entry)
    pg_session.flush()
    assert entry.id is not None


def test_create_memory_access_log(pg_session: Session) -> None:
    log = MemoryAccessLog(
        user_id=1,
        memory_item_id=42,
        synced=False,
    )
    pg_session.add(log)
    pg_session.flush()
    assert log.id is not None
    assert log.synced is False


def test_candidate_status_lifecycle(pg_session: Session) -> None:
    candidate = MemoryCandidate(
        user_id=1, content="test", category="fact",
        importance=3, importance_source="llm", source="llm",
        content_hash=_content_hash(1, "fact", "llm", "test"),
    )
    pg_session.add(candidate)
    pg_session.flush()

    # extracted → promoted
    candidate.status = "promoted"
    candidate.processed_at = candidate.created_at
    pg_session.flush()
    assert candidate.status == "promoted"


def test_correction_and_extraction_get_distinct_hashes(pg_session: Session) -> None:
    """Correction and extraction candidates with same content get different hashes."""
    hash_llm = _content_hash(1, "fact", "llm", "likes cats")
    hash_correction = _content_hash(1, "fact", "correction", "likes cats")
    assert hash_llm != hash_correction
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/test_memory_candidates.py -v`
Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/models/runtime_memory.py apps/server/tests/test_memory_candidates.py
git commit -m "feat(soul-writer): add PG runtime models — MemoryCandidate, PromotionJournal, MemoryAccessLog"
```

---

### Task 2: Create Alembic migration for Soul Writer PG tables

**Files:**
- Create: `apps/server/alembic_runtime/versions/006_soul_writer_tables.py`

- [ ] **Step 1: Write the migration**

```python
# apps/server/alembic_runtime/versions/006_soul_writer_tables.py
"""Soul Writer pipeline tables: memory_candidates, promotion_journal, memory_access_log.

Revision ID: 006_soul_writer
Revises: 005_p6_pgvector_embeddings
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP

revision = "006_soul_writer"
down_revision = "005_p6_pgvector_embeddings"
branch_labels = None
depends_on = None

TIMESTAMPTZ = TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("importance", sa.Integer, nullable=False, server_default="3"),
        sa.Column("importance_source", sa.String(32), nullable=False, server_default="'llm'"),
        sa.Column("supersedes_item_id", sa.Integer, nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_message_ids", ARRAY(sa.Integer), nullable=True),
        sa.Column("extraction_model", sa.String(128), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="'extracted'"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", TIMESTAMPTZ, nullable=True),
    )
    op.create_index(
        "ix_memory_candidates_user_status",
        "memory_candidates",
        ["user_id", "status"],
    )
    # Partial unique index: prevent duplicate active candidates with same hash.
    # Terminal-state rows excluded so re-extraction is possible.
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_candidates_active_hash "
        "ON memory_candidates(content_hash) "
        "WHERE status NOT IN ('rejected', 'superseded', 'failed')"
    )
    # Partial index for unsynced access log rows
    op.create_table(
        "promotion_journal",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("candidate_id", sa.BigInteger, nullable=True),
        sa.Column("pending_op_id", sa.BigInteger, nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("target_table", sa.String(32), nullable=True),
        sa.Column("target_record_id", sa.String(64), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("extraction_model", sa.String(128), nullable=True),
        sa.Column("journal_status", sa.String(16), nullable=False, server_default="'tentative'"),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_promotion_journal_user", "promotion_journal", ["user_id"])
    op.create_index(
        "ix_promotion_journal_hash",
        "promotion_journal",
        ["content_hash", "decision"],
    )
    op.create_index(
        "ix_promotion_journal_status",
        "promotion_journal",
        ["journal_status"],
    )

    op.create_table(
        "memory_access_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("memory_item_id", sa.Integer, nullable=False),
        sa.Column("accessed_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("synced", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_memory_access_log_user_item",
        "memory_access_log",
        ["user_id", "memory_item_id"],
    )
    op.execute(
        "CREATE INDEX ix_memory_access_log_unsynced "
        "ON memory_access_log(user_id) WHERE synced = FALSE"
    )


def downgrade() -> None:
    op.drop_table("memory_access_log")
    op.drop_table("promotion_journal")
    op.drop_table("memory_candidates")
```

- [ ] **Step 2: Commit**

```bash
git add apps/server/alembic_runtime/versions/006_soul_writer_tables.py
git commit -m "feat(soul-writer): Alembic migration for PG Soul Writer tables"
```

---

### Task 3: Add content_hash to PendingMemoryOp

**Files:**
- Create: `apps/server/alembic_runtime/versions/007_pending_ops_content_hash.py`
- Modify: `apps/server/src/anima_server/models/pending_memory_op.py`
- Modify: `apps/server/src/anima_server/services/agent/pending_ops.py`
- Test: `apps/server/tests/test_memory_candidates.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `apps/server/tests/test_memory_candidates.py`:

```python
def test_pending_memory_op_has_content_hash() -> None:
    """PendingMemoryOp model has content_hash column."""
    from anima_server.models.pending_memory_op import PendingMemoryOp

    columns = {c.name for c in PendingMemoryOp.__table__.columns}
    assert "content_hash" in columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/test_memory_candidates.py::test_pending_memory_op_has_content_hash -v`
Expected: FAIL — `content_hash` not in columns.

- [ ] **Step 3: Add content_hash column to PendingMemoryOp model**

In `apps/server/src/anima_server/models/pending_memory_op.py`, add after the `failed` column:

```python
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

- [ ] **Step 4: Write the Alembic migration**

```python
# apps/server/alembic_runtime/versions/007_pending_ops_content_hash.py
"""Add content_hash to pending_memory_ops for idempotent replay.

Revision ID: 007_pending_ops_hash
Revises: 006_soul_writer
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "007_pending_ops_hash"
down_revision = "006_soul_writer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pending_memory_ops",
        sa.Column("content_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pending_memory_ops", "content_hash")
```

- [ ] **Step 5: Update create_pending_op to compute content_hash**

In `apps/server/src/anima_server/services/agent/pending_ops.py`, modify `create_pending_op`:

```python
import hashlib

def create_pending_op(
    runtime_db: Session,
    *,
    user_id: int,
    op_type: str,
    target_block: str,
    content: str,
    old_content: str | None,
    source_run_id: int | None,
    source_tool_call_id: str | None,
) -> PendingMemoryOp:
    """Persist a pending identity write in the runtime store."""
    normalized_type = op_type.strip().lower()
    if normalized_type not in _VALID_OP_TYPES:
        raise ValueError(f"Invalid pending op type: {op_type}")

    content_hash = hashlib.sha256(
        f"{user_id}:{target_block.strip()}:{normalized_type}:{content.strip()}".encode()
    ).hexdigest()

    op = PendingMemoryOp(
        user_id=user_id,
        op_type=normalized_type,
        target_block=target_block.strip(),
        content=content.strip(),
        old_content=old_content,
        source_run_id=source_run_id,
        source_tool_call_id=source_tool_call_id,
        content_hash=content_hash,
    )
    runtime_db.add(op)
    runtime_db.flush()
    return op
```

- [ ] **Step 6: Run tests to verify**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/test_memory_candidates.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add apps/server/src/anima_server/models/pending_memory_op.py apps/server/src/anima_server/services/agent/pending_ops.py apps/server/alembic_runtime/versions/007_pending_ops_content_hash.py apps/server/tests/test_memory_candidates.py
git commit -m "feat(soul-writer): add content_hash to PendingMemoryOp for idempotent replay"
```

---

## Phase 2: MemoryDailyLog Removal

### Task 4: Migrate episode generation from MemoryDailyLog to RuntimeMessage

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/episodes.py`
- Test: `apps/server/tests/test_agent_episodes.py`

- [ ] **Step 1: Read current episodes.py to understand the DailyLog usage**

The current code queries `MemoryDailyLog` by user_id + date range, extracts user_message and assistant_response fields (decrypted via `df()`), and passes them to the LLM for episode generation.

Replace with: query `RuntimeMessage` by user_id + date range, filter role in ('user', 'assistant'), extract content_text.

- [ ] **Step 2: Update episodes.py imports and queries**

Replace `MemoryDailyLog` import with `RuntimeMessage`:

```python
# In episodes.py, replace:
from anima_server.models import MemoryDailyLog, MemoryEpisode
# With:
from anima_server.models import MemoryEpisode
from anima_server.models.runtime import RuntimeMessage
```

Replace the MemoryDailyLog query in `maybe_generate_episode` with:

```python
# Instead of querying MemoryDailyLog, query RuntimeMessage
from anima_server.db.runtime import get_runtime_session_factory

try:
    rt_factory = get_runtime_session_factory()
except RuntimeError:
    return None

with rt_factory() as rt_db:
    messages = list(
        rt_db.scalars(
            select(RuntimeMessage)
            .where(
                RuntimeMessage.user_id == user_id,
                RuntimeMessage.role.in_(("user", "assistant")),
                RuntimeMessage.created_at >= datetime.now(UTC) - timedelta(hours=24),
            )
            .order_by(RuntimeMessage.created_at)
        ).all()
    )
```

Convert messages to the `(user_msg, assistant_msg)` tuple format the rest of the function expects. RuntimeMessage content is plaintext (no decryption needed).

- [ ] **Step 3: Run existing episode tests**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/test_agent_episodes.py -v`
Expected: Tests may need fixture updates to use RuntimeMessage instead of MemoryDailyLog.

- [ ] **Step 4: Update episode test fixtures**

Replace MemoryDailyLog fixtures with RuntimeMessage rows in the test's in-memory DB setup.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/episodes.py apps/server/tests/test_agent_episodes.py
git commit -m "refactor(soul-writer): migrate episode generation from MemoryDailyLog to RuntimeMessage"
```

---

### Task 5: Migrate remaining MemoryDailyLog readers

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/conversation_search.py`
- Modify: `apps/server/src/anima_server/services/agent/inner_monologue.py`
- Modify: `apps/server/src/anima_server/api/routes/chat.py`
- Modify: `apps/server/src/anima_server/services/vault.py`
- Modify: `apps/server/src/anima_server/services/agent/batch_segmenter.py`

- [ ] **Step 1: conversation_search.py — remove `_search_daily_logs` branch**

The function `search_conversation_history` has two search paths: RuntimeMessage (already implemented) and MemoryDailyLog. Remove the MemoryDailyLog path entirely. Delete the `_search_daily_logs` helper function.

- [ ] **Step 2: inner_monologue.py — replace DailyLog reads with RuntimeMessage**

Find all `MemoryDailyLog` references in inner_monologue.py and replace with RuntimeMessage queries. The monologue uses recent conversation context — RuntimeMessage provides the same data.

- [ ] **Step 3: chat.py — journal stats from RuntimeMessage**

Replace `MemoryDailyLog.date` count with `RuntimeMessage` date-based count:

```python
# Replace:
journal_total = db.scalar(
    select(func.count(func.distinct(MemoryDailyLog.date))).where(
        MemoryDailyLog.user_id == userId,
    )
) or 0

# With:
from anima_server.models.runtime import RuntimeMessage
journal_total = rt_db.scalar(
    select(func.count(func.distinct(func.date(RuntimeMessage.created_at)))).where(
        RuntimeMessage.user_id == userId,
        RuntimeMessage.role == "user",
    )
) or 0
```

- [ ] **Step 4: vault.py — remove memoryDailyLogs from export/import**

In `export_vault_snapshot`: remove the `memory_daily_logs` serialization block.
In `import_vault_snapshot`: skip `memoryDailyLogs` in import payload (ignore if present for backward compat).
Delete `serialize_memory_daily_log_record` helper.

- [ ] **Step 5: batch_segmenter.py — read from RuntimeMessage**

Replace MemoryDailyLog queries with RuntimeMessage queries, same pattern as episodes.py.

- [ ] **Step 6: Run all affected tests**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/test_agent_consolidation.py tests/test_active_recall.py tests/test_batch_segmenter.py tests/test_agent_reflection.py tests/test_chat.py tests/test_vault.py -v`

- [ ] **Step 7: Commit**

```bash
git add apps/server/src/anima_server/services/agent/conversation_search.py apps/server/src/anima_server/services/agent/inner_monologue.py apps/server/src/anima_server/api/routes/chat.py apps/server/src/anima_server/services/vault.py apps/server/src/anima_server/services/agent/batch_segmenter.py
git commit -m "refactor(soul-writer): migrate all MemoryDailyLog readers to RuntimeMessage"
```

---

### Task 6: Delete MemoryDailyLog model and add_daily_log

**Files:**
- Modify: `apps/server/src/anima_server/models/agent_runtime.py` (delete class)
- Modify: `apps/server/src/anima_server/models/__init__.py` (remove import)
- Modify: `apps/server/src/anima_server/services/agent/memory_store.py` (delete function)
- Modify: `apps/server/src/anima_server/services/agent/consolidation.py` (remove calls)
- Create: `apps/server/alembic_core/versions/YYYYMMDD_drop_daily_logs.py`

- [ ] **Step 1: Delete add_daily_log from memory_store.py**

Remove the `add_daily_log` function (line ~436) and the `MemoryDailyLog` import.

- [ ] **Step 2: Remove add_daily_log calls from consolidation.py**

In `consolidate_turn_memory` (line ~130): remove the `add_daily_log` call and the `log = ...` / `result.daily_log_id = log.id` lines.

- [ ] **Step 3: Delete MemoryDailyLog class from models/agent_runtime.py**

Remove the class definition (line ~484) and update `__init__.py` to remove `MemoryDailyLog` from exports.

- [ ] **Step 4: Write Alembic migration to drop the table**

```python
# apps/server/alembic_core/versions/20260330_drop_daily_logs.py
"""Drop memory_daily_logs table — redundant with RuntimeMessage.

Revision ID: 20260330_drop_daily_logs
Revises: 20260328_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20260330_drop_daily_logs"
down_revision = "20260328_0002"

def upgrade() -> None:
    with op.batch_alter_table("memory_daily_logs") as batch_op:
        batch_op.drop_index("ix_memory_daily_logs_user_date")
    op.drop_table("memory_daily_logs")

def downgrade() -> None:
    op.create_table(
        "memory_daily_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column("user_message", sa.Text, nullable=False),
        sa.Column("assistant_response", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_memory_daily_logs_user_date", "memory_daily_logs", ["user_id", "date"])
```

- [ ] **Step 5: Grep for any remaining MemoryDailyLog references**

Run: `cd apps/server && grep -rn "MemoryDailyLog\|memory_daily_log\|add_daily_log" src/ tests/ --include="*.py" | grep -v "__pycache__" | grep -v "alembic_core/versions"`
Expected: Zero results (or only the migration file).

- [ ] **Step 6: Run full test suite**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/ -x -q`
Expected: All tests pass (minus the pre-existing chat test failure).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(soul-writer): delete MemoryDailyLog — all readers migrated to RuntimeMessage"
```

---

## Phase 3: Access Tracking Migration

### Task 7: Redirect touch_memory_items to PG

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/memory_store.py:377`
- Modify: `apps/server/src/anima_server/services/agent/memory_blocks.py:221,246,271,349`
- Test: `apps/server/tests/test_memory_candidates.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_candidates.py`:

```python
def test_touch_memory_items_writes_to_pg_access_log(pg_session: Session) -> None:
    """touch_memory_items should create MemoryAccessLog rows in PG, not mutate SQLCipher."""
    from unittest.mock import MagicMock

    from anima_server.services.agent.memory_store import touch_memory_items

    # Create a mock MemoryItem
    mock_item = MagicMock()
    mock_item.id = 42
    mock_item.reference_count = 5
    mock_item.last_referenced_at = None

    touch_memory_items(db=MagicMock(), items=[mock_item], runtime_db=pg_session)

    # Verify access log row created in PG
    logs = pg_session.scalars(select(MemoryAccessLog)).all()
    assert len(logs) == 1
    assert logs[0].memory_item_id == 42
    assert logs[0].synced is False

    # Verify the soul db item was NOT mutated
    assert mock_item.reference_count == 5  # unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/test_memory_candidates.py::test_touch_memory_items_writes_to_pg_access_log -v`
Expected: FAIL — `touch_memory_items` doesn't accept `runtime_db` parameter yet.

- [ ] **Step 3: Modify touch_memory_items to write to PG**

In `memory_store.py`, replace the `touch_memory_items` function:

```python
def touch_memory_items(
    db: Session,
    items: list[MemoryItem],
    *,
    now: datetime | None = None,
    runtime_db: Session | None = None,
) -> None:
    """Log memory access to PG (if runtime_db available) for later sync to SQLCipher."""
    if not items:
        return
    ref_now = now or datetime.now(UTC)

    if runtime_db is not None:
        from anima_server.models.runtime_memory import MemoryAccessLog

        for item in items:
            runtime_db.add(MemoryAccessLog(
                user_id=item.user_id,
                memory_item_id=item.id,
                accessed_at=ref_now,
            ))
        runtime_db.flush()
    else:
        # Fallback: direct SQLCipher write (legacy path, removed in Phase 7)
        for item in items:
            item.reference_count = (item.reference_count or 0) + 1
            item.last_referenced_at = ref_now
        db.flush()
        try:
            from anima_server.services.agent.heat_scoring import update_heat_on_access
            update_heat_on_access(db, items, now=ref_now)
        except Exception:
            pass
```

- [ ] **Step 4: Thread runtime_db through memory_blocks.py**

In `memory_blocks.py`, update `build_facts_memory_block`, `build_preferences_memory_block`, `build_goals_memory_block`, `build_relationships_memory_block` to accept `runtime_db` and pass it to `touch_memory_items`:

```python
def build_facts_memory_block(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float] | None = None,
    agent_type: str = "companion",
    runtime_db: Session | None = None,
) -> MemoryBlock | None:
    items = get_memory_items_scored(
        db, user_id=user_id, category="fact", limit=30, query_embedding=query_embedding
    )
    if not items:
        return None
    touch_memory_items(db, items, runtime_db=runtime_db)
    # ... rest unchanged
```

Update `build_runtime_memory_blocks` to pass `runtime_db` to these four functions (it already receives `runtime_db` as a parameter).

- [ ] **Step 5: Run tests**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/test_memory_candidates.py tests/test_agent_memory_blocks.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/services/agent/memory_store.py apps/server/src/anima_server/services/agent/memory_blocks.py apps/server/tests/test_memory_candidates.py
git commit -m "feat(soul-writer): redirect touch_memory_items to PG memory_access_log"
```

---

### Task 8: Create sync_access_metadata function

**Files:**
- Create: logic in new `apps/server/src/anima_server/services/agent/soul_writer.py` (preliminary — will be expanded in Phase 4+5)
- Test: `apps/server/tests/test_memory_candidates.py` (append)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_sync_access_metadata(pg_session: Session) -> None:
    """sync_access_metadata aggregates PG access logs into counts."""
    from anima_server.services.agent.access_sync import sync_access_metadata

    # Create 3 access log entries for item 42
    for _ in range(3):
        pg_session.add(MemoryAccessLog(user_id=1, memory_item_id=42, synced=False))
    pg_session.flush()

    # Mock soul_db with a mock item
    result = await sync_access_metadata(
        user_id=1, runtime_db=pg_session, soul_db=None, dry_run=True,
    )
    assert result["items_synced"] == 1
    assert result["access_counts"] == {42: 3}
```

- [ ] **Step 2: Implement sync_access_metadata as standalone function**

Create `apps/server/src/anima_server/services/agent/access_sync.py`:

```python
"""Access metadata sync: PG memory_access_log → SQLCipher memory_items."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select, update, delete, func
from sqlalchemy.orm import Session

from anima_server.models.runtime_memory import MemoryAccessLog

logger = logging.getLogger(__name__)


async def sync_access_metadata(
    *,
    user_id: int,
    runtime_db: Session,
    soul_db: Session | None,
    dry_run: bool = False,
) -> dict:
    """Aggregate PG access log → SQLCipher memory_items.

    Crash-idempotent: snapshot unsynced, mark synced, apply delta, delete.
    """
    # 1. Aggregate unsynced rows
    rows = runtime_db.execute(
        select(
            MemoryAccessLog.memory_item_id,
            func.count(MemoryAccessLog.id).label("cnt"),
            func.max(MemoryAccessLog.accessed_at).label("last_access"),
        )
        .where(
            MemoryAccessLog.user_id == user_id,
            MemoryAccessLog.synced.is_(False),
        )
        .group_by(MemoryAccessLog.memory_item_id)
    ).all()

    if not rows:
        return {"items_synced": 0, "access_counts": {}}

    access_counts = {row.memory_item_id: row.cnt for row in rows}
    last_access = {row.memory_item_id: row.last_access for row in rows}

    # 2. Mark as synced (idempotent)
    runtime_db.execute(
        update(MemoryAccessLog)
        .where(
            MemoryAccessLog.user_id == user_id,
            MemoryAccessLog.synced.is_(False),
        )
        .values(synced=True)
    )
    runtime_db.flush()

    if dry_run or soul_db is None:
        return {"items_synced": len(access_counts), "access_counts": access_counts}

    # 3. Apply to SQLCipher
    from anima_server.models import MemoryItem

    for item_id, count in access_counts.items():
        item = soul_db.get(MemoryItem, item_id)
        if item is None:
            continue
        item.reference_count = (item.reference_count or 0) + count
        item.last_referenced_at = last_access[item_id]
    soul_db.flush()

    try:
        from anima_server.services.agent.heat_scoring import update_heat_on_access
        items = [soul_db.get(MemoryItem, iid) for iid in access_counts if soul_db.get(MemoryItem, iid)]
        if items:
            update_heat_on_access(soul_db, items)
    except Exception:
        pass

    soul_db.commit()

    # 4. Delete synced rows
    runtime_db.execute(
        delete(MemoryAccessLog).where(
            MemoryAccessLog.user_id == user_id,
            MemoryAccessLog.synced.is_(True),
        )
    )
    runtime_db.commit()

    return {"items_synced": len(access_counts), "access_counts": access_counts}
```

- [ ] **Step 3: Run test**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/test_memory_candidates.py::test_sync_access_metadata -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/services/agent/access_sync.py apps/server/tests/test_memory_candidates.py
git commit -m "feat(soul-writer): create sync_access_metadata for PG→SQLCipher access count sync"
```

---

## Phase 4+5: Background Extraction + Soul Writer Core

> This is the largest phase. Ship together — extraction without Soul Writer leaves candidates unprocessed.

### Task 9: Rename soul_writer.py → soul_blocks.py

**Files:**
- Rename: `apps/server/src/anima_server/services/agent/soul_writer.py` → `soul_blocks.py`
- Modify: 7 import sites

- [ ] **Step 1: Rename the file**

```bash
cd apps/server/src/anima_server/services/agent
git mv soul_writer.py soul_blocks.py
```

- [ ] **Step 2: Update all 7 import sites**

```bash
cd apps/server
grep -rn "from anima_server.services.agent.soul_writer" src/ | grep -v __pycache__
```

Update each to `from anima_server.services.agent.soul_blocks`:
- `api/routes/consciousness.py`
- `api/routes/soul.py`
- `services/agent/consolidation.py`
- `services/agent/forgetting.py`
- `services/agent/inner_monologue.py` (2 sites)
- `services/agent/self_model.py`

- [ ] **Step 3: Run tests to verify nothing broke**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/ -x -q`
Expected: All pass (same baseline as before).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(soul-writer): rename soul_writer.py → soul_blocks.py for orchestrator namespace"
```

---

### Task 10: Add dry_run to store_memory_item

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/memory_store.py:178`
- Test: `apps/server/tests/test_memory_candidates.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_store_memory_item_dry_run_does_not_write(soul_session: Session) -> None:
    """store_memory_item(dry_run=True) returns analysis without writing."""
    from anima_server.services.agent.memory_store import store_memory_item

    result = store_memory_item(
        soul_session, user_id=1, content="Likes cats",
        category="preference", source="extraction",
        dry_run=True,
    )
    assert result.action == "added"  # would be added if not dry_run
    # Verify nothing was written
    from anima_server.models import MemoryItem
    count = soul_session.scalar(select(func.count(MemoryItem.id)))
    assert count == 0
```

(Requires a `soul_session` fixture using SQLCipher/SQLite Base.)

- [ ] **Step 2: Add dry_run parameter to store_memory_item**

In `memory_store.py`, add `dry_run: bool = False` parameter. When True, perform all analysis (dedup, slot matching, similarity) but skip `db.add()` and `db.flush()`. Return `MemoryWriteAnalysis` with the decision.

- [ ] **Step 3: Run test**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/test_memory_candidates.py::test_store_memory_item_dry_run_does_not_write -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/services/agent/memory_store.py apps/server/tests/test_memory_candidates.py
git commit -m "feat(soul-writer): add dry_run to store_memory_item for write-analysis-only mode"
```

---

### Task 11: Create candidate creation helper

**Files:**
- Create: `apps/server/src/anima_server/services/agent/candidate_ops.py`
- Test: `apps/server/tests/test_memory_candidates.py` (append)

- [ ] **Step 1: Write the helper**

```python
# apps/server/src/anima_server/services/agent/candidate_ops.py
"""MemoryCandidate creation and query helpers."""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy import func, or_, and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anima_server.models.runtime_memory import MemoryCandidate

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = frozenset({"fact", "preference", "goal", "relationship"})
_VALID_SOURCES = frozenset({"regex", "llm", "predict_calibrate", "tool", "feedback"})
_VALID_IMPORTANCE_SOURCES = frozenset({
    "regex", "llm", "predict_calibrate", "user_explicit", "correction",
})


def compute_content_hash(
    user_id: int, category: str, importance_source: str, content: str,
) -> str:
    normalized = content.strip().lower()
    return hashlib.sha256(
        f"{user_id}:{category}:{importance_source}:{normalized}".encode()
    ).hexdigest()


def create_memory_candidate(
    runtime_db: Session,
    *,
    user_id: int,
    content: str,
    category: str,
    importance: int = 3,
    importance_source: str = "llm",
    source: str = "llm",
    supersedes_item_id: int | None = None,
    source_message_ids: list[int] | None = None,
    extraction_model: str | None = None,
) -> MemoryCandidate | None:
    """Create a candidate with hash-based dedup. Returns None on duplicate."""
    if category not in _VALID_CATEGORIES:
        category = "fact"
    if source not in _VALID_SOURCES:
        source = "llm"
    if importance_source not in _VALID_IMPORTANCE_SOURCES:
        importance_source = "llm"
    importance = max(1, min(5, importance))

    content_hash = compute_content_hash(user_id, category, importance_source, content)

    candidate = MemoryCandidate(
        user_id=user_id,
        content=content.strip(),
        category=category,
        importance=importance,
        importance_source=importance_source,
        source=source,
        content_hash=content_hash,
        status="extracted",
        supersedes_item_id=supersedes_item_id,
        source_message_ids=source_message_ids,
        extraction_model=extraction_model,
    )
    try:
        with runtime_db.begin_nested():
            runtime_db.add(candidate)
            runtime_db.flush()
        return candidate
    except IntegrityError:
        return None


def count_eligible_candidates(runtime_db: Session, user_id: int, max_retry: int = 3) -> int:
    """Count candidates eligible for promotion."""
    return runtime_db.scalar(
        select(func.count(MemoryCandidate.id)).where(
            MemoryCandidate.user_id == user_id,
            or_(
                MemoryCandidate.status.in_(["extracted", "queued"]),
                and_(
                    MemoryCandidate.status == "failed",
                    MemoryCandidate.retry_count < max_retry,
                ),
            ),
        )
    ) or 0
```

- [ ] **Step 2: Write tests**

```python
def test_create_memory_candidate_dedup(pg_session: Session) -> None:
    from anima_server.services.agent.candidate_ops import create_memory_candidate

    c1 = create_memory_candidate(pg_session, user_id=1, content="likes cats",
                                  category="preference", source="llm")
    assert c1 is not None

    # Duplicate — same user, category, importance_source, content
    c2 = create_memory_candidate(pg_session, user_id=1, content="likes cats",
                                  category="preference", source="llm")
    assert c2 is None  # rejected by unique constraint


def test_correction_and_extraction_not_deduped(pg_session: Session) -> None:
    from anima_server.services.agent.candidate_ops import create_memory_candidate

    c1 = create_memory_candidate(pg_session, user_id=1, content="likes cats",
                                  category="preference", source="llm")
    c2 = create_memory_candidate(pg_session, user_id=1, content="likes cats",
                                  category="preference", importance_source="correction",
                                  source="feedback")
    assert c1 is not None
    assert c2 is not None  # different importance_source → different hash
```

- [ ] **Step 3: Run tests, commit**

```bash
git add apps/server/src/anima_server/services/agent/candidate_ops.py apps/server/tests/test_memory_candidates.py
git commit -m "feat(soul-writer): add candidate creation helper with hash dedup + savepoint"
```

---

### Task 12: Create Soul Writer orchestrator

**Files:**
- Create: `apps/server/src/anima_server/services/agent/soul_writer.py`
- Test: `apps/server/tests/test_soul_writer.py`

This is the core task. The Soul Writer:
1. Acquires per-user asyncio lock
2. Syncs access metadata (always)
3. Loads unconsolidated PendingMemoryOps
4. Loads eligible MemoryCandidates
5. Processes ops first (higher authority), then candidates
6. Per-item transactions with journal entries
7. Idempotent via content hash checks

- [ ] **Step 1: Write the Soul Writer orchestrator**

Create `apps/server/src/anima_server/services/agent/soul_writer.py` with:
- `get_user_soul_writer_lock(user_id)` — per-user asyncio.Lock
- `run_soul_writer(user_id)` — main entry point
- `plan_candidate_promotion(soul_db, candidate, user_id)` — dedup logic
- `reconcile_soul_writer(user_id)` — crash recovery

(Full implementation code — this is the longest single task. The pseudocode is in the spec at the Soul Writer Flow section. Implement it following the spec exactly.)

- [ ] **Step 2: Write comprehensive tests**

Create `apps/server/tests/test_soul_writer.py` with tests for:
- Idempotency (promote, crash, rerun → no duplicate)
- PendingMemoryOp append idempotency
- Per-item error isolation
- Candidate dedup against canonical state
- Authority weighting (user_explicit > llm > regex)
- Correction with valid target → supersede
- Correction with stale target → fallback to promote
- Claims created during promotion
- Suppression on supersession
- Access sync runs even without candidates

- [ ] **Step 3: Run tests, commit**

```bash
git add apps/server/src/anima_server/services/agent/soul_writer.py apps/server/tests/test_soul_writer.py
git commit -m "feat(soul-writer): create Soul Writer orchestrator with per-item transactions"
```

---

### Task 13: Create run_background_extraction

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/consolidation.py`

- [ ] **Step 1: Add run_background_extraction function**

This replaces `run_background_memory_consolidation`. It does the same extraction logic but writes MemoryCandidates to PG instead of memory_items to SQLCipher.

```python
async def run_background_extraction(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
    """Per-turn extraction. Writes ONLY to PG. Never touches SQLCipher."""
    from anima_server.services.agent.candidate_ops import (
        count_eligible_candidates,
        create_memory_candidate,
    )
    from anima_server.config import settings

    try:
        from anima_server.db.runtime import get_runtime_session_factory
        rt_factory = runtime_db_factory or get_runtime_session_factory()
    except RuntimeError:
        return

    with rt_factory() as rt_db:
        # 1. Regex extraction
        extracted = extract_turn_memory(user_message)
        for fact in extracted.facts:
            create_memory_candidate(rt_db, user_id=user_id, content=fact,
                                    category="fact", importance=3,
                                    importance_source="regex", source="regex")
        for pref in extracted.preferences:
            create_memory_candidate(rt_db, user_id=user_id, content=pref,
                                    category="preference", importance=3,
                                    importance_source="regex", source="regex")

        # 2. LLM extraction
        if settings.agent_provider != "scaffold":
            llm_result = await extract_memories_via_llm(
                user_message=user_message,
                assistant_response=assistant_response,
            )
            for item in llm_result.memories:
                create_memory_candidate(
                    rt_db, user_id=user_id,
                    content=item.get("content", ""),
                    category=item.get("category", "fact"),
                    importance=item.get("importance", 3),
                    importance_source="llm", source="llm",
                )

        rt_db.commit()

        # 3. Threshold check
        count = count_eligible_candidates(rt_db, user_id=user_id)
        if count >= settings.soul_writer_candidate_threshold:
            from anima_server.services.agent.soul_writer import run_soul_writer
            import asyncio
            asyncio.create_task(run_soul_writer(user_id))
```

- [ ] **Step 2: Rewire schedule_background_memory_consolidation**

Replace the body of `schedule_background_memory_consolidation` to call `run_background_extraction` instead of `run_background_memory_consolidation` / `run_sleeptime_agents`. Remove `bump_turn_counter` and `should_run_sleeptime` calls.

- [ ] **Step 3: Run tests, commit**

```bash
git add apps/server/src/anima_server/services/agent/consolidation.py
git commit -m "feat(soul-writer): replace per-turn soul writes with PG-only background extraction"
```

---

### Task 14: Wire Soul Writer triggers

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py` (pre-turn check + compaction trigger)
- Modify: `apps/server/src/anima_server/services/agent/reflection.py` (inactivity trigger)
- Modify: `apps/server/src/anima_server/main.py` (shutdown trigger)
- Modify: `apps/server/src/anima_server/services/agent/sleep_agent.py` (remove SLEEPTIME_FREQUENCY)

- [ ] **Step 1: Add pre-turn check to service.py**

In the turn preparation flow (before prompt assembly), add:

```python
from anima_server.services.agent.soul_writer import run_soul_writer
from anima_server.services.agent.candidate_ops import count_eligible_candidates

# Before build_runtime_memory_blocks:
eligible = count_eligible_candidates(runtime_db, user_id=user_id)
if eligible > 0:
    await run_soul_writer(user_id)
```

- [ ] **Step 2: Add compaction trigger to service.py**

After compaction completes, call Soul Writer:

```python
if compaction_result is not None:
    await run_soul_writer(user_id)
```

- [ ] **Step 3: Add inactivity trigger to reflection.py**

In `run_reflection`, call Soul Writer before the sleeptime orchestrator:

```python
from anima_server.services.agent.soul_writer import run_soul_writer
await run_soul_writer(user_id)
# Then run orchestrator tasks (KG, episodes, etc.)
```

- [ ] **Step 4: Add shutdown trigger to main.py**

In the lifespan shutdown handler:

```python
# Before drain_background_memory_tasks:
from anima_server.services.agent.soul_writer import run_soul_writer
# Promote pending candidates for active users
# (get_active_user_ids: query RuntimeThread for recent activity)
```

- [ ] **Step 5: Remove SLEEPTIME_FREQUENCY from sleep_agent.py**

Remove `SLEEPTIME_FREQUENCY`, `_turn_counters`, `bump_turn_counter`, `should_run_sleeptime`, `_commit_with_retry`.

- [ ] **Step 6: Run full test suite**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/ -x -q`
Expected: All pass. This is the critical integration point.

- [ ] **Step 7: Commit**

```bash
git add apps/server/src/anima_server/services/agent/service.py apps/server/src/anima_server/services/agent/reflection.py apps/server/src/anima_server/main.py apps/server/src/anima_server/services/agent/sleep_agent.py
git commit -m "feat(soul-writer): wire all triggers — pre-turn, inactivity, compaction, shutdown, threshold"
```

---

## Phase 6: Tool Write Redirection + SessionNote Migration

### Task 15: Redirect save_to_memory to MemoryCandidate

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/tools.py:134`
- Modify: `apps/server/src/anima_server/services/agent/session_memory.py:111`

- [ ] **Step 1: Modify save_to_memory to create MemoryCandidate**

Replace `promote_session_note` call with `create_memory_candidate(importance_source="user_explicit", source="tool")`.

- [ ] **Step 2: Modify promote_session_note to create MemoryCandidate**

Instead of calling `add_memory_item`, read the note content and create a MemoryCandidate.

- [ ] **Step 3: Run tests, commit**

---

### Task 16: Split feedback_signals — growth log vs corrections

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/feedback_signals.py`

- [ ] **Step 1: Growth log appends → PendingMemoryOp**

`append_growth_log_entry` calls → create PendingMemoryOp with `target_block="growth_log"`.

- [ ] **Step 2: Memory corrections → MemoryCandidate**

`apply_memory_correction` → create MemoryCandidate with `importance_source="correction"`, `supersedes_item_id=<matched_item_id>`.

- [ ] **Step 3: Run tests, commit**

---

### Task 17: Move SessionNote to PG

**Files:**
- Modify: `apps/server/src/anima_server/models/agent_runtime.py` (change Base → RuntimeBase or move class)
- Create: Alembic migration on both engines
- Modify: `apps/server/src/anima_server/services/agent/session_memory.py`

- [ ] **Step 1: Move SessionNote class to RuntimeBase**
- [ ] **Step 2: Update session_memory.py to use runtime DB session**
- [ ] **Step 3: Create Alembic migrations (drop from SQLCipher, create on PG)**
- [ ] **Step 4: Run tests, commit**

---

### Task 18: Move _promote_runtime_emotional_patterns to Soul Writer

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/consolidation.py`
- Modify: `apps/server/src/anima_server/services/agent/soul_writer.py`

- [ ] **Step 1: Remove _promote_runtime_emotional_patterns calls from consolidation.py**
- [ ] **Step 2: Add emotional pattern promotion to Soul Writer run (after access sync)**
- [ ] **Step 3: Run tests, commit**

---

## Phase 7: Cleanup

### Task 19: Remove dead code paths

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/consolidation.py`
- Modify: `apps/server/src/anima_server/services/agent/sleep_agent.py`

- [ ] **Step 1: Remove old consolidation functions**

Remove or mark as deprecated: `consolidate_turn_memory`, `consolidate_turn_memory_with_llm`, `run_background_memory_consolidation`. Remove `upsert_claim` and `suppress_memory` calls from consolidation (now in Soul Writer).

- [ ] **Step 2: Remove _commit_with_retry from sleep_agent.py**

- [ ] **Step 3: Grep for any remaining dead references**

Run: `cd apps/server && grep -rn "consolidate_turn_memory\|run_background_memory_consolidation\|_commit_with_retry\|SLEEPTIME_FREQUENCY\|bump_turn_counter\|should_run_sleeptime\|add_daily_log" src/ --include="*.py" | grep -v __pycache__`
Expected: Zero results.

- [ ] **Step 4: Run full test suite**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/ -x -q`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(soul-writer): remove dead consolidation code paths"
```

---

### Task 20: Final verification

- [ ] **Step 1: Run full test suite**

Run: `cd apps/server && ANIMA_CORE_REQUIRE_ENCRYPTION=false python -m pytest tests/ -v`
Expected: All pass (minus pre-existing chat test).

- [ ] **Step 2: Verify zero MemoryDailyLog references**

Run: `grep -rn "MemoryDailyLog\|memory_daily_log\|add_daily_log" apps/server/src/ --include="*.py" | grep -v __pycache__ | grep -v alembic`
Expected: Zero results.

- [ ] **Step 3: Verify zero per-turn SQLCipher writes in automated paths**

Run: `grep -rn "soul_db\|SessionLocal\|db\.commit\|db\.flush\|db\.add" apps/server/src/anima_server/services/agent/consolidation.py | grep -v "runtime\|_factory\|candidate\|pending"` — verify no direct soul writes in the extraction path.

- [ ] **Step 4: Commit final state**

```bash
git commit --allow-empty -m "feat(soul-writer): Soul Writer architecture complete — all phases implemented"
```
