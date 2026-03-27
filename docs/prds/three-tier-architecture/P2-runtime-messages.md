---
title: "Phase 2: Runtime Messages"
description: Migrate agent runtime models (threads, messages, runs, steps) from per-user SQLCipher to shared PostgreSQL, enabling concurrent multi-agent writes and removing SQLite's single-writer bottleneck for conversation data.
category: prd
version: "1.0"
---

# Phase 2: Runtime Messages

**Status**: Approved
**Date**: 2026-03-26 (updated 2026-03-27)
**Depends on**: P1 (Embedded PostgreSQL)
**Blocks**: P3, P6, P7
**Design Spec**: [2026-03-27-p2-runtime-messages-design.md](../../superpowers/specs/2026-03-27-p2-runtime-messages-design.md)

### Design Updates (2026-03-27)

Three departures from the original PRD, decided during design review:

1. **Sync sessions (psycopg2), not async** — The entire service layer is synchronous. Converting to async belongs in P7 (Concurrency Refactor). Using sync PG sessions keeps the diff minimal and avoids rewiring background tasks, companion, and tool context.
2. **No feature flag** — `ANIMA_USE_RUNTIME_PG` is removed. Desktop app with embedded PG has no deployment scenario needing a SQLCipher fallback. One code path, less maintenance.
3. **PG-specific column types** — `TIMESTAMPTZ`, `postgresql.JSON` instead of generic types. Runtime is PG-only; tests use embedded PG.

## Overview

AnimaOS currently stores all agent runtime state -- threads, messages, runs, steps, and background task tracking -- in per-user SQLCipher databases. This design was correct for the single-user portable model, but it creates a hard ceiling for multi-agent concurrency: SQLite allows only one writer at a time, and the WAL-mode busy timeout (30 seconds) is the only concurrency mechanism available.

Phase 2 moves these runtime models to the shared PostgreSQL instance provisioned in Phase 1. Messages are ephemeral by design (they live only as long as the conversation context window), making them the lowest-risk candidates for migration. Identity data (soul, self-model, memory items, claims, episodes, knowledge graph) remains in SQLCipher where it belongs.

### Why messages first

1. **Highest write contention** -- Every agent turn writes 3-10 rows (user message, assistant message, tool results, steps). Concurrent agent processes (reflection, consolidation, proactive greeting) all compete for the same SQLite write lock.
2. **No encryption requirement** -- Message content is already decrypted at the application layer before being passed to the LLM. The SQLCipher field-level encryption (`ef`/`df` wrappers) on `content_text` is defense-in-depth, not a privacy boundary. Runtime PG can use TLS in transit and `pgcrypto` at rest if needed.
3. **No migration burden** -- Messages are not durable knowledge. The compaction system already discards old messages and replaces them with summaries. A clean cutover (new messages go to PG, old SQLCipher tables become dead code) is safe.
4. **Unblocks Phase 3+** -- Once the service layer can accept two session types (soul session for identity, runtime session for messages), subsequent phases can move memory items, episodes, and claims incrementally.

## Scope

### In scope

- New `RuntimeBase` declarative base for PostgreSQL models
- Runtime versions of: `AgentThread`, `AgentMessage`, `AgentRun`, `AgentStep`, `BackgroundTaskRun`
- Runtime database engine/session factory (`db/runtime.py`)
- Alembic migration environment for the runtime database (separate from the soul database)
- Rewiring of all service modules that read/write these models
- Updated test fixtures providing runtime sessions (using embedded PG)
- Removal of field-level encryption (`ef`/`df`) calls on message content in the PG path (TLS + at-rest encryption replaces this)

### Out of scope

- Migration of `MemoryItem`, `MemoryEpisode`, `MemoryClaim`, `MemoryDailyLog`, `MemoryVector`, `KGEntity`, `KGRelation` (Phase 3+)
- Migration of `SessionNote` (stays in soul -- it is distilled session knowledge, not raw messages)
- Migration of `ForgetAuditLog` (stays in soul -- tracks identity-level deletions)
- Migration of `User`, `UserKey`, `AgentProfile`, `SelfModelBlock`, `EmotionalSignal` (identity tier, stays in soul permanently)
- PostgreSQL high-availability, replication, or connection pooling (operational concern, not application code)
- Multi-tenant isolation in PG (single-user desktop app; `user_id` column is sufficient)

## Implementation Details

### 1. Runtime database infrastructure (`db/runtime.py`)

A new module that mirrors `db/session.py` but targets PostgreSQL.

```
db/
  base.py            # existing SoulBase (renamed from Base)
  runtime_base.py    # new RuntimeBase for PG models
  runtime.py         # new engine/session factory for PG
  session.py         # existing soul DB engine (unchanged)
```

**Key behaviors:**

- `RuntimeBase` is a separate `DeclarativeBase` with its own `MetaData`. This prevents any accidental table creation in the soul database.
- `get_runtime_engine()` reads `ANIMA_RUNTIME_DATABASE_URL` (default: `postgresql://localhost:5432/anima_runtime`). Returns a standard PG engine with connection pooling (`pool_size=5`, `max_overflow=10`, `pool_pre_ping=True`).
- `get_runtime_session_factory()` returns a `sessionmaker` bound to the runtime engine.
- `get_runtime_db()` is a FastAPI dependency that yields a runtime `Session`. It does not require the unlock token -- runtime data is not encrypted.
- `ensure_runtime_database()` runs Alembic migrations against the runtime PG on startup.

**Feature flag:**

When `ANIMA_USE_RUNTIME_PG=false`, all runtime session requests fall through to the soul session factory (existing behavior). This is the escape hatch.

```python
def get_runtime_session_factory(
    user_id: int | None = None,
) -> sessionmaker[Session]:
    if not settings.use_runtime_pg:
        # Fallback: use soul DB (SQLCipher)
        if user_id is not None:
            return ensure_user_database(user_id)
        return SessionLocal
    return _pg_runtime_factory
```

### 2. Runtime model definitions (`models/runtime.py`)

New file containing PG-native versions of the five runtime models. These are structurally identical to the existing SQLCipher models but:

- Inherit from `RuntimeBase` instead of `Base`
- Drop the `ForeignKey("users.id")` constraint (user table stays in soul DB; `user_id` is an unvalidated integer column with an index)
- Drop the `ForeignKey("agent_messages.id")` on `AgentRun.pending_approval_message_id` and replace with a plain indexed integer column (cross-table FK still works within the same PG database)
- Use `BIGINT` primary keys instead of `INTEGER` for future-proofing
- Use `TIMESTAMPTZ` columns (PG-native) instead of `DateTime(timezone=True)` with SQLite `func.now()`
- Add a composite index on `(user_id, created_at)` for `AgentMessage` to accelerate conversation search

### 3. Runtime model schemas

```python
# models/runtime.py

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, TIMESTAMPTZ
from sqlalchemy.orm import Mapped, mapped_column, relationship

from anima_server.db.runtime_base import RuntimeBase


class RuntimeThread(RuntimeBase):
    __tablename__ = "runtime_threads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    last_message_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    next_message_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    messages: Mapped[list[RuntimeMessage]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="RuntimeMessage.sequence_id",
    )
    runs: Mapped[list[RuntimeRun]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="RuntimeRun.started_at",
    )


class RuntimeRun(RuntimeBase):
    __tablename__ = "runtime_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_approval_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )

    thread: Mapped[RuntimeThread] = relationship(back_populates="runs")
    steps: Mapped[list[RuntimeStep]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RuntimeStep.step_index",
    )


class RuntimeStep(RuntimeBase):
    __tablename__ = "runtime_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_index", name="uq_runtime_steps_run_step"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    tool_calls_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    run: Mapped[RuntimeRun] = relationship(back_populates="steps")


class RuntimeMessage(RuntimeBase):
    __tablename__ = "runtime_messages"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "sequence_id", name="uq_runtime_messages_thread_seq"
        ),
        Index("ix_runtime_messages_user_created", "user_id", "created_at"),
        Index("ix_runtime_messages_thread_context", "thread_id", "is_in_context"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    step_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    sequence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_args_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_in_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    thread: Mapped[RuntimeThread] = relationship(back_populates="messages")


class RuntimeBackgroundTaskRun(RuntimeBase):
    __tablename__ = "runtime_background_task_runs"
    __table_args__ = (
        Index("ix_runtime_bg_task_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
```

**Design notes:**

- `RuntimeMessage` adds a `user_id` column directly on the message row. The current SQLCipher model derives `user_id` by joining through `AgentThread`. Denormalizing this avoids a join on every conversation search query and simplifies the service layer.
- Table names are prefixed with `runtime_` to make the PG schema self-documenting and to avoid collisions if soul tables are ever co-located.
- Foreign keys between runtime tables (e.g., `RuntimeMessage.thread_id` -> `RuntimeThread.id`) are enforced within PG via `ForeignKeyConstraint` on the `__table_args__`. The relationship definitions above rely on SQLAlchemy's ORM-level joins. Explicit FK constraints will be added in the Alembic migration.
- The `RuntimeRun.pending_approval_message_id` column intentionally does not use a formal FK constraint to `RuntimeMessage.id` to avoid circular dependency issues during cascade deletes. It is an indexed lookup column only.

### 4. Service layer rewiring

The core change is introducing a **dual-session pattern**: service functions that touch both runtime and soul data receive two session arguments.

#### 4a. `persistence.py` -- Primary target

Every function in `persistence.py` currently takes a single `db: Session` and operates on `AgentThread`, `AgentMessage`, `AgentRun`, `AgentStep`. These all move to the runtime session.

**Before:**
```python
def get_or_create_thread(db: Session, user_id: int) -> AgentThread:
    thread = db.scalar(select(AgentThread).where(AgentThread.user_id == user_id))
    ...
```

**After:**
```python
def get_or_create_thread(db: Session, user_id: int) -> RuntimeThread:
    thread = db.scalar(select(RuntimeThread).where(RuntimeThread.user_id == user_id))
    ...
```

The function signature stays `db: Session` but the caller passes the runtime session. The model import changes from `AgentThread` to `RuntimeThread`.

**Functions to rewire:**

| Function | Current model | New model | Session |
|---|---|---|---|
| `get_or_create_thread` | `AgentThread` | `RuntimeThread` | runtime |
| `load_thread_history` | `AgentMessage`, `AgentThread` | `RuntimeMessage`, `RuntimeThread` | runtime |
| `list_transcript_messages` | `AgentMessage`, `AgentRun`, `AgentThread` | `RuntimeMessage`, `RuntimeRun`, `RuntimeThread` | runtime |
| `create_run` | `AgentRun` | `RuntimeRun` | runtime |
| `append_user_message` | calls `append_message` | calls `append_message` | runtime |
| `append_message` | `AgentMessage` | `RuntimeMessage` | runtime |
| `create_step` | `AgentStep` | `RuntimeStep` | runtime |
| `persist_agent_result` | `AgentMessage`, `AgentStep` | `RuntimeMessage`, `RuntimeStep` | runtime |
| `finalize_run` | `AgentRun` | `RuntimeRun` | runtime |
| `mark_run_failed` | `AgentRun` | `RuntimeRun` | runtime |
| `cancel_run` | `AgentRun`, `AgentMessage` | `RuntimeRun`, `RuntimeMessage` | runtime |
| `save_approval_checkpoint` | `AgentMessage`, `AgentRun` | `RuntimeMessage`, `RuntimeRun` | runtime |
| `load_approval_checkpoint` | `AgentRun`, `AgentMessage` | `RuntimeRun`, `RuntimeMessage` | runtime |
| `clear_approval_checkpoint` | `AgentRun`, `AgentMessage` | `RuntimeRun`, `RuntimeMessage` | runtime |
| `reset_thread` | `AgentThread` | `RuntimeThread` | runtime |
| `clear_threads` | `AgentThread` | `RuntimeThread` | runtime |
| `count_messages_by_role` | `AgentMessage` | `RuntimeMessage` | runtime |

**Encryption removal:**

In the PG path, the `ef()` and `df()` calls on `content_text` are removed. The `append_message` function currently does:

```python
content_text=ef(uid, content_text, table="agent_messages", field="content_text"),
```

In the runtime path this becomes:

```python
content_text=content_text,
```

Similarly, `load_thread_history` drops the `df()` call:

```python
# Before
content = df(uid, row.content_text or "", table="agent_messages", field="content_text")
# After (runtime path)
content = row.content_text or ""
```

#### 4b. `sequencing.py`

`reserve_message_sequences` queries `AgentThread.next_message_sequence`. This changes to `RuntimeThread.next_message_sequence`. The optimistic-locking pattern (CAS on `next_message_sequence`) works identically on PG and is actually more robust since PG supports `SELECT ... FOR UPDATE` for true row-level locking.

Consider upgrading the CAS loop to `FOR UPDATE` in the PG path:

```python
def reserve_message_sequences(
    db: Session,
    *,
    thread_id: int,
    count: int,
) -> int:
    if count < 1:
        raise ValueError("count must be at least 1")

    row = db.execute(
        select(RuntimeThread.next_message_sequence)
        .where(RuntimeThread.id == thread_id)
        .with_for_update()
    ).scalar_one()

    start = int(row)
    db.execute(
        update(RuntimeThread)
        .where(RuntimeThread.id == thread_id)
        .values(next_message_sequence=start + count)
    )
    return start
```

#### 4c. `conversation_search.py`

`_search_messages` queries `AgentThread` and `AgentMessage`. Both move to runtime models. The `MemoryDailyLog` search in `_search_daily_logs` stays on the soul session -- daily logs are distilled knowledge, not raw messages.

This means `search_conversation_history` needs **two sessions**:

```python
async def search_conversation_history(
    runtime_db: Session,
    soul_db: Session,
    *,
    user_id: int,
    query: str,
    ...
) -> list[ConversationHit]:
```

The `_search_messages` helper uses `runtime_db`. The `_search_daily_logs` helper uses `soul_db`.

#### 4d. `compaction.py`

Both `compact_thread_context` and `compact_thread_context_with_llm` query and mutate `AgentMessage` rows. These move entirely to the runtime session. The `AgentThread` reference also moves.

#### 4e. `service.py`

The main orchestrator. Currently receives a single `db: Session` from the FastAPI dependency. After P2, it needs both sessions.

**Approach:** Introduce a `RuntimeContext` dataclass that bundles both sessions:

```python
@dataclass(slots=True)
class DbPair:
    soul: Session    # SQLCipher per-user DB (identity, memory)
    runtime: Session  # PostgreSQL (messages, runs, steps)
```

The `_execute_agent_turn_locked` function currently threads `db` through every call. After P2, it passes `db_pair.runtime` to persistence/compaction functions and `db_pair.soul` to memory/consolidation functions.

**Alternatively** (simpler, recommended for P2): Since `service.py` already passes `db` to each function individually, the minimal change is to resolve two sessions at the top of `_execute_agent_turn_locked` and pass the correct one to each callee. No new dataclass needed.

```python
async def _execute_agent_turn_locked(
    user_message: str,
    user_id: int,
    db: Session,         # soul session (from FastAPI dependency)
    *,
    ...
) -> AgentResult:
    runtime_db = get_runtime_session(user_id)  # PG session
    try:
        # persistence functions get runtime_db
        thread = get_or_create_thread(runtime_db, user_id)
        run = create_run(runtime_db, ...)
        # memory functions get soul db
        memory_blocks = build_runtime_memory_blocks(db, ...)
        ...
    finally:
        runtime_db.close()
```

#### 4f. `companion.py`

`AnimaCompanion.warm()` and `ensure_history_loaded()` call `load_thread_history(db, ...)`. These need the runtime session. The companion needs a way to obtain a runtime session.

**Approach:** Add a `runtime_db_factory` to the companion constructor:

```python
class AnimaCompanion:
    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        user_id: int,
        runtime_db_factory: Callable[[], Session] | None = None,
        ...
    ) -> None:
        self._runtime_db_factory = runtime_db_factory or get_runtime_session_factory()
```

When `ensure_history_loaded` is called without a db argument (e.g., from background tasks), it creates a runtime session from the factory.

#### 4g. `streaming.py`

No changes needed. `streaming.py` only constructs `AgentStreamEvent` dataclasses from `AgentResult` data. It never touches the database directly.

#### 4h. `reflection.py`

`run_reflection` reads messages indirectly via `run_quick_reflection` and `run_sleeptime_agents`. These call into inner monologue and sleep agent modules that receive a `db_factory`. The db_factory currently produces soul sessions.

After P2, reflection tasks that read messages need a runtime session. The `db_factory` pattern must be extended:

```python
def schedule_reflection(
    *,
    user_id: int,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
```

Inner monologue reads conversation history (runtime) and writes self-model updates (soul). It needs both factories.

#### 4i. `chat.py` (API route)

The route handler currently injects `db: Session = Depends(get_db)` (soul session). After P2, it also needs a runtime session:

```python
@router.post("")
async def send_message(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),           # soul
    runtime_db: Session = Depends(get_runtime_db),  # runtime
) -> ChatResponse | StreamingResponse:
```

The `get_chat_history` endpoint is the most visible change: it reads `AgentMessage` rows that are now in PG:

```python
@router.get("/history")
async def get_chat_history(
    ...
    runtime_db: Session = Depends(get_runtime_db),
) -> list[ChatHistoryMessage]:
    rows = list_agent_history(userId, runtime_db, limit=limit)
    ...
```

The `/home` endpoint queries `AgentMessage` count -- this moves to the runtime session. The `MemoryItem`, `Task`, and `MemoryDailyLog` queries stay on the soul session.

### 5. Alembic configuration for runtime DB

A separate Alembic environment for runtime migrations:

```
apps/server/
  alembic.ini             # existing (soul DB)
  alembic/                # existing (soul migrations)
  alembic_runtime.ini     # new
  alembic_runtime/        # new
    env.py
    versions/
      001_create_runtime_tables.py
```

The runtime `env.py` imports `RuntimeBase.metadata` and connects to the PG engine. Migrations run automatically on startup via `ensure_runtime_database()`.

### 6. Configuration additions (`config.py`)

```python
class Settings(BaseSettings):
    ...
    # Runtime database (Phase 2)
    runtime_database_url: str = "postgresql://localhost:5432/anima_runtime"
    runtime_database_echo: bool = False
    runtime_pool_size: int = 5
    runtime_pool_max_overflow: int = 10
    use_runtime_pg: bool = True  # Feature flag; False = fallback to soul DB
```

Environment variables:
- `ANIMA_RUNTIME_DATABASE_URL`
- `ANIMA_RUNTIME_DATABASE_ECHO`
- `ANIMA_RUNTIME_POOL_SIZE`
- `ANIMA_RUNTIME_POOL_MAX_OVERFLOW`
- `ANIMA_USE_RUNTIME_PG`

## Files to Create/Modify

### New files

| File | Purpose |
|---|---|
| `db/runtime_base.py` | `RuntimeBase` declarative base with PG-appropriate naming convention |
| `db/runtime.py` | Runtime engine, session factory, FastAPI dependency, startup migration runner |
| `models/runtime.py` | `RuntimeThread`, `RuntimeMessage`, `RuntimeRun`, `RuntimeStep`, `RuntimeBackgroundTaskRun` |
| `alembic_runtime.ini` | Alembic config pointing to runtime PG |
| `alembic_runtime/env.py` | Runtime migration environment |
| `alembic_runtime/versions/001_create_runtime_tables.py` | Initial migration creating all five tables |
| `tests/conftest_runtime.py` | Test fixtures for runtime PG sessions (can use SQLite in-memory for CI) |

### Modified files

| File | Nature of change |
|---|---|
| `config.py` | Add `runtime_database_url`, `use_runtime_pg`, pool settings |
| `models/__init__.py` | Re-export runtime models alongside soul models |
| `services/agent/persistence.py` | All functions: swap model imports to `Runtime*`, remove `ef`/`df` on content_text |
| `services/agent/sequencing.py` | Swap `AgentThread` to `RuntimeThread`, optionally upgrade to `FOR UPDATE` |
| `services/agent/conversation_search.py` | `_search_messages` uses runtime session; `_search_daily_logs` uses soul session; function signatures gain second session param |
| `services/agent/compaction.py` | All functions: swap models to `Runtime*`, remove `ef`/`df` on summary content |
| `services/agent/service.py` | Resolve runtime session at turn start, pass to persistence/compaction; pass soul session to memory functions |
| `services/agent/companion.py` | Accept `runtime_db_factory`; `ensure_history_loaded` and `warm` use runtime session |
| `services/agent/reflection.py` | Accept `runtime_db_factory` for message reads; pass soul factory for self-model writes |
| `services/agent/sleep_agent.py` | `BackgroundTaskRun` queries move to runtime session |
| `api/routes/chat.py` | Add `runtime_db` dependency; history/home endpoints use it for message queries |
| `tests/test_agent_service.py` | Use runtime session fixtures for message assertions |
| `tests/test_persistence.py` | Use runtime session fixtures |
| `tests/test_compaction.py` | Use runtime session fixtures |
| `tests/test_conversation_search.py` | Provide both session types |

## Migration Strategy

### Data migration: None required

Messages are ephemeral. The compaction system discards old messages and replaces them with summaries. There is no business requirement to preserve existing conversation history across the migration.

**Cutover plan:**

1. Deploy P2 code with `ANIMA_USE_RUNTIME_PG=false` (feature flag off). Existing behavior, all tests pass.
2. Provision PostgreSQL, set `ANIMA_RUNTIME_DATABASE_URL`. Run `ensure_runtime_database()` to create tables.
3. Flip `ANIMA_USE_RUNTIME_PG=true`. New conversations write to PG. Old SQLCipher message tables are orphaned but harmless.
4. After validation period (1 week), remove old SQLCipher message tables via a soul-DB Alembic migration that drops `agent_threads`, `agent_messages`, `agent_runs`, `agent_steps`, `background_task_runs`.

### Rollback plan

Set `ANIMA_USE_RUNTIME_PG=false`. The feature flag routes all runtime queries back to the soul SQLCipher database. Messages created in PG during the flag-on period are inaccessible but not lost (they remain in PG). This is acceptable because messages are ephemeral.

### Handling in-flight turns during cutover

The feature flag is read at session-resolution time, not at import time. A turn that starts before the flag flip will complete using whichever session it resolved at `_execute_agent_turn_locked` entry. No partial writes across databases.

## Test Plan

### Unit tests

1. **Message CRUD in PG** -- Create thread, append messages, load history. Assert rows exist in runtime DB, not in soul DB.
2. **Run/step lifecycle** -- Create run, create steps, finalize run. Assert `RuntimeRun.status == "completed"` with correct token counts.
3. **Sequence reservation** -- Concurrent calls to `reserve_message_sequences` produce non-overlapping ranges (test with threading).
4. **Conversation search** -- Insert messages in runtime DB, daily logs in soul DB. Call `search_conversation_history` with both sessions. Assert hits from both sources.
5. **Compaction** -- Insert messages in runtime DB, trigger compaction. Assert summary message created in runtime DB, old messages marked `is_in_context=False`.
6. **Approval checkpoint** -- Save checkpoint, load checkpoint, clear checkpoint. All in runtime DB.
7. **BackgroundTaskRun in PG** -- Create, update status, query by user. Assert in runtime DB.

### Integration tests

8. **Full agent turn** -- Send a message through `run_agent`. Assert user message + assistant message in runtime DB. Assert memory extraction still writes to soul DB.
9. **Streaming turn** -- Send a streaming message through `stream_agent`. Assert SSE events produced. Assert messages in runtime DB.
10. **Reflection reads from PG** -- Trigger reflection. Assert it reads conversation history from runtime DB and writes self-model updates to soul DB.
11. **Concurrent message writes** -- Two coroutines write messages to the same thread simultaneously. Assert no deadlocks and all messages persisted with correct sequences. (This is the core concurrency test that validates the PG migration.)
12. **Chat history API** -- `GET /api/chat/history` returns messages from runtime DB.
13. **Home dashboard** -- `GET /api/chat/home` returns `messageCount` from runtime DB and `memoryCount` from soul DB.

### Rollback tests

14. **Feature flag off** -- Set `ANIMA_USE_RUNTIME_PG=false`. Run full agent turn. Assert all data in soul SQLCipher DB, no PG writes.
15. **Feature flag toggle mid-session** -- Start with flag on, flip to off between turns. Assert each turn writes to the correct database.

### CI considerations

- Runtime tests can use an in-memory SQLite database with `RuntimeBase.metadata.create_all()` to avoid requiring a PG instance in CI.
- For true concurrency tests (test 11), use a real PG instance via Docker in the CI pipeline, or mark those tests as `@pytest.mark.pg_required` and skip when PG is unavailable.

## Acceptance Criteria

1. All agent turns (blocking and streaming) write threads, messages, runs, and steps to PostgreSQL when `ANIMA_USE_RUNTIME_PG=true`.
2. No runtime data (threads, messages, runs, steps) is written to the per-user SQLCipher database when the flag is on.
3. Memory extraction, self-model updates, emotional signals, session notes, and all other identity data continue to write to SQLCipher.
4. `GET /api/chat/history` returns messages from PostgreSQL.
5. `search_conversation_history` returns results from both PG (messages) and SQLCipher (daily logs).
6. Compaction (text-based and LLM-powered) operates correctly on PG-backed messages.
7. Approval checkpoint flow (save, load, clear, cancel) works with PG-backed runs and messages.
8. Setting `ANIMA_USE_RUNTIME_PG=false` restores full SQLCipher-only behavior with no PG dependency.
9. Two concurrent agent turns for the same user do not deadlock or produce duplicate sequence IDs.
10. All existing tests pass (846+) with the runtime PG path active.
11. Alembic runtime migrations run automatically on server startup and are idempotent.

## Out of Scope

- **Memory model migration** -- `MemoryItem`, `MemoryEpisode`, `MemoryClaim`, `MemoryDailyLog`, `MemoryVector` stay in SQLCipher. These move in Phase 3 (Memory Layer) after the dual-session pattern is proven.
- **Knowledge graph migration** -- `KGEntity`, `KGRelation` stay in SQLCipher. Phase 4.
- **Identity model migration** -- `User`, `UserKey`, `AgentProfile`, `SelfModelBlock`, `EmotionalSignal` are permanent soul-tier models. They never move to PG.
- **SessionNote migration** -- Stays in soul. Session notes are distilled knowledge about the current conversation, not raw messages. They are more like working memory than message history.
- **ForgetAuditLog migration** -- Stays in soul. It records identity-level audit events.
- **Connection pooling / PgBouncer** -- Operational infrastructure, not application code. Document recommended pool sizes in deployment guide.
- **Read replicas** -- Not needed for desktop single-user deployment. Can be added later for server deployments.
- **Message content encryption in PG** -- SQLCipher field-level encryption (`ef`/`df`) is removed for PG-backed messages. If at-rest encryption is required, use PostgreSQL TDE or filesystem-level encryption. The threat model for messages (ephemeral, compacted away) is different from identity data (permanent, portable).
- **Backfilling old messages from SQLCipher to PG** -- Not needed. Messages are ephemeral. Old threads are abandoned on cutover.
