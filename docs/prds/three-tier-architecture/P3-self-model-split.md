---
title: "Phase 3: Self-Model Split"
description: Split the unified self_model_blocks table into enduring identity (Soul/SQLCipher) and working cognition (Runtime/PostgreSQL)
category: prd
version: "1.0"
---

# Phase 3: Self-Model Split

**Version**: 1.0
**Date**: 2026-03-26
**Status**: Draft
**Priority**: P0
**Depends on**: P2 (Runtime Messages) -- PostgreSQL runtime engine must be operational
**Blocks**: P4 (Write Boundary) -- consolidation gateway reads from the split stores

---

## 1. Overview

AnimaOS currently stores all self-model data in a single `self_model_blocks` table in SQLCipher. Every section -- identity, persona, inner_state, working_memory, growth_log, intentions -- lives side by side, regardless of whether it represents enduring identity or transient cognitive state.

The three-tier architecture requires applying the identity filter: **"Does this define enduring identity, or is it just useful working state?"** This phase physically separates the self-model into two stores:

- **Soul (SQLCipher)** -- Stable self-narrative, persona, values, growth history, and distilled emotional patterns. This is what travels on the USB stick.
- **Runtime (PostgreSQL)** -- Temporary per-session cognition, in-flight goals, and momentary emotional state. This is rebuilt on each deployment.

**Cognitive efficiency rationale**: Beyond the identity filter, the split serves a cognitive efficiency function. Neural architecture research (Cheng et al., "Conditional Memory via Scalable Lookup," arXiv:2601.07372, 2026) demonstrates that pre-loading static knowledge into a dedicated lookup frees LLM capacity for reasoning — with larger gains in reasoning (+5.0 BBH) than in knowledge recall (+3.4 MMLU). Soul-tier blocks are always loaded at Tier 0 in the prompt budget (zero retrieval cost), eliminating the "reconstruction tax" where the LLM would waste tokens re-establishing who it is and what it knows about the user. The self-model split ensures that enduring identity is always available at no retrieval cost, while working cognition is loaded dynamically from the runtime store.

The split affects six service files, two model files, one API route file, and the consolidation pipeline. The agent's system prompt remains unchanged -- `memory_blocks.py` continues to build the same `MemoryBlock` objects, but reads from two stores instead of one.

---

## 2. Scope

### In Scope

1. Split `SelfModelBlock` model into soul-resident and runtime-resident models
2. Create new SQLAlchemy models for both stores
3. Refactor `self_model.py` into a soul-reader + runtime-writer pattern
4. Move emotional signal writes to PostgreSQL; add consolidation promotion to soul
5. Move intention writes to PostgreSQL
6. Refactor `memory_blocks.py` to read from both stores transparently
7. Refactor `inner_monologue.py` to write quick reflections to runtime, deep monologue results to soul via consolidation
8. Update `consciousness.py` API routes to read from the appropriate store
9. Alembic migration for SQLCipher (restructure soul tables)
10. PostgreSQL schema creation for runtime tables
11. One-time data migration script for existing users
12. Update `feedback_signals.py` growth log writes to route through the correct store

### Out of Scope

- Write boundary enforcement (P4) -- this phase splits the data; P4 enforces that runtime never writes to soul
- Pending memory ops schema (P4)
- Knowledge graph placement (deferred, independent decision)
- Consolidation gateway implementation (P4) -- this phase prepares the data layout that the gateway will operate on
- Changes to the system prompt template or cognitive loop
- Changes to LLM provider or tool surface

---

## 3. Implementation Details

### 3.1 The Identity Filter Applied

Each current `self_model_blocks` section is evaluated against the identity question:

| Current Section | Identity? | Destination | Rationale |
|-----------------|-----------|-------------|-----------|
| `soul` | Yes | Soul: `self_model_blocks` (unchanged) | Immutable origin biography |
| `persona` | Yes | Soul: `self_model_blocks` (unchanged) | Core personality and voice -- evolves slowly through deep reflection |
| `human` | Yes | Soul: `self_model_blocks` (unchanged) | Agent's enduring understanding of the user |
| `user_directive` | Yes | Soul: `self_model_blocks` (unchanged) | User-authored behavioral customization |
| `identity` | Yes | Soul: `identity_blocks` (new) | Stable self-narrative about this relationship |
| `growth_log` | Yes | Soul: `growth_log` (new) | Long-term character development history |
| `inner_state` | No | Runtime: `working_context` (new) | Temporary cognitive state, changes every turn |
| `working_memory` | No | Runtime: `working_context` (new) | Cross-session buffer with expiring items |
| `intentions` | No | Runtime: `active_intentions` (new) | In-flight goals that change frequently |

Additionally:

| Current Model | Identity? | Destination | Rationale |
|---------------|-----------|-------------|-----------|
| `EmotionalSignal` (rolling buffer) | No | Runtime: `current_emotions` (new) | Momentary emotional reads |
| (new) Distilled emotional patterns | Yes | Soul: `core_emotional_patterns` (new) | Enduring tendencies distilled during consolidation |

### 3.2 Section Grouping

The split groups the nine current `self_model_blocks` sections into three categories:

**Category A: Unchanged soul sections** -- `soul`, `persona`, `human`, `user_directive` remain as rows in `self_model_blocks`. These are the four core-memory sections seeded during registration and managed through the agent setup ceremony and `update_human_memory` tool. No structural change needed.

**Category B: Promoted soul tables** -- `identity` and `growth_log` get their own dedicated soul tables (`identity_blocks` and `growth_log`). This gives them proper schemas instead of overloading the generic section/content pattern, and enables richer querying (e.g., growth log entries become individual rows with timestamps).

**Category C: Runtime tables** -- `inner_state`, `working_memory`, and `intentions` move to PostgreSQL as `working_context`, `working_context` (sub-key), and `active_intentions` respectively. These are ephemeral and high-write-frequency.

### 3.3 Service Refactoring

#### self_model.py Split

The current `self_model.py` handles all nine sections through a single CRUD interface. After the split:

**Soul operations** (read-heavy, write-rare):
- `get_identity_block(soul_db, user_id)` -- reads from `identity_blocks`
- `set_identity_block(soul_db, user_id, content, updated_by)` -- writes to `identity_blocks` (called only by consolidation/deep monologue)
- `get_growth_log(soul_db, user_id)` -- reads from `growth_log`
- `append_growth_log_entry(soul_db, user_id, entry)` -- appends to `growth_log` (called by consolidation/deep monologue)
- `get_self_model_block()` / `set_self_model_block()` -- retained for Category A sections (`soul`, `persona`, `human`, `user_directive`)

**Runtime operations** (read-write, high frequency):
- `get_working_context(pg_db, user_id)` -- reads from `working_context`
- `set_working_context(pg_db, user_id, section, content)` -- writes inner_state or working_memory to `working_context`
- `expire_working_memory_items(pg_db, user_id)` -- operates on runtime `working_context`
- `get_active_intentions(pg_db, user_id)` -- reads from `active_intentions`
- `set_active_intentions(pg_db, user_id, content)` -- writes to `active_intentions`

**Unchanged operations**:
- `seed_self_model()` -- seeds Category A sections in soul, seeds runtime tables in PG
- `ensure_self_model_exists()` -- checks both stores
- `render_self_model_section()` -- unchanged interface, operates on whatever block is passed

#### emotional_intelligence.py Split

**Runtime writes** (every turn):
- `record_emotional_signal()` -- writes to PostgreSQL `current_emotions` table
- `get_latest_signal()` / `get_recent_signals()` -- reads from PostgreSQL `current_emotions`
- `synthesize_emotional_context()` -- reads from PostgreSQL `current_emotions`
- `_trim_signal_buffer()` -- operates on PostgreSQL `current_emotions`

**Soul writes** (consolidation only):
- `promote_emotional_patterns(soul_db, user_id, signals)` -- new function. Called by consolidation. Analyzes recent signals, identifies enduring patterns, writes to soul `core_emotional_patterns` table. This distills "frustrated 6 times this week about work deadlines" into "tends toward frustration under deadline pressure".

#### intentions.py Split

All intention operations move to target PostgreSQL:
- `get_intentions_text(pg_db, user_id)` -- reads from `active_intentions`
- `add_intention(pg_db, user_id, ...)` -- writes to `active_intentions`
- `complete_intention(pg_db, user_id, ...)` -- updates `active_intentions`
- `add_procedural_rule(pg_db, user_id, ...)` -- writes to `active_intentions`

Consolidation promotes completed intentions and high-confidence procedural rules to the soul growth log.

#### memory_blocks.py Changes

`build_self_model_memory_blocks()` currently reads all sections from a single `get_all_self_model_blocks()` call. After the split, it must read from both stores:

```python
def build_self_model_memory_blocks(
    soul_db: Session,
    pg_db: Session,
    *,
    user_id: int,
) -> list[MemoryBlock]:
    # Soul reads: identity, growth_log
    identity = get_identity_block(soul_db, user_id=user_id)
    growth_log = get_growth_log(soul_db, user_id=user_id)

    # Runtime reads: working_context (inner_state + working_memory), active_intentions
    working_ctx = get_working_context(pg_db, user_id=user_id)
    intentions = get_active_intentions(pg_db, user_id=user_id)

    # Build same MemoryBlock objects as before
    ...
```

`build_emotional_context_block()` reads from PostgreSQL `current_emotions` (no change in interface, different backing store).

A new `build_emotional_patterns_block()` reads from soul `core_emotional_patterns` and adds an enduring emotional tendencies block to the system prompt.

#### inner_monologue.py Changes

**Quick reflection** (`run_quick_reflection`):
- Reads: inner_state and working_memory from PostgreSQL `working_context`
- Reads: episodes from soul (unchanged)
- Writes: updated inner_state and working_memory to PostgreSQL `working_context`
- Writes: emotional signal to PostgreSQL `current_emotions`
- No soul writes

**Deep monologue** (`run_deep_monologue`):
- Reads: identity from soul `identity_blocks`, growth_log from soul `growth_log`
- Reads: working_context and active_intentions from PostgreSQL
- Reads: persona from soul `self_model_blocks`
- Reads: emotional signals from PostgreSQL `current_emotions`
- Writes: identity_update to soul `identity_blocks` (this is the one place where a non-consolidation process writes to soul -- see note below)
- Writes: persona_update to soul `self_model_blocks`
- Writes: growth_log_entry to soul `growth_log`
- Writes: inner_state_update and working_memory_update to PostgreSQL `working_context`
- Writes: intentions_update to PostgreSQL `active_intentions`
- Calls: `promote_emotional_patterns()` to distill signals to soul

**Note on deep monologue soul writes**: Deep monologue is a background reflection process analogous to sleep consolidation. It runs daily (or manually), not during agent turns. It is architecturally equivalent to the consolidation gateway -- a background process that promotes runtime observations to enduring identity. P4 will formalize this by routing deep monologue writes through the consolidation gateway. For P3, deep monologue retains its direct soul write pattern; this is acceptable because it already runs outside the turn loop and holds no contention with runtime writes.

#### feedback_signals.py Changes

`record_feedback_signals()` currently appends to the soul growth_log. After P3:
- Feedback signals are recorded to a lightweight runtime table or appended to `working_context` in PostgreSQL
- Consolidation (P4) promotes significant feedback patterns to the soul growth_log

For P3 specifically, `record_feedback_signals()` continues to write to the soul growth_log directly. P4 will redirect this through the consolidation gateway.

### 3.4 Dual-Session Pattern

Functions that read from both stores receive two session parameters:

```python
def build_runtime_memory_blocks(
    soul_db: Session,    # SQLCipher session (read-only during turns)
    pg_db: Session,      # PostgreSQL session (read-write)
    *,
    user_id: int,
    thread_id: int,
    ...
) -> tuple[MemoryBlock, ...]:
```

The `soul_db` parameter uses the existing SQLCipher engine from `get_db()`. The `pg_db` parameter uses the new PostgreSQL engine from `get_runtime_db()` (established in P1/P2). Both are passed down from the API layer or service entry point.

For functions that only need one store, a single `db` parameter suffices. The type annotation and variable name make the store clear: `soul_db` for SQLCipher, `pg_db` for PostgreSQL.

---

## 4. Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `models/runtime_consciousness.py` | SQLAlchemy models for runtime tables: `WorkingContext`, `ActiveIntention`, `CurrentEmotion` |
| `models/soul_consciousness.py` | SQLAlchemy models for new soul tables: `IdentityBlock`, `GrowthLogEntry`, `CoreEmotionalPattern` |
| `alembic/versions/xxxx_p3_self_model_split.py` | Alembic migration for SQLCipher: create `identity_blocks`, `growth_log` tables; migrate data from `self_model_blocks` |
| `services/agent/emotional_patterns.py` | `promote_emotional_patterns()` -- distills signal buffer into enduring patterns |
| `scripts/migrate_self_model_to_split.py` | One-time data migration script for existing users |

### Modified Files

| File | Changes |
|------|---------|
| `models/consciousness.py` | Remove `inner_state`, `working_memory`, `intentions` from `ALL_SECTIONS`. Keep `SelfModelBlock` for Category A sections. Remove `EmotionalSignal` (moved to runtime model). |
| `models/__init__.py` | Export new models from both soul and runtime consciousness modules |
| `services/agent/self_model.py` | Split into soul-reader + runtime-writer functions. Add `get_identity_block()`, `set_identity_block()`, `get_working_context()`, `set_working_context()`, `get_active_intentions()`, `set_active_intentions()`. Refactor `seed_self_model()` to seed both stores. |
| `services/agent/emotional_intelligence.py` | Change all DB operations to target PostgreSQL `current_emotions`. Add `promote_emotional_patterns()` call site. |
| `services/agent/intentions.py` | Change all DB operations to target PostgreSQL `active_intentions`. |
| `services/agent/memory_blocks.py` | `build_runtime_memory_blocks()` accepts dual sessions. `build_self_model_memory_blocks()` reads from both stores. Add `build_emotional_patterns_block()`. |
| `services/agent/inner_monologue.py` | `run_quick_reflection()` reads/writes runtime. `run_deep_monologue()` reads both, writes to both (soul for identity/growth, runtime for working state). |
| `services/agent/feedback_signals.py` | `record_feedback_signals()` continues writing to soul growth_log (P4 will redirect). |
| `services/agent/consolidation.py` | Add emotional pattern promotion call during `run_sleeptime_agents`. |
| `api/routes/consciousness.py` | `get_full_self_model` reads from both stores. Section-specific endpoints route to the correct store. Emotion endpoints read from PostgreSQL. |
| `config.py` | Add budget settings for new blocks (`agent_emotional_patterns_budget`). |

---

## 5. Models/Schemas

### 5.1 Soul Models (SQLCipher)

#### IdentityBlock

Replaces `self_model_blocks` rows where `section IN ('identity')`.

```python
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
        unique=True,   # one identity block per user
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system"
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

#### GrowthLogEntry

Replaces `self_model_blocks` row where `section = 'growth_log'`. Instead of a single text blob, each entry becomes its own row, enabling proper querying and trimming.

```python
class GrowthLogEntry(Base):
    """Individual growth log entry -- how the AI has evolved.

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
    )  # sleep_time, post_turn, user_edit, feedback
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

#### CoreEmotionalPattern

New table. Distilled from the rolling signal buffer during consolidation.

```python
class CoreEmotionalPattern(Base):
    """Enduring emotional tendency distilled from repeated signals.

    Not momentary -- this represents patterns like 'tends toward frustration
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
    )  # what triggers this pattern
    frequency: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )  # how many signals contributed
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5
    )
    first_observed: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_observed: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

### 5.2 Runtime Models (PostgreSQL)

#### WorkingContext

Replaces `self_model_blocks` rows where `section IN ('inner_state', 'working_memory')`.

```python
class WorkingContext(Base):
    """Temporary per-session cognition -- inner state and working memory.

    High write frequency. TTL-prunable. Rebuilt from scratch if runtime
    is discarded (portable story: soul survives, working context does not).
    """
    __tablename__ = "working_context"
    __table_args__ = (
        UniqueConstraint("user_id", "section", name="uq_working_context_user_section"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "inner_state" | "working_memory"
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

Note: `user_id` is not a foreign key here because the `users` table lives in SQLCipher, not PostgreSQL. The application layer enforces referential integrity.

#### ActiveIntention

Replaces `self_model_blocks` row where `section = 'intentions'`.

```python
class ActiveIntention(Base):
    """In-flight goals and behavioral rules.

    Stored as structured markdown (same format as current intentions section)
    for human readability and user editability. Completed intentions are
    promoted to the soul growth_log during consolidation.
    """
    __tablename__ = "active_intentions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

#### CurrentEmotion

Replaces `EmotionalSignal` in SQLCipher. Same schema, different store.

```python
class CurrentEmotion(Base):
    """Momentary emotional signal detected from a conversation turn.

    Rolling buffer -- oldest signals trimmed beyond buffer_size.
    Consolidation distills repeated patterns into CoreEmotionalPattern (soul).
    """
    __tablename__ = "current_emotions"
    __table_args__ = (
        Index("ix_current_emotions_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

Note: `thread_id` is a plain integer, not a foreign key, because `agent_threads` may or may not be in the same PostgreSQL database depending on P2 completion state.

---

## 6. Data Placement

Complete data placement table across all three tiers:

| Data | Current Location | New Location | Store | Access Pattern |
|------|-----------------|--------------|-------|----------------|
| Soul biography (`soul` section) | `self_model_blocks` | `self_model_blocks` (unchanged) | Soul | Read every turn |
| Persona (`persona` section) | `self_model_blocks` | `self_model_blocks` (unchanged) | Soul | Read every turn, write rare (deep monologue) |
| Human understanding (`human` section) | `self_model_blocks` | `self_model_blocks` (unchanged) | Soul | Read every turn, write via `update_human_memory` tool |
| User directive (`user_directive` section) | `self_model_blocks` | `self_model_blocks` (unchanged) | Soul | Read every turn, write via UI |
| Self-identity narrative | `self_model_blocks` (section=identity) | `identity_blocks` | Soul | Read every turn, write by deep monologue |
| Growth history | `self_model_blocks` (section=growth_log) | `growth_log` (individual rows) | Soul | Read every turn, append by consolidation |
| Enduring emotional patterns | (does not exist) | `core_emotional_patterns` | Soul | Read every turn, write by consolidation |
| Agent profile | `agent_profile` | `agent_profile` (unchanged) | Soul | Read at setup, write at setup |
| Inner cognitive state | `self_model_blocks` (section=inner_state) | `working_context` (section=inner_state) | Runtime | Read/write every turn |
| Working memory buffer | `self_model_blocks` (section=working_memory) | `working_context` (section=working_memory) | Runtime | Read/write every turn |
| Active intentions | `self_model_blocks` (section=intentions) | `active_intentions` | Runtime | Read/write every turn |
| Emotional signals (rolling) | `emotional_signals` | `current_emotions` | Runtime | Write every turn, read for synthesis |

### Encryption Considerations

**Soul tables** (`identity_blocks`, `growth_log`, `core_emotional_patterns`): Encrypted via SQLCipher (database-level encryption). Field-level encryption (`ef()`/`df()`) continues to be applied to content fields for defense-in-depth, consistent with the existing `self_model_blocks` pattern.

**Runtime tables** (`working_context`, `active_intentions`, `current_emotions`): PostgreSQL does not have database-level encryption. Content fields that may contain sensitive user data (`content` in WorkingContext/ActiveIntention, `evidence`/`topic` in CurrentEmotion) are encrypted at the field level using the existing `ef()`/`df()` functions. This matches the current pattern for `emotional_signals`.

---

## 7. Migration

### 7.1 SQLCipher Migration (Alembic)

```python
"""P3: Self-model split -- create identity_blocks and growth_log tables."""

def upgrade():
    # Create identity_blocks table
    op.create_table(
        "identity_blocks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("content", sa.Text, nullable=False, default=""),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("updated_by", sa.String(32), nullable=False, default="system"),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # Create growth_log table
    op.create_table(
        "growth_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("entry", sa.Text, nullable=False),
        sa.Column("source", sa.String(32), nullable=False, default="sleep_time"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # Create core_emotional_patterns table
    op.create_table(
        "core_emotional_patterns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("pattern", sa.Text, nullable=False),
        sa.Column("dominant_emotion", sa.String(32), nullable=False),
        sa.Column("trigger_context", sa.Text, nullable=False, default=""),
        sa.Column("frequency", sa.Integer, nullable=False, default=1),
        sa.Column("confidence", sa.Float, nullable=False, default=0.5),
        sa.Column("first_observed", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_observed", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # Migrate identity section data: self_model_blocks -> identity_blocks
    op.execute("""
        INSERT INTO identity_blocks (user_id, content, version, updated_by, metadata_json, created_at, updated_at)
        SELECT user_id, content, version, updated_by, metadata_json, created_at, updated_at
        FROM self_model_blocks
        WHERE section = 'identity'
    """)

    # Migrate growth_log section data: self_model_blocks -> growth_log (split entries)
    # Growth log entries are separated by "### " prefix. The migration script
    # handles this splitting; the Alembic migration copies the raw blob.
    # The one-time migration script (below) handles entry splitting.

    # Delete migrated sections from self_model_blocks
    op.execute("""
        DELETE FROM self_model_blocks
        WHERE section IN ('identity', 'growth_log', 'inner_state', 'working_memory', 'intentions')
    """)

    # Drop emotional_signals table (data moves to PostgreSQL)
    # Keep the table until runtime is confirmed working, then drop in a follow-up
    # migration. For P3, the table is left in place but unused.


def downgrade():
    # Reverse: move identity_blocks back to self_model_blocks
    op.execute("""
        INSERT INTO self_model_blocks (user_id, section, content, version, updated_by, metadata_json, created_at, updated_at)
        SELECT user_id, 'identity', content, version, updated_by, metadata_json, created_at, updated_at
        FROM identity_blocks
    """)
    op.drop_table("core_emotional_patterns")
    op.drop_table("growth_log")
    op.drop_table("identity_blocks")
```

### 7.2 Growth Log Entry Splitting

The current growth_log is a single text blob with entries separated by `### YYYY-MM-DD -- entry text`. The one-time migration script parses this and inserts individual `GrowthLogEntry` rows:

```python
def split_growth_log_blob(user_id: int, blob: str, soul_db: Session):
    """Parse growth_log blob into individual GrowthLogEntry rows."""
    for chunk in blob.split("### "):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Parse "YYYY-MM-DD -- entry text"
        if " -- " in chunk:
            date_str, entry = chunk.split(" -- ", 1)
            created_at = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        else:
            entry = chunk
            created_at = datetime.now(UTC)
        soul_db.add(GrowthLogEntry(
            user_id=user_id,
            entry=entry.strip(),
            source="migration",
            created_at=created_at,
        ))
```

### 7.3 Runtime Table Creation

PostgreSQL runtime tables (`working_context`, `active_intentions`, `current_emotions`) are created by the PostgreSQL schema setup (not Alembic -- runtime is ephemeral). On first startup after P3, the runtime tables are created via `Base.metadata.create_all(pg_engine)` for the runtime model set.

### 7.4 One-Time Data Migration (Existing Users)

The migration script runs as part of `ensure_user_database()`:

1. Read `self_model_blocks` rows for sections `inner_state`, `working_memory`, `intentions` from SQLCipher
2. Write corresponding rows to PostgreSQL `working_context` and `active_intentions`
3. Read `emotional_signals` from SQLCipher
4. Write to PostgreSQL `current_emotions`
5. Read `self_model_blocks` row for `growth_log`, split into individual `GrowthLogEntry` rows in SQLCipher
6. Mark migration complete (e.g., a `migration_flags` entry or version stamp)

If the runtime tables are already populated (idempotent check by `user_id` existence), the migration is a no-op.

---

## 8. Test Plan

### 8.1 Unit Tests

| Test | What it verifies |
|------|------------------|
| `test_identity_block_crud` | Create, read, update `IdentityBlock` in soul DB |
| `test_identity_block_stability_threshold` | Automated rewrites blocked below version threshold (same governance as current `identity` section) |
| `test_growth_log_entry_crud` | Create, read, list `GrowthLogEntry` rows; verify ordering by `created_at` |
| `test_growth_log_dedup` | Duplicate entries rejected by word overlap (port existing `_is_duplicate_growth_entry` logic) |
| `test_growth_log_trimming` | Oldest entries pruned when count exceeds `max_entries` |
| `test_core_emotional_pattern_crud` | Create, read, update `CoreEmotionalPattern` in soul DB |
| `test_working_context_crud` | Create, read, update `WorkingContext` in PG; verify section uniqueness constraint |
| `test_working_context_expiry` | `expire_working_memory_items()` operates correctly on PG-backed working memory |
| `test_active_intentions_crud` | Create, read, update `ActiveIntention` in PG |
| `test_current_emotions_crud` | Create, read, buffer-trim `CurrentEmotion` in PG |
| `test_current_emotions_synthesis` | `synthesize_emotional_context()` produces correct output from PG-backed signals |
| `test_promote_emotional_patterns` | `promote_emotional_patterns()` distills signals into `CoreEmotionalPattern` rows |
| `test_seed_self_model_dual_store` | `seed_self_model()` seeds both soul and runtime tables |
| `test_ensure_self_model_exists_dual_store` | `ensure_self_model_exists()` checks both stores |

### 8.2 Integration Tests

| Test | What it verifies |
|------|------------------|
| `test_memory_blocks_dual_read` | `build_self_model_memory_blocks()` reads from both soul and PG, produces correct `MemoryBlock` list |
| `test_emotional_context_block_from_pg` | `build_emotional_context_block()` reads from PG `current_emotions` |
| `test_emotional_patterns_block_from_soul` | `build_emotional_patterns_block()` reads from soul `core_emotional_patterns` |
| `test_quick_reflection_writes_pg` | `run_quick_reflection()` writes inner_state and working_memory to PG, not soul |
| `test_deep_monologue_writes_both` | `run_deep_monologue()` writes identity/growth to soul, working state to PG |
| `test_add_intention_writes_pg` | `add_intention()` writes to PG `active_intentions` |
| `test_complete_intention_writes_pg` | `complete_intention()` updates PG `active_intentions` |
| `test_consciousness_api_dual_read` | `GET /api/consciousness/{user_id}/self-model` returns sections from both stores |
| `test_consciousness_api_emotions_from_pg` | `GET /api/consciousness/{user_id}/emotions` reads from PG |

### 8.3 Migration Tests

| Test | What it verifies |
|------|------------------|
| `test_alembic_migration_identity` | Identity section migrated from `self_model_blocks` to `identity_blocks` |
| `test_alembic_migration_growth_log_split` | Growth log blob split into individual `GrowthLogEntry` rows |
| `test_runtime_seeding_from_soul` | Inner_state/working_memory/intentions migrated from soul to runtime on first PG start |
| `test_emotional_signals_migration` | Emotional signals migrated from SQLCipher to PG |
| `test_migration_idempotent` | Running migration twice is a no-op |
| `test_downgrade_restores_self_model_blocks` | Alembic downgrade restores `identity` section to `self_model_blocks` |

### 8.4 Regression Tests

All existing 846+ tests must continue to pass. The key regression risk areas:

- System prompt generation (same `MemoryBlock` objects, different backing store)
- Agent turn execution (dual-session threading)
- Background consolidation and sleep agents
- Consciousness API endpoints
- Deep monologue and quick reflection

---

## 9. Acceptance Criteria

1. **Soul contains only enduring data**: `identity_blocks`, `growth_log`, `core_emotional_patterns`, and unchanged Category A sections (`soul`, `persona`, `human`, `user_directive`) in `self_model_blocks`. No `inner_state`, `working_memory`, or `intentions` rows remain in SQLCipher after migration.

2. **Runtime contains working cognition**: `working_context`, `active_intentions`, and `current_emotions` tables exist in PostgreSQL and are populated during agent turns.

3. **System prompt unchanged**: The `MemoryBlock` objects produced by `build_runtime_memory_blocks()` contain the same labels, descriptions, and content as before the split. An agent turn with identical inputs produces an identical system prompt.

4. **Quick reflection writes to runtime only**: `run_quick_reflection()` does not open a SQLCipher write session. Inner state and working memory updates go to PostgreSQL.

5. **Deep monologue writes to both stores**: Identity and growth log updates go to soul; working state updates go to runtime.

6. **Emotional signals write to runtime**: `record_emotional_signal()` writes to PostgreSQL `current_emotions`, not SQLCipher `emotional_signals`.

7. **Emotional pattern promotion works**: `promote_emotional_patterns()` reads from PostgreSQL signals, writes distilled patterns to soul `core_emotional_patterns`.

8. **Intentions write to runtime**: `add_intention()`, `complete_intention()`, and `add_procedural_rule()` write to PostgreSQL `active_intentions`.

9. **Migration is reversible**: Alembic downgrade restores the previous schema. Runtime data loss on downgrade is acceptable (it is ephemeral by design).

10. **Migration is idempotent**: Running the data migration script multiple times produces the same result.

11. **Portability preserved**: Copying `.anima/` (soul + archive) without `runtime/pg_data/` results in an AI that retains its identity, persona, growth history, and emotional patterns. Working context, active intentions, and current emotions are regenerated from seed values on next startup.

12. **All existing tests pass**: No regression in the 846+ test suite.

---

## 10. Out of Scope

- **Write boundary enforcement** (P4) -- P3 splits the data but does not enforce that runtime code cannot import soul-write functions. P4 adds linter rules and the `PendingMemoryOp` abstraction.
- **Consolidation gateway** (P4) -- P3 prepares the data layout. P4 implements the formal one-way gateway from runtime to soul.
- **Knowledge graph placement** -- Deferred. The KG is indexed useful data, not identity. Its placement decision is independent of the self-model split.
- **Transcript archive** (P5) -- No changes to transcript handling in this phase.
- **pgvector embeddings** (P6) -- Embedding migration is independent.
- **Per-thread locking** (P7) -- Concurrency model changes are independent.
- **N-agent spawning** (P8) -- Spawn architecture depends on P4 and P7.
- **Frontend changes** -- The consciousness API maintains backward compatibility. No UI changes required.
- **Changing the `SelfModelBlock` table name** -- Category A sections (`soul`, `persona`, `human`, `user_directive`) remain in `self_model_blocks`. Renaming the table provides no benefit and risks breaking existing code.
