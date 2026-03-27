# P3: Self-Model Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Physically separate the self-model into enduring identity (Soul/SQLCipher) and working cognition (Runtime/PostgreSQL), applying the identity filter to every piece of self-model data.

**Architecture:** Nine `self_model_blocks` sections split into three categories. Category A (`soul`, `persona`, `human`, `user_directive`) stays in SQLCipher unchanged. Category B (`identity`, `growth_log`) gets promoted to dedicated soul tables (`identity_blocks`, `growth_log`). Category C (`inner_state`, `working_memory`, `intentions`) moves to PostgreSQL runtime tables (`working_context`, `active_intentions`). Emotional signals move from SQLCipher `emotional_signals` to PostgreSQL `current_emotions`. A new `core_emotional_patterns` soul table stores distilled patterns.

**Tech Stack:** Python, SQLAlchemy, Alembic (SQLCipher), PostgreSQL runtime schema, pytest

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/anima_server/models/soul_consciousness.py` | Soul-tier models: `IdentityBlock`, `GrowthLogEntry`, `CoreEmotionalPattern` |
| `src/anima_server/models/runtime_consciousness.py` | Runtime-tier models: `WorkingContext`, `ActiveIntention`, `CurrentEmotion` |
| `src/anima_server/services/agent/emotional_patterns.py` | `promote_emotional_patterns()` — distills rolling signals into enduring patterns |
| `alembic_core/versions/20260327_0001_p3_self_model_split.py` | Alembic migration: create `identity_blocks`, `growth_log`, `core_emotional_patterns`; migrate data; clean `self_model_blocks` |
| `alembic_runtime/versions/002_p3_consciousness_tables.py` | Runtime migration: create `working_context`, `active_intentions`, `current_emotions` |
| `tests/test_p3_self_model_split.py` | All P3-specific tests |

### Modified Files

| File | Changes |
|------|---------|
| `src/anima_server/models/__init__.py` | Export new soul and runtime models |
| `src/anima_server/models/consciousness.py` | Remove `inner_state`, `working_memory`, `intentions` from section lists; keep `SelfModelBlock` for Category A; keep `EmotionalSignal` in place (leave table, stop using) |
| `src/anima_server/config.py` | Add `agent_emotional_patterns_budget` setting |
| `src/anima_server/services/agent/self_model.py` | Split into soul-reader + runtime-writer functions; new `get_identity_block()`, `set_identity_block()`, `get_working_context()`, `set_working_context()`, `get_active_intentions()`, `set_active_intentions()`, `get_growth_log_entries()`, `append_growth_log_entry_row()` |
| `src/anima_server/services/agent/emotional_intelligence.py` | Retarget all DB ops from `EmotionalSignal` to `CurrentEmotion` (runtime) |
| `src/anima_server/services/agent/intentions.py` | Retarget from `self_model_blocks` to `ActiveIntention` (runtime) |
| `src/anima_server/services/agent/memory_blocks.py` | Dual-session reads for `build_self_model_memory_blocks()` and `build_emotional_context_block()`; add `build_emotional_patterns_block()` |
| `src/anima_server/services/agent/inner_monologue.py` | Quick reflection writes to PG; deep monologue reads both, writes both |
| `src/anima_server/services/agent/consolidation.py` | Add emotional pattern promotion call in sleeptime |
| `src/anima_server/api/routes/consciousness.py` | Dual-session reads for self-model and emotion endpoints |
| `tests/conftest.py` | Ensure runtime tables include new consciousness tables |

---

## Task 1: Soul-Tier Models

**Files:**
- Create: `apps/server/src/anima_server/models/soul_consciousness.py`
- Test: `apps/server/tests/test_p3_self_model_split.py`

- [ ] **Step 1: Write the failing test for IdentityBlock CRUD**

```python
# apps/server/tests/test_p3_self_model_split.py
"""P3: Self-Model Split tests."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from anima_server.db.base import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def soul_db() -> Session:
    """In-memory SQLite session with soul tables."""
    from anima_server.models.user import User

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    # Create a test user
    user = User(id=1, username="test", display_name="Test", hashed_password="x")
    session.add(user)
    session.commit()
    yield session
    session.close()
    engine.dispose()


class TestIdentityBlock:
    def test_create_and_read(self, soul_db: Session):
        from anima_server.models.soul_consciousness import IdentityBlock

        block = IdentityBlock(user_id=1, content="I am a companion.", version=1, updated_by="system")
        soul_db.add(block)
        soul_db.flush()

        loaded = soul_db.get(IdentityBlock, block.id)
        assert loaded is not None
        assert loaded.content == "I am a companion."
        assert loaded.version == 1
        assert loaded.user_id == 1

    def test_unique_per_user(self, soul_db: Session):
        from sqlalchemy.exc import IntegrityError
        from anima_server.models.soul_consciousness import IdentityBlock

        soul_db.add(IdentityBlock(user_id=1, content="first", version=1, updated_by="system"))
        soul_db.flush()
        soul_db.add(IdentityBlock(user_id=1, content="second", version=1, updated_by="system"))
        with pytest.raises(IntegrityError):
            soul_db.flush()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py::TestIdentityBlock -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'anima_server.models.soul_consciousness'`

- [ ] **Step 3: Write IdentityBlock, GrowthLogEntry, CoreEmotionalPattern models**

```python
# apps/server/src/anima_server/models/soul_consciousness.py
"""Soul-tier consciousness models (SQLCipher).

New dedicated tables for data promoted from the generic self_model_blocks:
- IdentityBlock: stable self-narrative (one per user)
- GrowthLogEntry: append-only character development log (many per user)
- CoreEmotionalPattern: enduring emotional tendencies distilled from signals
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.base import Base


class IdentityBlock(Base):
    """Stable self-narrative about the agent's relationship with this user.

    Profile-pattern: full rewrite on update. Version tracks maturity.
    Write governance: automated rewrites blocked below stability threshold.
    """

    __tablename__ = "identity_blocks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GrowthLogEntry(Base):
    """Individual growth log entry — how the AI has evolved.

    Append-only. Deduplicated by word overlap on insert.
    Trimmed to max_entries per user (oldest pruned).
    """

    __tablename__ = "growth_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    entry: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="sleep_time"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CoreEmotionalPattern(Base):
    """Enduring emotional tendency distilled from repeated signals.

    Not momentary — represents patterns like 'tends toward frustration
    under deadline pressure' or 'lights up when discussing creative projects'.
    """

    __tablename__ = "core_emotional_patterns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    dominant_emotion: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_context: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    frequency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    first_observed: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_observed: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py::TestIdentityBlock -v`
Expected: PASS

- [ ] **Step 5: Write and run GrowthLogEntry tests**

Add to `test_p3_self_model_split.py`:

```python
class TestGrowthLogEntry:
    def test_create_and_list(self, soul_db: Session):
        from anima_server.models.soul_consciousness import GrowthLogEntry

        entries = [
            GrowthLogEntry(user_id=1, entry="Learned patience", source="sleep_time"),
            GrowthLogEntry(user_id=1, entry="Adapted tone", source="post_turn"),
        ]
        soul_db.add_all(entries)
        soul_db.flush()

        from sqlalchemy import select
        rows = soul_db.scalars(
            select(GrowthLogEntry)
            .where(GrowthLogEntry.user_id == 1)
            .order_by(GrowthLogEntry.id)
        ).all()
        assert len(rows) == 2
        assert rows[0].entry == "Learned patience"
        assert rows[1].source == "post_turn"

    def test_multiple_entries_per_user(self, soul_db: Session):
        """Unlike IdentityBlock, multiple entries per user are allowed."""
        from anima_server.models.soul_consciousness import GrowthLogEntry

        for i in range(5):
            soul_db.add(GrowthLogEntry(user_id=1, entry=f"Entry {i}", source="sleep_time"))
        soul_db.flush()

        from sqlalchemy import select, func
        count = soul_db.scalar(
            select(func.count()).select_from(GrowthLogEntry).where(GrowthLogEntry.user_id == 1)
        )
        assert count == 5
```

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py::TestGrowthLogEntry -v`

- [ ] **Step 6: Write and run CoreEmotionalPattern tests**

Add to `test_p3_self_model_split.py`:

```python
class TestCoreEmotionalPattern:
    def test_create_and_read(self, soul_db: Session):
        from anima_server.models.soul_consciousness import CoreEmotionalPattern

        pattern = CoreEmotionalPattern(
            user_id=1,
            pattern="Tends toward frustration under deadline pressure",
            dominant_emotion="frustrated",
            trigger_context="work deadlines",
            frequency=6,
            confidence=0.8,
        )
        soul_db.add(pattern)
        soul_db.flush()

        loaded = soul_db.get(CoreEmotionalPattern, pattern.id)
        assert loaded is not None
        assert loaded.dominant_emotion == "frustrated"
        assert loaded.frequency == 6
```

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py::TestCoreEmotionalPattern -v`

- [ ] **Step 7: Commit**

```bash
git add apps/server/src/anima_server/models/soul_consciousness.py apps/server/tests/test_p3_self_model_split.py
git commit -m "feat(p3): add soul-tier consciousness models — IdentityBlock, GrowthLogEntry, CoreEmotionalPattern"
```

---

## Task 2: Runtime-Tier Models

**Files:**
- Create: `apps/server/src/anima_server/models/runtime_consciousness.py`
- Modify: `apps/server/tests/test_p3_self_model_split.py`

- [ ] **Step 1: Write failing tests for runtime models**

Add to `test_p3_self_model_split.py`:

```python
from anima_server.db.runtime_base import RuntimeBase
from sqlalchemy import BigInteger, event
from sqlalchemy.ext.compiler import compiles


@compiles(BigInteger, "sqlite")
def _compile_biginteger_sqlite(type_, compiler, **kw):
    return "INTEGER"


@pytest.fixture()
def runtime_db() -> Session:
    """In-memory SQLite session with runtime consciousness tables."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


class TestWorkingContext:
    def test_create_and_read(self, runtime_db: Session):
        from anima_server.models.runtime_consciousness import WorkingContext

        wc = WorkingContext(
            user_id=1,
            section="inner_state",
            content="Feeling reflective.",
            version=1,
            updated_by="post_turn",
        )
        runtime_db.add(wc)
        runtime_db.flush()

        loaded = runtime_db.get(WorkingContext, wc.id)
        assert loaded is not None
        assert loaded.section == "inner_state"
        assert loaded.content == "Feeling reflective."

    def test_unique_constraint(self, runtime_db: Session):
        """Only one row per (user_id, section)."""
        from sqlalchemy.exc import IntegrityError
        from anima_server.models.runtime_consciousness import WorkingContext

        runtime_db.add(WorkingContext(user_id=1, section="inner_state", content="a"))
        runtime_db.flush()
        runtime_db.add(WorkingContext(user_id=1, section="inner_state", content="b"))
        with pytest.raises(IntegrityError):
            runtime_db.flush()


class TestActiveIntention:
    def test_create_and_read(self, runtime_db: Session):
        from anima_server.models.runtime_consciousness import ActiveIntention

        ai = ActiveIntention(user_id=1, content="Learn their preferences", version=1)
        runtime_db.add(ai)
        runtime_db.flush()

        loaded = runtime_db.get(ActiveIntention, ai.id)
        assert loaded is not None
        assert loaded.content == "Learn their preferences"

    def test_unique_per_user(self, runtime_db: Session):
        from sqlalchemy.exc import IntegrityError
        from anima_server.models.runtime_consciousness import ActiveIntention

        runtime_db.add(ActiveIntention(user_id=1, content="a"))
        runtime_db.flush()
        runtime_db.add(ActiveIntention(user_id=1, content="b"))
        with pytest.raises(IntegrityError):
            runtime_db.flush()


class TestCurrentEmotion:
    def test_create_and_read(self, runtime_db: Session):
        from anima_server.models.runtime_consciousness import CurrentEmotion

        ce = CurrentEmotion(
            user_id=1,
            emotion="excited",
            confidence=0.8,
            evidence_type="linguistic",
            evidence="Used exclamation marks",
            trajectory="stable",
            topic="weekend plans",
        )
        runtime_db.add(ce)
        runtime_db.flush()

        loaded = runtime_db.get(CurrentEmotion, ce.id)
        assert loaded is not None
        assert loaded.emotion == "excited"
        assert loaded.confidence == 0.8

    def test_multiple_per_user(self, runtime_db: Session):
        """Rolling buffer — many signals per user."""
        from anima_server.models.runtime_consciousness import CurrentEmotion

        for e in ["excited", "calm", "curious"]:
            runtime_db.add(CurrentEmotion(user_id=1, emotion=e, confidence=0.6))
        runtime_db.flush()

        from sqlalchemy import select, func
        count = runtime_db.scalar(
            select(func.count()).select_from(CurrentEmotion).where(CurrentEmotion.user_id == 1)
        )
        assert count == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py -k "TestWorkingContext or TestActiveIntention or TestCurrentEmotion" -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write runtime consciousness models**

```python
# apps/server/src/anima_server/models/runtime_consciousness.py
"""Runtime-tier consciousness models (PostgreSQL).

Working cognition that is ephemeral — discarded on machine transfer,
rebuilt from seed values on next startup.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.runtime_base import RuntimeBase

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)


class WorkingContext(RuntimeBase):
    """Temporary per-session cognition — inner state and working memory.

    High write frequency. TTL-prunable. Rebuilt from scratch if runtime
    is discarded (portable story: soul survives, working context does not).
    """

    __tablename__ = "working_context"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "section", name="uq_working_context_user_section"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    section: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "inner_state" | "working_memory"
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )


class ActiveIntention(RuntimeBase):
    """In-flight goals and behavioral rules.

    Stored as structured markdown for human readability and user editability.
    Completed intentions are promoted to the soul growth_log during consolidation.
    """

    __tablename__ = "active_intentions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )


class CurrentEmotion(RuntimeBase):
    """Momentary emotional signal detected from a conversation turn.

    Rolling buffer — oldest signals trimmed beyond buffer_size.
    Consolidation distills repeated patterns into CoreEmotionalPattern (soul).
    """

    __tablename__ = "current_emotions"
    __table_args__ = (
        Index("ix_current_emotions_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    emotion: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    evidence_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="linguistic"
    )
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trajectory: Mapped[str] = mapped_column(
        String(24), nullable=False, default="stable"
    )
    previous_emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    acted_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py -k "TestWorkingContext or TestActiveIntention or TestCurrentEmotion" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/models/runtime_consciousness.py apps/server/tests/test_p3_self_model_split.py
git commit -m "feat(p3): add runtime-tier consciousness models — WorkingContext, ActiveIntention, CurrentEmotion"
```

---

## Task 3: Model Exports + Config + Conftest

**Files:**
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Modify: `apps/server/src/anima_server/config.py`
- Modify: `apps/server/tests/conftest.py`

- [ ] **Step 1: Update model exports**

In `apps/server/src/anima_server/models/__init__.py`, add imports:

```python
from anima_server.models.runtime_consciousness import (
    ActiveIntention,
    CurrentEmotion,
    WorkingContext,
)
from anima_server.models.soul_consciousness import (
    CoreEmotionalPattern,
    GrowthLogEntry,
    IdentityBlock,
)
```

And add to `__all__`:

```python
    "ActiveIntention",
    "CoreEmotionalPattern",
    "CurrentEmotion",
    "GrowthLogEntry",
    "IdentityBlock",
    "WorkingContext",
```

- [ ] **Step 2: Add config setting**

In `apps/server/src/anima_server/config.py`, add after `agent_emotional_confidence_threshold`:

```python
    agent_emotional_patterns_budget: int = 400
```

- [ ] **Step 3: Update conftest to register runtime consciousness tables**

In `apps/server/tests/conftest.py`, add an import after the existing runtime model import (line 20):

```python
from anima_server.models import runtime_consciousness as _runtime_consciousness_models  # noqa: F401
```

This ensures `RuntimeBase.metadata.create_all(engine)` creates the new runtime tables.

- [ ] **Step 4: Run existing test suite to check for regressions**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All existing tests pass (no regressions from adding new models)

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/models/__init__.py apps/server/src/anima_server/config.py apps/server/tests/conftest.py
git commit -m "chore(p3): export new models, add emotional_patterns_budget config, register runtime tables in conftest"
```

---

## Task 4: Alembic Migrations

**Files:**
- Create: `apps/server/alembic_core/versions/20260327_0001_p3_self_model_split.py`
- Create: `apps/server/alembic_runtime/versions/002_p3_consciousness_tables.py`

- [ ] **Step 1: Write soul (SQLCipher) Alembic migration**

```python
# apps/server/alembic_core/versions/20260327_0001_p3_self_model_split.py
"""P3: Self-model split — create identity_blocks, growth_log, core_emotional_patterns tables.

Migrates identity and growth_log sections from self_model_blocks into dedicated tables.
Removes inner_state, working_memory, and intentions from self_model_blocks (moved to runtime PG).

Revision ID: 20260327_0001
Revises: 20260324_0001
Create Date: 2026-03-27
"""
import sqlalchemy as sa
from alembic import op

revision = "20260327_0001"
down_revision = "20260324_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- identity_blocks ---
    op.create_table(
        "identity_blocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_by", sa.String(32), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_identity_blocks_user_id"),
    )

    # --- growth_log ---
    op.create_table(
        "growth_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="sleep_time"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # --- core_emotional_patterns ---
    op.create_table(
        "core_emotional_patterns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("dominant_emotion", sa.String(32), nullable=False),
        sa.Column("trigger_context", sa.Text(), nullable=False, server_default=""),
        sa.Column("frequency", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "first_observed",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_observed",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # --- Migrate identity rows ---
    op.execute("""
        INSERT INTO identity_blocks (user_id, content, version, updated_by, created_at, updated_at)
        SELECT user_id, content, version, updated_by, created_at, updated_at
        FROM self_model_blocks
        WHERE section = 'identity'
    """)

    # --- Delete migrated/moved sections from self_model_blocks ---
    op.execute("""
        DELETE FROM self_model_blocks
        WHERE section IN ('identity', 'growth_log', 'inner_state', 'working_memory', 'intentions')
    """)

    # Note: growth_log blob splitting into individual GrowthLogEntry rows
    # is handled by the application code in ensure_user_database(), not here,
    # because the splitting logic requires parsing markdown which is
    # better done in Python than SQL.


def downgrade() -> None:
    # Move identity back to self_model_blocks
    op.execute("""
        INSERT INTO self_model_blocks (user_id, section, content, version, updated_by, created_at, updated_at)
        SELECT user_id, 'identity', content, version, updated_by, created_at, updated_at
        FROM identity_blocks
    """)
    op.drop_table("core_emotional_patterns")
    op.drop_table("growth_log")
    op.drop_table("identity_blocks")
```

- [ ] **Step 2: Write runtime (PostgreSQL) Alembic migration**

```python
# apps/server/alembic_runtime/versions/002_p3_consciousness_tables.py
"""P3: Add consciousness tables to runtime.

Creates working_context, active_intentions, current_emotions tables.

Revision ID: 002
Revises: 001
Create Date: 2026-03-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMPTZ

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- working_context ---
    op.create_table(
        "working_context",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("section", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("updated_by", sa.String(32), nullable=False, server_default="system"),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "section", name="uq_working_context_user_section"),
    )

    # --- active_intentions ---
    op.create_table(
        "active_intentions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("updated_by", sa.String(32), nullable=False, server_default="system"),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )

    # --- current_emotions ---
    op.create_table(
        "current_emotions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("thread_id", sa.BigInteger, nullable=True),
        sa.Column("emotion", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default=sa.text("0.5")),
        sa.Column("evidence_type", sa.String(24), nullable=False, server_default="linguistic"),
        sa.Column("evidence", sa.Text, nullable=False, server_default=""),
        sa.Column("trajectory", sa.String(24), nullable=False, server_default="stable"),
        sa.Column("previous_emotion", sa.String(32), nullable=True),
        sa.Column("topic", sa.String(255), nullable=False, server_default=""),
        sa.Column("acted_on", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_current_emotions_user_created",
        "current_emotions",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("current_emotions")
    op.drop_table("active_intentions")
    op.drop_table("working_context")
```

- [ ] **Step 3: Verify migration files are syntactically valid**

Run: `cd apps/server && python -c "import alembic_core.versions.\"20260327_0001_p3_self_model_split\" as m; print('OK')" 2>/dev/null || python -c "exec(open('alembic_core/versions/20260327_0001_p3_self_model_split.py').read()); print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add apps/server/alembic_core/versions/20260327_0001_p3_self_model_split.py apps/server/alembic_runtime/versions/002_p3_consciousness_tables.py
git commit -m "feat(p3): add Alembic migrations for soul and runtime consciousness tables"
```

---

## Task 5: Refactor self_model.py — Soul Reader + Runtime Writer

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/self_model.py`
- Modify: `apps/server/tests/test_p3_self_model_split.py`

This is the core refactor. The existing `self_model.py` functions are split:
- Category A sections (`soul`, `persona`, `human`, `user_directive`) — unchanged, still use `SelfModelBlock`
- `identity` — reads/writes `IdentityBlock` (soul)
- `growth_log` — reads/writes `GrowthLogEntry` rows (soul)
- `inner_state`, `working_memory` — reads/writes `WorkingContext` (runtime)
- `intentions` — reads/writes `ActiveIntention` (runtime)

- [ ] **Step 1: Write tests for new identity block functions**

Add to `test_p3_self_model_split.py`:

```python
class TestSelfModelIdentityBlock:
    def test_get_identity_block_returns_none_when_missing(self, soul_db: Session):
        from anima_server.services.agent.self_model import get_identity_block

        result = get_identity_block(soul_db, user_id=1)
        assert result is None

    def test_set_and_get_identity_block(self, soul_db: Session):
        from anima_server.services.agent.self_model import (
            get_identity_block,
            set_identity_block,
        )

        set_identity_block(soul_db, user_id=1, content="I am a companion.", updated_by="system")
        soul_db.flush()

        block = get_identity_block(soul_db, user_id=1)
        assert block is not None
        assert block.content == "I am a companion."
        assert block.version == 1

    def test_set_identity_block_bumps_version(self, soul_db: Session):
        from anima_server.services.agent.self_model import (
            get_identity_block,
            set_identity_block,
        )

        set_identity_block(soul_db, user_id=1, content="v1", updated_by="system")
        soul_db.flush()
        set_identity_block(soul_db, user_id=1, content="v2", updated_by="sleep_time")
        soul_db.flush()

        block = get_identity_block(soul_db, user_id=1)
        assert block.content == "v2"
        assert block.version == 2

    def test_identity_stability_threshold(self, soul_db: Session):
        """Automated rewrites blocked when version < threshold."""
        from anima_server.services.agent.self_model import (
            get_identity_block,
            set_identity_block,
        )

        set_identity_block(soul_db, user_id=1, content="original", updated_by="system")
        soul_db.flush()

        # Automated writer tries to overwrite with very different content
        set_identity_block(
            soul_db, user_id=1, content="completely different text here", updated_by="sleep_time"
        )
        soul_db.flush()

        block = get_identity_block(soul_db, user_id=1)
        # Should still be original because version < threshold and overlap < 0.5
        assert block.content == "original"
```

- [ ] **Step 2: Write tests for growth log entry functions**

```python
class TestSelfModelGrowthLog:
    def test_append_growth_log_entry_row(self, soul_db: Session):
        from anima_server.services.agent.self_model import (
            append_growth_log_entry_row,
            get_growth_log_entries,
        )

        append_growth_log_entry_row(soul_db, user_id=1, entry="Learned patience")
        soul_db.flush()

        entries = get_growth_log_entries(soul_db, user_id=1)
        assert len(entries) == 1
        assert entries[0].entry == "Learned patience"

    def test_dedup_by_word_overlap(self, soul_db: Session):
        from anima_server.services.agent.self_model import (
            append_growth_log_entry_row,
            get_growth_log_entries,
        )

        append_growth_log_entry_row(soul_db, user_id=1, entry="Learned to be patient with the user")
        soul_db.flush()
        result = append_growth_log_entry_row(
            soul_db, user_id=1, entry="Learned to be patient with the user today"
        )
        soul_db.flush()

        assert result is None  # duplicate rejected
        entries = get_growth_log_entries(soul_db, user_id=1)
        assert len(entries) == 1

    def test_trim_to_max_entries(self, soul_db: Session):
        from anima_server.services.agent.self_model import (
            append_growth_log_entry_row,
            get_growth_log_entries,
        )

        for i in range(25):
            append_growth_log_entry_row(
                soul_db, user_id=1, entry=f"Unique entry number {i} with distinctive words {i*100}"
            )
            soul_db.flush()

        entries = get_growth_log_entries(soul_db, user_id=1)
        assert len(entries) <= 20
```

- [ ] **Step 3: Write tests for working context functions**

```python
class TestSelfModelWorkingContext:
    def test_get_working_context_empty(self, runtime_db: Session):
        from anima_server.services.agent.self_model import get_working_context

        result = get_working_context(runtime_db, user_id=1)
        assert result == {}

    def test_set_and_get_working_context(self, runtime_db: Session):
        from anima_server.services.agent.self_model import (
            get_working_context,
            set_working_context,
        )

        set_working_context(
            runtime_db, user_id=1, section="inner_state", content="Feeling curious."
        )
        runtime_db.flush()

        result = get_working_context(runtime_db, user_id=1)
        assert "inner_state" in result
        assert result["inner_state"].content == "Feeling curious."

    def test_get_active_intentions(self, runtime_db: Session):
        from anima_server.services.agent.self_model import (
            get_active_intentions,
            set_active_intentions,
        )

        set_active_intentions(runtime_db, user_id=1, content="Learn preferences")
        runtime_db.flush()

        result = get_active_intentions(runtime_db, user_id=1)
        assert result is not None
        assert result.content == "Learn preferences"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py -k "TestSelfModel" -v`
Expected: FAIL — missing functions

- [ ] **Step 5: Implement the new functions in self_model.py**

Add the following functions to `apps/server/src/anima_server/services/agent/self_model.py`:

```python
# --- New imports at top ---
from anima_server.models.soul_consciousness import (
    CoreEmotionalPattern,
    GrowthLogEntry,
    IdentityBlock,
)

# ── Identity Block (Soul) ────────────────────────────────────────────

def get_identity_block(
    db: Session,
    *,
    user_id: int,
) -> IdentityBlock | None:
    """Get the identity block for a user from the soul store."""
    return db.scalar(
        select(IdentityBlock).where(IdentityBlock.user_id == user_id)
    )


def set_identity_block(
    db: Session,
    *,
    user_id: int,
    content: str,
    updated_by: str = "system",
) -> IdentityBlock:
    """Create or update the identity block. Bumps version on update.

    Write governance: automated writers cannot fully rewrite until
    version >= _IDENTITY_STABILITY_THRESHOLD.
    """
    existing = get_identity_block(db, user_id=user_id)

    if (
        existing is not None
        and existing.version < _IDENTITY_STABILITY_THRESHOLD
        and updated_by not in _TRUSTED_WRITERS
        and existing.content.strip()
    ):
        existing_words = set(existing.content.lower().split())
        new_words = set(content.lower().split())
        if existing_words and new_words:
            overlap = len(existing_words & new_words) / max(
                len(existing_words), len(new_words)
            )
            if overlap < 0.5:
                logger.info(
                    "Blocked identity rewrite by %s (version %d < %d, overlap %.2f).",
                    updated_by,
                    existing.version,
                    _IDENTITY_STABILITY_THRESHOLD,
                    overlap,
                )
                append_growth_log_entry_row(
                    db,
                    user_id=user_id,
                    entry=f"Identity update proposed by {updated_by} (blocked): {content[:200]}",
                )
                return existing

    if existing is not None:
        existing.content = content
        existing.version += 1
        existing.updated_by = updated_by
        existing.updated_at = datetime.now(UTC)
        db.flush()
        return existing

    block = IdentityBlock(
        user_id=user_id,
        content=content,
        version=1,
        updated_by=updated_by,
    )
    db.add(block)
    db.flush()
    return block


# ── Growth Log (Soul) ────────────────────────────────────────────────

def get_growth_log_entries(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
) -> list[GrowthLogEntry]:
    """Get growth log entries, most recent first."""
    return list(
        db.scalars(
            select(GrowthLogEntry)
            .where(GrowthLogEntry.user_id == user_id)
            .order_by(GrowthLogEntry.created_at.desc())
            .limit(limit)
        ).all()
    )


def get_growth_log_text(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
) -> str:
    """Render growth log entries as formatted text for the system prompt."""
    entries = get_growth_log_entries(db, user_id=user_id, limit=limit)
    if not entries:
        return ""
    lines = []
    for entry in reversed(entries):
        date_str = entry.created_at.strftime("%Y-%m-%d") if entry.created_at else "unknown"
        lines.append(f"### {date_str} — {entry.entry}")
    return "\n\n".join(lines)


def append_growth_log_entry_row(
    db: Session,
    *,
    user_id: int,
    entry: str,
    source: str = "sleep_time",
    max_entries: int = 20,
) -> GrowthLogEntry | None:
    """Append a growth log entry. Deduplicates by word overlap. Trims to max_entries.

    Returns None if the entry is a duplicate.
    """
    if not entry or not entry.strip():
        return None

    # Dedup check against existing entries
    existing = get_growth_log_entries(db, user_id=user_id, limit=max_entries)
    for ex in existing:
        if _is_duplicate_growth_entry_text(ex.entry, entry):
            return None

    row = GrowthLogEntry(
        user_id=user_id,
        entry=entry.strip(),
        source=source,
    )
    db.add(row)
    db.flush()

    # Trim oldest entries beyond max
    from sqlalchemy import func as sa_func

    total = db.scalar(
        select(sa_func.count())
        .select_from(GrowthLogEntry)
        .where(GrowthLogEntry.user_id == user_id)
    ) or 0

    if total > max_entries:
        # Delete oldest entries
        cutoff_id = db.scalar(
            select(GrowthLogEntry.id)
            .where(GrowthLogEntry.user_id == user_id)
            .order_by(GrowthLogEntry.created_at.desc())
            .offset(max_entries)
            .limit(1)
        )
        if cutoff_id is not None:
            from sqlalchemy import delete as sa_delete

            db.execute(
                sa_delete(GrowthLogEntry).where(
                    GrowthLogEntry.user_id == user_id,
                    GrowthLogEntry.id <= cutoff_id,
                )
            )
            db.flush()

    return row


def _is_duplicate_growth_entry_text(existing_entry: str, new_entry: str) -> bool:
    """Check if a new growth log entry is substantially similar to an existing one."""
    new_words = set(new_entry.lower().split())
    if len(new_words) < 3:
        return new_entry.lower().strip() in existing_entry.lower()
    existing_words = set(existing_entry.lower().split())
    if not existing_words:
        return False
    overlap = len(new_words & existing_words) / max(len(new_words), len(existing_words))
    return overlap > 0.7


# ── Working Context (Runtime/PG) ─────────────────────────────────────

def get_working_context(
    pg_db: Session,
    *,
    user_id: int,
) -> dict[str, "WorkingContext"]:
    """Get all working context rows for a user, keyed by section."""
    from anima_server.models.runtime_consciousness import WorkingContext

    rows = pg_db.scalars(
        select(WorkingContext).where(WorkingContext.user_id == user_id)
    ).all()
    return {r.section: r for r in rows}


def set_working_context(
    pg_db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str = "system",
) -> "WorkingContext":
    """Create or update a working context section in runtime PG."""
    from anima_server.models.runtime_consciousness import WorkingContext

    existing = pg_db.scalar(
        select(WorkingContext).where(
            WorkingContext.user_id == user_id,
            WorkingContext.section == section,
        )
    )

    if existing is not None:
        existing.content = content
        existing.version += 1
        existing.updated_by = updated_by
        existing.updated_at = datetime.now(UTC)
        pg_db.flush()
        return existing

    row = WorkingContext(
        user_id=user_id,
        section=section,
        content=content,
        version=1,
        updated_by=updated_by,
    )
    pg_db.add(row)
    pg_db.flush()
    return row


# ── Active Intentions (Runtime/PG) ───────────────────────────────────

def get_active_intentions(
    pg_db: Session,
    *,
    user_id: int,
) -> "ActiveIntention | None":
    """Get the active intentions block for a user from runtime PG."""
    from anima_server.models.runtime_consciousness import ActiveIntention

    return pg_db.scalar(
        select(ActiveIntention).where(ActiveIntention.user_id == user_id)
    )


def set_active_intentions(
    pg_db: Session,
    *,
    user_id: int,
    content: str,
    updated_by: str = "system",
) -> "ActiveIntention":
    """Create or update active intentions in runtime PG."""
    from anima_server.models.runtime_consciousness import ActiveIntention

    existing = get_active_intentions(pg_db, user_id=user_id)

    if existing is not None:
        existing.content = content
        existing.version += 1
        existing.updated_by = updated_by
        existing.updated_at = datetime.now(UTC)
        pg_db.flush()
        return existing

    row = ActiveIntention(
        user_id=user_id,
        content=content,
        version=1,
        updated_by=updated_by,
    )
    pg_db.add(row)
    pg_db.flush()
    return row
```

Also update `seed_self_model()` to seed the new tables and remove references to moved sections. Update `ensure_self_model_exists()` to check both stores. Keep `append_growth_log_entry()` as a backward-compat wrapper that calls `append_growth_log_entry_row()`. Update `expire_working_memory_items()` to operate on the runtime `WorkingContext`.

Update `ALL_SECTIONS` and `SECTIONS` to reflect what remains in `self_model_blocks`:

```python
# Sections that remain in self_model_blocks (Category A)
SOUL_SECTIONS = ("soul", "persona", "human", "user_directive")

# Sections that moved to dedicated soul tables (Category B)
IDENTITY_SECTIONS = ("identity", "growth_log")

# Sections that moved to runtime (Category C)
RUNTIME_SECTIONS = ("inner_state", "working_memory", "intentions")

# ALL_SECTIONS kept for backward compat (API validation)
ALL_SECTIONS = SOUL_SECTIONS + IDENTITY_SECTIONS + RUNTIME_SECTIONS
```

- [ ] **Step 6: Run P3 tests**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py -v`
Expected: PASS

- [ ] **Step 7: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add apps/server/src/anima_server/services/agent/self_model.py apps/server/tests/test_p3_self_model_split.py
git commit -m "feat(p3): split self_model.py into soul-reader + runtime-writer pattern"
```

---

## Task 6: Retarget emotional_intelligence.py to Runtime

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/emotional_intelligence.py`
- Modify: `apps/server/tests/test_p3_self_model_split.py`

- [ ] **Step 1: Write tests for runtime-backed emotional signals**

Add to `test_p3_self_model_split.py`:

```python
class TestEmotionalIntelligenceRuntime:
    def test_record_signal_to_runtime(self, runtime_db: Session):
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal

        signal = record_emotional_signal(
            runtime_db,
            user_id=1,
            emotion="excited",
            confidence=0.8,
            evidence="Used exclamation marks",
            topic="weekend",
        )
        assert signal is not None
        assert signal.emotion == "excited"

    def test_get_recent_signals_from_runtime(self, runtime_db: Session):
        from anima_server.services.agent.emotional_intelligence import (
            get_recent_signals,
            record_emotional_signal,
        )

        record_emotional_signal(runtime_db, user_id=1, emotion="calm", confidence=0.7)
        record_emotional_signal(runtime_db, user_id=1, emotion="curious", confidence=0.6)
        runtime_db.flush()

        signals = get_recent_signals(runtime_db, user_id=1)
        assert len(signals) == 2

    def test_synthesize_from_runtime(self, runtime_db: Session):
        from anima_server.services.agent.emotional_intelligence import (
            record_emotional_signal,
            synthesize_emotional_context,
        )

        record_emotional_signal(
            runtime_db, user_id=1, emotion="frustrated", confidence=0.8, evidence="Short replies"
        )
        runtime_db.flush()

        context = synthesize_emotional_context(runtime_db, user_id=1)
        assert "frustrated" in context

    def test_trim_buffer(self, runtime_db: Session):
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal

        # Record more than buffer size
        for i in range(25):
            record_emotional_signal(
                runtime_db, user_id=1, emotion="calm", confidence=0.5
            )
            runtime_db.flush()

        from sqlalchemy import select, func
        from anima_server.models.runtime_consciousness import CurrentEmotion

        count = runtime_db.scalar(
            select(func.count()).select_from(CurrentEmotion).where(CurrentEmotion.user_id == 1)
        )
        assert count <= 20  # buffer_size default
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py::TestEmotionalIntelligenceRuntime -v`
Expected: FAIL

- [ ] **Step 3: Retarget emotional_intelligence.py**

Replace all references to `EmotionalSignal` with `CurrentEmotion` from `anima_server.models.runtime_consciousness`. The function signatures stay identical — only the model class and table/field names in `ef()`/`df()` calls change.

Key changes:
- Import `CurrentEmotion` instead of `EmotionalSignal`
- All `EmotionalSignal` → `CurrentEmotion` in queries
- `ef(user_id, ..., table="emotional_signals", ...)` → content stored as plaintext (runtime PG has no SQLCipher encryption; field-level encryption will be added in a separate concern)
- `df(user_id, ..., table="emotional_signals", ...)` → read as plaintext

Note: The PRD says runtime content that may contain sensitive data should use field-level `ef()`/`df()`. However, the P2 precedent established that runtime messages store content as plaintext. For consistency, P3 follows the same pattern. If field-level encryption is needed for runtime, it can be added later.

- [ ] **Step 4: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py::TestEmotionalIntelligenceRuntime -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass (update any tests that import/mock `EmotionalSignal`)

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/services/agent/emotional_intelligence.py apps/server/tests/test_p3_self_model_split.py
git commit -m "feat(p3): retarget emotional_intelligence.py to runtime CurrentEmotion"
```

---

## Task 7: Retarget intentions.py to Runtime

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/intentions.py`
- Modify: `apps/server/tests/test_p3_self_model_split.py`

- [ ] **Step 1: Write tests for runtime-backed intentions**

```python
class TestIntentionsRuntime:
    def test_add_intention_to_runtime(self, runtime_db: Session):
        from anima_server.services.agent.self_model import set_active_intentions
        from anima_server.services.agent.intentions import add_intention

        # Seed intentions
        set_active_intentions(runtime_db, user_id=1, content="# Active Intentions\n\n## Ongoing")
        runtime_db.flush()

        content = add_intention(
            runtime_db,
            user_id=1,
            title="Learn communication style",
            evidence="New relationship",
        )
        assert "Learn communication style" in content

    def test_complete_intention_in_runtime(self, runtime_db: Session):
        from anima_server.services.agent.self_model import set_active_intentions
        from anima_server.services.agent.intentions import add_intention, complete_intention

        set_active_intentions(runtime_db, user_id=1, content="# Active Intentions\n\n## Ongoing")
        runtime_db.flush()

        add_intention(runtime_db, user_id=1, title="Test goal")
        runtime_db.flush()

        result = complete_intention(runtime_db, user_id=1, title="Test goal")
        assert result is True
```

- [ ] **Step 2: Run to verify failure, then implement**

Retarget `intentions.py` to use `get_active_intentions()` and `set_active_intentions()` from `self_model.py` instead of `get_self_model_block(section="intentions")` / `set_self_model_block(section="intentions")`.

Key changes:
- `get_self_model_block(db, user_id=user_id, section="intentions")` → `get_active_intentions(db, user_id=user_id)`
- `set_self_model_block(db, ..., section="intentions", ...)` → `set_active_intentions(db, ..., content=content, ...)`
- Block content access: `block.content` stays the same (both `SelfModelBlock` and `ActiveIntention` have `.content`)

- [ ] **Step 3: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py::TestIntentionsRuntime -v`
Expected: PASS

- [ ] **Step 4: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/intentions.py apps/server/tests/test_p3_self_model_split.py
git commit -m "feat(p3): retarget intentions.py to runtime ActiveIntention"
```

---

## Task 8: Refactor memory_blocks.py for Dual-Session Reads

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/memory_blocks.py`
- Modify: `apps/server/tests/test_p3_self_model_split.py`

- [ ] **Step 1: Write test for dual-session self-model blocks**

```python
class TestMemoryBlocksDualRead:
    def test_build_self_model_memory_blocks_dual_store(self, soul_db: Session, runtime_db: Session):
        """build_self_model_memory_blocks reads identity from soul and working context from runtime."""
        from anima_server.services.agent.self_model import (
            set_identity_block,
            set_working_context,
            set_active_intentions,
        )
        from anima_server.services.agent.memory_blocks import build_self_model_memory_blocks

        set_identity_block(soul_db, user_id=1, content="I am a caring companion.", updated_by="system")
        soul_db.flush()

        set_working_context(runtime_db, user_id=1, section="inner_state", content="Feeling curious.")
        set_working_context(runtime_db, user_id=1, section="working_memory", content="- Remember to ask about project")
        set_active_intentions(runtime_db, user_id=1, content="# Intentions\n- Learn preferences")
        runtime_db.flush()

        blocks = build_self_model_memory_blocks(soul_db, pg_db=runtime_db, user_id=1)

        labels = {b.label for b in blocks}
        assert "self_identity" in labels
        assert "self_inner_state" in labels
        assert "self_working_memory" in labels
        assert "self_intentions" in labels

    def test_build_emotional_context_from_runtime(self, runtime_db: Session):
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal
        from anima_server.services.agent.memory_blocks import build_emotional_context_block

        record_emotional_signal(runtime_db, user_id=1, emotion="curious", confidence=0.7)
        runtime_db.flush()

        block = build_emotional_context_block(runtime_db, user_id=1)
        assert block is not None
        assert "curious" in block.value

    def test_build_emotional_patterns_block(self, soul_db: Session):
        from anima_server.models.soul_consciousness import CoreEmotionalPattern
        from anima_server.services.agent.memory_blocks import build_emotional_patterns_block

        soul_db.add(CoreEmotionalPattern(
            user_id=1,
            pattern="Gets frustrated under deadline pressure",
            dominant_emotion="frustrated",
            trigger_context="work deadlines",
            frequency=6,
            confidence=0.8,
        ))
        soul_db.flush()

        block = build_emotional_patterns_block(soul_db, user_id=1)
        assert block is not None
        assert "frustrated" in block.value
```

- [ ] **Step 2: Implement changes**

Update `build_self_model_memory_blocks()` to accept `pg_db` parameter and read from both stores:

```python
def build_self_model_memory_blocks(
    db: Session,
    *,
    user_id: int,
    pg_db: Session | None = None,
) -> list[MemoryBlock]:
```

- Identity: read from `get_identity_block(db, user_id=user_id)`
- Growth log: read from `get_growth_log_text(db, user_id=user_id)`
- Inner state: read from `get_working_context(pg_db, user_id=user_id)["inner_state"]` if `pg_db` provided
- Working memory: read from `get_working_context(pg_db, user_id=user_id)["working_memory"]` if `pg_db` provided
- Intentions: read from `get_active_intentions(pg_db, user_id=user_id)` if `pg_db` provided

Add `build_emotional_patterns_block()`:

```python
def build_emotional_patterns_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    """Build memory block from enduring emotional patterns (soul)."""
    from anima_server.models.soul_consciousness import CoreEmotionalPattern

    patterns = db.scalars(
        select(CoreEmotionalPattern)
        .where(CoreEmotionalPattern.user_id == user_id)
        .order_by(CoreEmotionalPattern.confidence.desc())
        .limit(10)
    ).all()
    if not patterns:
        return None

    lines = []
    for p in patterns:
        lines.append(f"- {p.pattern} ({p.dominant_emotion}, confidence: {p.confidence:.1f})")

    from anima_server.config import settings
    value = "\n".join(lines)
    if len(value) > settings.agent_emotional_patterns_budget:
        value = value[:settings.agent_emotional_patterns_budget]

    return MemoryBlock(
        label="emotional_patterns",
        description="My enduring emotional tendencies — patterns distilled from many conversations.",
        value=value,
    )
```

Update `build_runtime_memory_blocks()` to pass `runtime_db` through to `build_self_model_memory_blocks()`.

- [ ] **Step 3: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py::TestMemoryBlocksDualRead -v`
Expected: PASS

- [ ] **Step 4: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/memory_blocks.py apps/server/tests/test_p3_self_model_split.py
git commit -m "feat(p3): dual-session reads in memory_blocks.py + emotional patterns block"
```

---

## Task 9: Refactor inner_monologue.py

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/inner_monologue.py`

- [ ] **Step 1: Update quick reflection to write to runtime**

In `run_quick_reflection()`:
- Replace `get_self_model_block(db, section="inner_state")` with `get_working_context(pg_db, user_id=user_id)["inner_state"]`
- Replace `set_self_model_block(db, section="inner_state", ...)` with `set_working_context(pg_db, user_id=user_id, section="inner_state", ...)`
- Same for `working_memory`
- The `record_emotional_signal()` call already targets runtime after Task 6
- Need to accept a `runtime_db_factory` parameter or build one internally

- [ ] **Step 2: Update deep monologue to read both, write both**

In `run_deep_monologue()`:
- Phase 1 (Read): read identity from `get_identity_block(db)`, growth log from `get_growth_log_text(db)`, persona from `get_self_model_block(db, section="persona")`, working context from runtime, intentions from runtime, emotional signals from runtime
- Phase 3 (Write): identity/persona/growth_log → soul DB. inner_state/working_memory/intentions → runtime PG

- [ ] **Step 3: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/services/agent/inner_monologue.py
git commit -m "feat(p3): refactor inner_monologue.py for dual-store reads/writes"
```

---

## Task 10: Emotional Patterns Promotion + Consolidation Integration

**Files:**
- Create: `apps/server/src/anima_server/services/agent/emotional_patterns.py`
- Modify: `apps/server/src/anima_server/services/agent/consolidation.py`
- Modify: `apps/server/tests/test_p3_self_model_split.py`

- [ ] **Step 1: Write test for emotional pattern promotion**

```python
class TestEmotionalPatternPromotion:
    def test_promote_from_signals(self, soul_db: Session, runtime_db: Session):
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal
        from anima_server.services.agent.emotional_patterns import promote_emotional_patterns
        from anima_server.models.soul_consciousness import CoreEmotionalPattern

        # Record several frustrated signals
        for _ in range(5):
            record_emotional_signal(
                runtime_db, user_id=1, emotion="frustrated", confidence=0.7,
                evidence="Deadline talk", topic="work",
            )
        runtime_db.flush()

        promoted = promote_emotional_patterns(soul_db=soul_db, pg_db=runtime_db, user_id=1)
        soul_db.flush()

        assert promoted >= 1
        from sqlalchemy import select
        patterns = soul_db.scalars(
            select(CoreEmotionalPattern).where(CoreEmotionalPattern.user_id == 1)
        ).all()
        assert len(patterns) >= 1
        assert any(p.dominant_emotion == "frustrated" for p in patterns)
```

- [ ] **Step 2: Implement promote_emotional_patterns()**

```python
# apps/server/src/anima_server/services/agent/emotional_patterns.py
"""Promote recurring emotional signals into enduring soul patterns."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime_consciousness import CurrentEmotion
from anima_server.models.soul_consciousness import CoreEmotionalPattern

logger = logging.getLogger(__name__)

MIN_SIGNALS_FOR_PATTERN = 3
MIN_CONFIDENCE_FOR_PATTERN = 0.5


def promote_emotional_patterns(
    *,
    soul_db: Session,
    pg_db: Session,
    user_id: int,
) -> int:
    """Analyze recent emotional signals and promote recurring patterns to soul.

    Returns the number of patterns created or updated.
    """
    signals = pg_db.scalars(
        select(CurrentEmotion)
        .where(CurrentEmotion.user_id == user_id)
        .order_by(CurrentEmotion.created_at.desc())
        .limit(50)
    ).all()

    if len(signals) < MIN_SIGNALS_FOR_PATTERN:
        return 0

    # Count emotions weighted by confidence
    emotion_counts: Counter[str] = Counter()
    emotion_evidence: dict[str, list[str]] = {}
    for s in signals:
        if s.confidence >= MIN_CONFIDENCE_FOR_PATTERN:
            emotion_counts[s.emotion] += 1
            if s.emotion not in emotion_evidence:
                emotion_evidence[s.emotion] = []
            if s.topic:
                emotion_evidence[s.emotion].append(s.topic)

    promoted = 0
    now = datetime.now(UTC)

    for emotion, count in emotion_counts.items():
        if count < MIN_SIGNALS_FOR_PATTERN:
            continue

        # Check if pattern already exists
        existing = soul_db.scalar(
            select(CoreEmotionalPattern).where(
                CoreEmotionalPattern.user_id == user_id,
                CoreEmotionalPattern.dominant_emotion == emotion,
            )
        )

        topics = emotion_evidence.get(emotion, [])
        trigger = ", ".join(set(topics[:5])) if topics else ""
        avg_confidence = sum(
            s.confidence for s in signals if s.emotion == emotion
        ) / count

        if existing is not None:
            existing.frequency = count
            existing.confidence = round(avg_confidence, 2)
            existing.last_observed = now
            if trigger:
                existing.trigger_context = trigger
        else:
            pattern_text = f"Tends toward {emotion}"
            if trigger:
                pattern_text += f" when discussing {trigger}"
            soul_db.add(CoreEmotionalPattern(
                user_id=user_id,
                pattern=pattern_text,
                dominant_emotion=emotion,
                trigger_context=trigger,
                frequency=count,
                confidence=round(avg_confidence, 2),
                first_observed=now,
                last_observed=now,
            ))
        promoted += 1

    soul_db.flush()
    return promoted
```

- [ ] **Step 3: Wire into consolidation/sleep agent**

In `consolidation.py` or `sleep_agent.py`, add a call to `promote_emotional_patterns()` during the sleeptime orchestration, after the main consolidation tasks run.

- [ ] **Step 4: Run tests**

Run: `cd apps/server && python -m pytest tests/test_p3_self_model_split.py::TestEmotionalPatternPromotion -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/emotional_patterns.py apps/server/src/anima_server/services/agent/consolidation.py apps/server/tests/test_p3_self_model_split.py
git commit -m "feat(p3): emotional pattern promotion from runtime signals to soul"
```

---

## Task 11: Consciousness API Dual-Session

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/consciousness.py`

- [ ] **Step 1: Update self-model endpoints to read from both stores**

`get_full_self_model`:
- Read Category A from `soul_db` (SQLCipher) using existing `get_all_self_model_blocks()`
- Read identity from `get_identity_block(soul_db)`
- Read growth log from `get_growth_log_text(soul_db)`
- Read working context from `get_working_context(pg_db)`
- Read intentions from `get_active_intentions(pg_db)`
- Assemble into the same response shape

`get_self_model_section`:
- Route to the correct store based on section name

`update_self_model_section`:
- Route writes to the correct store

`get_emotional_state`:
- Read from runtime PG using `CurrentEmotion` model

`get_intentions`:
- Read from runtime PG

Add `Depends(get_runtime_db)` to endpoints that need runtime access.

- [ ] **Step 2: Run existing consciousness tests**

Run: `cd apps/server && python -m pytest tests/ -k "consciousness" -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/api/routes/consciousness.py
git commit -m "feat(p3): consciousness API reads from dual stores"
```

---

## Task 12: Update Callers + Backward Compatibility

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/feedback_signals.py`
- Modify: `apps/server/src/anima_server/models/consciousness.py`
- Various callers that reference moved sections

- [ ] **Step 1: Update feedback_signals.py**

`record_feedback_signals()` calls `append_growth_log_entry()`. Keep this working via the backward-compat wrapper in `self_model.py` that delegates to `append_growth_log_entry_row()`.

No changes needed if the wrapper is in place. Verify by running:

Run: `cd apps/server && python -m pytest tests/ -k "feedback" -v`

- [ ] **Step 2: Update consciousness.py model**

Add `needs_regeneration` column handling to consciousness model. Remove moved sections from documentation comments. Keep `EmotionalSignal` model in place but unused (will be dropped in a future migration).

- [ ] **Step 3: Grep for any remaining references to moved sections**

Search for `section="inner_state"`, `section="working_memory"`, `section="intentions"`, `section="identity"`, `section="growth_log"` accessing `SelfModelBlock` directly (not through the new functions). Fix any found.

Run: `cd apps/server && grep -rn 'section.*=.*"inner_state"\|section.*=.*"working_memory"\|section.*=.*"intentions"' src/anima_server/ --include="*.py" | grep -v self_model.py | grep -v __pycache__`

- [ ] **Step 4: Run full test suite — final regression check**

Run: `cd apps/server && python -m pytest tests/ -x -q --timeout=60`
Expected: All 846+ tests pass

- [ ] **Step 5: Commit**

```bash
git add -u apps/server/src/ apps/server/tests/
git commit -m "feat(p3): update remaining callers for self-model split"
```

---

## Task 13: Final Verification

- [ ] **Step 1: Run the complete test suite**

Run: `cd apps/server && python -m pytest tests/ -v --timeout=120`
Expected: All tests pass, no regressions

- [ ] **Step 2: Verify acceptance criteria**

Manually verify:
1. Soul DB has: `identity_blocks`, `growth_log`, `core_emotional_patterns` tables + unchanged `self_model_blocks` (with only `soul`, `persona`, `human`, `user_directive` sections)
2. Runtime PG has: `working_context`, `active_intentions`, `current_emotions` tables
3. `build_runtime_memory_blocks()` produces same MemoryBlock labels as before
4. No `inner_state`, `working_memory`, or `intentions` rows in `self_model_blocks`

- [ ] **Step 3: Squash fixup commits if any, create final commit**

All work should already be committed. Verify with `git log --oneline`.
