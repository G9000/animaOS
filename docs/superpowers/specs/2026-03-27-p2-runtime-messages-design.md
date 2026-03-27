# P2: Runtime Messages — Design Spec

**Date**: 2026-03-27
**Status**: Approved
**Parent PRD**: [P2-runtime-messages.md](../../prds/three-tier-architecture/P2-runtime-messages.md)
**Depends on**: P1 (Embedded PostgreSQL) — merged

---

## Decisions (departures from PRD)

| Decision | PRD said | We're doing | Why |
|---|---|---|---|
| Session type | Async (`AsyncSession` via asyncpg) | **Sync** (`Session` via psycopg2) | Entire service layer is sync. Async conversion belongs in P7 (Concurrency Refactor). Avoids rewiring background tasks, companion, tool context, and test fixtures. |
| Feature flag | `ANIMA_USE_RUNTIME_PG` toggle with SQLCipher fallback | **No flag** | Desktop app with embedded PG. No deployment scenario needs fallback. If PG doesn't work, we fix it before shipping. One code path, less maintenance. |
| Column types | Generic SQLAlchemy types for portability | **PG-specific** (`TIMESTAMPTZ`, `postgresql.JSON`) | Runtime is PG-only. Tests use embedded PG. No SQLite fallback needed. |

Everything else follows the PRD as written.

---

## 1. Runtime Models (`models/runtime.py`)

Five models inheriting from `RuntimeBase` (from P1). PG-specific types. `runtime_` table prefix. ForeignKeys within the runtime DB are enforced. No cross-DB FKs to soul tables (`user_id` is a plain indexed `BigInteger`).

```python
from sqlalchemy.dialects.postgresql import JSON, TIMESTAMPTZ

class RuntimeThread(RuntimeBase):
    __tablename__ = "runtime_threads"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    last_message_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    next_message_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    # relationships: messages, runs

class RuntimeRun(RuntimeBase):
    __tablename__ = "runtime_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runtime_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_approval_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    # relationships: thread, steps

class RuntimeStep(RuntimeBase):
    __tablename__ = "runtime_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_index", name="uq_runtime_steps_run_step"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runtime_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    tool_calls_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    # relationship: run

class RuntimeMessage(RuntimeBase):
    __tablename__ = "runtime_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence_id", name="uq_runtime_messages_thread_seq"),
        Index("ix_runtime_messages_user_created", "user_id", "created_at"),
        Index("ix_runtime_messages_thread_context", "thread_id", "is_in_context"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runtime_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    step_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    sequence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # NO ef()/df()
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_args_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_in_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    # relationship: thread

class RuntimeBackgroundTaskRun(RuntimeBase):
    __tablename__ = "runtime_background_task_runs"
    __table_args__ = (Index("ix_runtime_bg_task_user_status", "user_id", "status"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
```

---

## 2. Runtime DB Infrastructure (`db/runtime.py`)

Replace the async engine from P1 with a sync engine.

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_runtime_engine: Engine | None = None
_runtime_session_factory: sessionmaker[Session] | None = None

def init_runtime_engine(database_url: str, *, echo: bool = False) -> None:
    """Initialize the Runtime store sync engine (psycopg2)."""
    global _runtime_engine, _runtime_session_factory
    # Convert asyncpg URL to psycopg2 if needed
    sync_url = database_url.replace("+asyncpg", "+psycopg2")
    if "://" in sync_url and "+psycopg2" not in sync_url:
        sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://")
    _runtime_engine = create_engine(
        sync_url, echo=echo, pool_pre_ping=True,
        pool_size=5, max_overflow=10,
    )
    _runtime_session_factory = sessionmaker(
        bind=_runtime_engine, expire_on_commit=False,
    )

def dispose_runtime_engine() -> None:
    global _runtime_engine, _runtime_session_factory
    if _runtime_engine is not None:
        _runtime_engine.dispose()
        _runtime_engine = None
        _runtime_session_factory = None

def get_runtime_engine() -> Engine:
    if _runtime_engine is None:
        raise RuntimeError("Runtime engine not initialized.")
    return _runtime_engine

def get_runtime_session_factory() -> sessionmaker[Session]:
    if _runtime_session_factory is None:
        raise RuntimeError("Runtime session factory not initialized.")
    return _runtime_session_factory

def get_runtime_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a sync PG session."""
    factory = get_runtime_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def ensure_runtime_tables() -> None:
    """Run Alembic runtime migrations on startup."""
    ...
```

**Lifespan changes** (`main.py`):
- `init_runtime_engine()` becomes sync (no `await`)
- `dispose_runtime_engine()` becomes sync
- `ensure_runtime_tables()` called after engine init
- `pg_lifecycle.py` URL converter updated to produce `postgresql+psycopg2://` URLs

**Config changes** (`config.py`):
- Remove `runtime_database_url` empty-string default → compute from embedded PG
- Add `runtime_pool_size: int = 5`, `runtime_pool_max_overflow: int = 10`
- Remove `use_runtime_pg` (no feature flag)

**Dependencies** (`pyproject.toml`):
- Add `psycopg2-binary>=2.9.0` (sync PG driver)
- `asyncpg` can stay for P7 or be removed

---

## 3. Session Pattern — Dual-Session Wiring

### 3a. API Routes (`api/routes/chat.py`)

Every endpoint that touches messages gets a second dependency:

```python
from anima_server.db.runtime import get_runtime_db

@router.post("")
async def send_message(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),                    # soul
    runtime_db: Session = Depends(get_runtime_db),    # runtime
) -> ChatResponse | StreamingResponse:
    result = await run_agent(user_message, user_id, db, runtime_db, ...)
```

Endpoints that only touch soul data (e.g., `/brief`, `/nudges`) keep the single `db` dependency.

Endpoints that only touch runtime data (e.g., `/history`) switch to `runtime_db` only (plus soul `db` if they need `df()` for legacy — but we're dropping `ef()`/`df()` for messages).

### 3b. WebSocket (`api/routes/ws.py`)

The WebSocket handler creates its own sessions. After P2, it also creates a runtime session:

```python
soul_db = get_user_session_factory(conn.user_id)()
runtime_db = get_runtime_session_factory()()
try:
    async for event in stream_agent(text, conn.user_id, soul_db, runtime_db, ...):
        ...
finally:
    runtime_db.close()
    soul_db.close()
```

### 3c. Service Layer (`service.py`)

Every function in the call chain gets `runtime_db: Session` added:

| Function | soul_db usage | runtime_db usage |
|---|---|---|
| `run_agent` | Pass through | Pass through |
| `_execute_agent_turn` | Pass through | Pass through |
| `_execute_agent_turn_locked` | Memory blocks, consolidation | Thread, run, messages, compaction |
| `_prepare_turn_context` | Memory blocks | Thread, run, user msg, sequences |
| `_invoke_turn_runtime` | Memory refresh | Tool context, error handling |
| `_persist_turn_result` | — | Persist result, compaction |
| `_proactive_compact_if_needed` | — | Compaction |
| `approve_or_deny_turn` | Memory blocks | Checkpoint load/clear, messages |
| `stream_agent` | Pass through | Pass through |
| `list_agent_history` | — | Query messages |
| `reset_agent_thread` | — | Reset thread |
| `cancel_agent_run` | — | Cancel run |
| `dry_run_agent` | Memory blocks | Thread lookup, history |

### 3d. ToolContext (`tool_context.py`)

```python
@dataclass(slots=True)
class ToolContext:
    db: Session          # soul — core_memory_append, save_to_memory, note_to_self, etc.
    runtime_db: Session  # runtime — recall_conversation, etc.
    user_id: int
    thread_id: int
    memory_modified: bool = False
```

Set in `_invoke_turn_runtime`:
```python
set_tool_context(ToolContext(db=db, runtime_db=runtime_db, user_id=user_id, thread_id=thread.id))
```

### 3e. Companion (`companion.py`)

`AnimaCompanion` methods that load history switch to runtime session:

```python
def ensure_history_loaded(self, runtime_db: Session) -> list[StoredMessage]:
    ...  # calls load_thread_history(runtime_db, ...)

def ensure_memory_loaded(self, db: Session) -> tuple[MemoryBlock, ...]:
    ...  # soul session, unchanged

def warm(self, db: Session, runtime_db: Session) -> None:
    self.ensure_memory_loaded(db)
    self.ensure_history_loaded(runtime_db)
```

### 3f. Background Tasks

`_run_post_turn_hooks` passes two factories:

```python
def _run_post_turn_hooks(
    *,
    user_id: int,
    thread_id: int,
    user_message: str,
    result: AgentResult,
    db_factory: Callable[[], Session],              # soul
    runtime_db_factory: Callable[[], Session],      # runtime
) -> None:
    schedule_background_memory_consolidation(
        user_id=user_id,
        ...,
        db_factory=db_factory,                      # writes to soul
    )
    schedule_reflection(
        user_id=user_id,
        thread_id=thread_id,
        db_factory=db_factory,                      # writes to soul
        runtime_db_factory=runtime_db_factory,      # reads messages
    )
```

`_build_db_factory` pattern extended:
```python
def _build_runtime_db_factory() -> Callable[[], Session]:
    return get_runtime_session_factory()
```

Background tasks that read messages (reflection, inner monologue) use `runtime_db_factory()`. Those that write soul data (consolidation, self-model) use `db_factory()`. `sleep_agent.py` uses `runtime_db_factory` for `BackgroundTaskRun` CRUD.

---

## 4. Persistence Layer (`persistence.py`)

All functions stay `def`. Changes:

1. **Model imports**: `AgentThread` → `RuntimeThread`, `AgentMessage` → `RuntimeMessage`, etc.
2. **Remove `ef()`/`df()`** on `content_text`
3. **No signature change** — callers pass the runtime session as `db: Session`

The parameter name stays `db: Session` in persistence functions (they don't know or care which backend). The caller is responsible for passing the right session.

### Encryption removal

```python
# Before (append_message)
content_text=ef(uid, content_text, table="agent_messages", field="content_text")
# After
content_text=content_text

# Before (load_thread_history)
content = df(uid, row.content_text or "", table="agent_messages", field="content_text")
# After
content = row.content_text or ""
```

Same removal in `compaction.py` for summary messages.

---

## 5. Sequencing (`sequencing.py`)

Swap `AgentThread` → `RuntimeThread`. Upgrade CAS loop to `SELECT ... FOR UPDATE`:

```python
def reserve_message_sequences(db: Session, *, thread_id: int, count: int) -> int:
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

---

## 6. Conversation Search (`conversation_search.py`)

`search_conversation_history` takes two sessions:

```python
def search_conversation_history(
    runtime_db: Session,    # for message search
    soul_db: Session,       # for daily log search
    *,
    user_id: int,
    query: str,
    ...
) -> list[ConversationHit]:
    hits = _search_messages(runtime_db, ...)    # RuntimeMessage, RuntimeThread
    hits += _search_daily_logs(soul_db, ...)    # MemoryDailyLog (soul, df() stays)
    return sorted(hits, ...)
```

`_search_messages` swaps models. `_search_daily_logs` unchanged (soul data, `df()` stays).

---

## 7. Compaction (`compaction.py`)

Swap models, remove `ef()`/`df()` on message content. All functions stay `def`, take `db: Session` (runtime session passed by caller).

`compact_thread_context_with_llm` is `async def` already (calls LLM). It takes `db: Session` which is the runtime session — sync PG session works fine in async context.

---

## 8. Alembic Runtime

```
apps/server/
  alembic.ini                 # existing (soul)
  alembic/                    # existing (soul migrations)
  alembic_runtime.ini         # new
  alembic_runtime/            # new
    env.py                    # imports RuntimeBase.metadata, connects to PG
    script.py.mako
    versions/
      001_create_runtime_tables.py
```

`ensure_runtime_tables()` runs `alembic upgrade head` programmatically against the PG engine on startup, same pattern as `_run_alembic_upgrade()` for soul DB.

---

## 9. Files to Create

| File | Purpose |
|---|---|
| `models/runtime.py` | 5 runtime model classes |
| `alembic_runtime.ini` | Alembic config for runtime PG |
| `alembic_runtime/env.py` | Runtime migration environment |
| `alembic_runtime/script.py.mako` | Migration template |
| `alembic_runtime/versions/001_create_runtime_tables.py` | Initial migration |
| `tests/conftest_runtime.py` | Runtime PG test fixtures |

## 10. Files to Modify

| File | Change |
|---|---|
| `db/runtime.py` | Replace async engine with sync engine, add `get_runtime_db` dependency, add `ensure_runtime_tables()` |
| `db/__init__.py` | Export new runtime session functions |
| `config.py` | Add pool settings, remove `use_runtime_pg` if present |
| `main.py` | Update lifespan to sync engine init, call `ensure_runtime_tables()` |
| `db/pg_lifecycle.py` | URL converter produces `postgresql+psycopg2://` |
| `models/__init__.py` | Re-export runtime models |
| `services/agent/persistence.py` | Swap models, remove ef/df |
| `services/agent/sequencing.py` | Swap model, upgrade to FOR UPDATE |
| `services/agent/compaction.py` | Swap models, remove ef/df |
| `services/agent/conversation_search.py` | Dual-session signature, swap models in message search |
| `services/agent/service.py` | Thread `runtime_db` through all functions |
| `services/agent/companion.py` | `ensure_history_loaded` takes runtime session |
| `services/agent/reflection.py` | Accept `runtime_db_factory` |
| `services/agent/sleep_agent.py` | BackgroundTaskRun queries use runtime session |
| `services/agent/tool_context.py` | Add `runtime_db` field |
| `api/routes/chat.py` | Add `runtime_db` dependency, rewire endpoints |
| `api/routes/ws.py` | Create runtime session alongside soul session |
| `pyproject.toml` | Add `psycopg2-binary>=2.9.0` |
| `tests/test_agent_service.py` | Runtime session fixtures |
| `tests/test_persistence.py` | Runtime session fixtures |
| `tests/test_compaction.py` | Runtime session fixtures |
| `tests/test_conversation_search.py` | Dual-session fixtures |

---

## 11. Migration Strategy

No data migration. Messages are ephemeral. Clean cutover: new messages go to PG, old SQLCipher message tables become dead code. Old tables are dropped in a future soul-DB Alembic migration after a validation period.

---

## 12. Test Plan

1. **Message CRUD in PG** — thread, messages, runs, steps lifecycle
2. **Sequence reservation** — `FOR UPDATE` correctness
3. **Conversation search** — dual-session, results from both PG and soul
4. **Compaction** — operates on PG-backed messages
5. **Approval checkpoint** — save/load/clear in PG
6. **BackgroundTaskRun** — CRUD in PG
7. **Full agent turn** — messages in PG, memory in soul
8. **Streaming turn** — SSE events, messages in PG
9. **Chat history API** — returns from PG
10. **Home dashboard** — messageCount from PG, memoryCount from soul
11. **Concurrent writes** — two coroutines writing simultaneously, no deadlocks
