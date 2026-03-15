# ANIMA Core — Implementation Plan

> Status: historical implementation brief with current-state corrections
> Created: 2026-03-14
> Completed: 2026-03-14
> Goal: Transform the current server into a portable, encrypted, memory-intelligent personal AI
> Note: this document is preserved as the execution brief that drove the migration; use `docs/memory-system.md` for the live architecture.
> Audience: Implementation agent — this document contains all context needed to execute
> See also: [docs/memory-system.md](memory-system.md) for how the memory system works

---

## Architecture Decision Summary

These reflect the current implementation more accurately than the original plan.
Current state correction:

- SQLite is the default server database, but `ANIMA_DATABASE_URL` still allows overrides.
- SQLCipher support is optional, not enforced by default.
- Structured memory now lives in SQLite tables, and the soul directive now lives in `self_model_blocks`; legacy `users/<user_id>/soul.md` files are auto-migrated on first read.
- The portable Core currently includes the database, `manifest.json`, and remaining per-user files.

The numbered list below is the historical target architecture from the original
execution brief. It is preserved for traceability, not as an exact description
of the current server.

1. **Single SQLite database** — no PostgreSQL, no Docker. The database file lives inside the Core directory.
2. **SQLCipher encryption** — the entire SQLite database is encrypted at rest. Passphrase required to open.
3. **No markdown memory files** — all memory (facts, preferences, episodes, daily logs, identity) stored in SQLite tables. The encrypted database replaces the file-based memory store.
4. **Portable Core** — the `.anima/` directory contains everything. Copy it to a USB, it works on another machine.
5. **LLM providers** — Ollama, OpenRouter (open models only), vLLM. No OpenAI, Anthropic, or Google.
6. **Single user** — one Core = one person's AI.

---

## Current Codebase Inventory

All paths relative to `apps/server/src/anima_server/`.

### Dependencies (`apps/server/pyproject.toml`)

```
alembic>=1.16.5          — DB migrations
argon2-cffi>=25.1.0      — password hashing + KDF
cryptography>=45.0.0     — AES-256-GCM encryption
fastapi>=0.115.12        — HTTP framework
httpx>=0.28.1            — HTTP client for LLM providers
jinja2>=3.1.6            — prompt templates
langchain-core>=1.2.18   — REMOVE: only used for message types + @tool decorator
pydantic-settings>=2.11.0 — config management
psycopg[binary]>=3.2.9   — REMOVE: PostgreSQL driver
sqlalchemy>=2.0.43       — ORM
uvicorn[standard]>=0.35.0 — ASGI server
```

### Files That Must Change

| File | What changes | Why |
|---|---|---|
| `config.py` | Default DB URL → SQLite, add `core_passphrase` setting | SQLite + SQLCipher |
| `pyproject.toml` | Remove `psycopg[binary]`, remove `langchain-core`, add `sqlcipher3-binary` | Dependency cleanup |
| `db/session.py` | SQLCipher PRAGMA key on connect | Encryption |
| `services/agent/messages.py` | Replace `langchain_core.messages` imports with own message classes | Remove langchain |
| `services/agent/tools.py` | Replace `langchain_core.tools.tool` decorator with own implementation | Remove langchain |
| `services/agent/memory_store.py` | Rewrite: file-based → SQLite-based memory CRUD | SQLite memory |
| `services/agent/memory_blocks.py` | Read from DB tables instead of files | SQLite memory |
| `services/agent/consolidation.py` | Write to DB tables instead of files, add LLM extraction | SQLite memory + LLM |
| `services/agent/persistence.py` | No changes needed (already SQLAlchemy) | — |
| `apps/server/src/anima_server/models/agent_runtime.py` | Add memory tables | New schema |
| `services/vault.py` | `reset_identity_sequences()` already handles non-pg; update `read_data_snapshot()`/`write_data_snapshot()` to work without user files | No more memory files |
| `services/storage.py` | Remove `get_user_data_dir()` or repurpose for non-memory use | No more memory files |
| `alembic/versions/04d82bffa29f_*.py` | Fix `server_default=sa.text('now()')` → `sa.text('CURRENT_TIMESTAMP')` | SQLite compat |

### Files That Must NOT Change

| File | Why |
|---|---|
| `runtime.py` | Loop runtime is complete and working |
| `rules.py` | Tool rules engine is complete |
| `executor.py` | Tool execution is complete |
| `streaming.py` | SSE events are complete |
| `compaction.py` | Context compaction is complete |
| `system_prompt.py` | Prompt assembly is complete |
| `adapters/openai_compatible.py` | LLM adapter is complete |
| `openai_compatible_client.py` | HTTP client is complete |
| `output_filter.py` | Reasoning trace filter is complete |
| `services/crypto.py` | Crypto primitives are complete |
| `services/auth.py` | Auth logic is complete |
| All template files (`templates/`) | Prompt templates are complete |

### Existing Test Patterns

Tests are in `apps/server/tests/`. They use:
- In-memory SQLite via `create_engine("sqlite://", ...)` with `StaticPool`
- `QueueAdapter` — a test LLM adapter that returns pre-queued `StepExecutionResult` objects
- `TestClient` from FastAPI for HTTP-level tests
- `pytest` + `pytest-asyncio` with `asyncio_mode = "auto"`
- Temporary directories for file-based operations
- Tests create their own `Session` and `Engine` — they do NOT use the production `db/session.py`

---

## Task 1: Remove PostgreSQL and LangChain Dependencies

### 1A: Remove `psycopg` and switch default to SQLite

**File: `config.py`**

Change line 5:
```python
# Before
DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5433/anima"

# After
DEFAULT_DATABASE_URL = "sqlite:///" + str(Path(__file__).resolve().parents[4] / ".anima" / "dev" / "anima.db")
```

Note: construct the path to put `anima.db` inside the existing `data_dir`. Import `Path` (already imported).

**File: `pyproject.toml`**

Remove from `dependencies`:
```
"psycopg[binary]>=3.2.9",
```

Add optional dependency group:
```toml
[dependency-groups]
postgres = ["psycopg[binary]>=3.2.9"]
```

**File: `apps/server/alembic/versions/04d82bffa29f_create_users_table.py`**

Line 30 uses `server_default=sa.text('now()')` which is PostgreSQL-only. Change to:
```python
server_default=sa.text('CURRENT_TIMESTAMP')
```

This appears twice (lines 30 and 31 for `created_at` and `updated_at`). All other migrations already use `CURRENT_TIMESTAMP`.

**File: `apps/server/src/anima_server/models/user.py`** (read this file to verify)

Check if `User` model uses `server_default=func.now()`. SQLAlchemy translates `func.now()` to `CURRENT_TIMESTAMP` for SQLite, so this should work, but verify by running the test suite.

### 1B: Remove `langchain-core`

**File: `pyproject.toml`**

Remove from `dependencies`:
```
"langchain-core>=1.2.18",
```

**File: `services/agent/messages.py`**

Replace langchain message classes with plain dataclasses. Currently imports:
```python
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
```

Create replacement message classes. These need the following attributes used by the codebase:
- `.content` (str) — used everywhere via `message_content()`
- `.type` (str) — used in `runtime.py:_snapshot_messages()` to determine role ("ai", "tool", "system", etc.)
- `.tool_calls` (list) — used on AIMessage for tool call payloads
- `.tool_call_id` (str) — used on ToolMessage
- `.name` (str | None) — used on ToolMessage
- `.content_delta` (str) — used in streaming adapter (but this is on stream chunks, not these messages)
- `.usage_metadata` (dict | None) — used in `message_usage_payload()`
- `.response_metadata` (dict | None) — used in `message_usage_payload()`
- `.tool_call_deltas` (tuple) — used in streaming adapter

Create in `messages.py` (replace the import):
```python
from dataclasses import dataclass, field

@dataclass
class SystemMessage:
    content: str
    type: str = "system"

@dataclass
class HumanMessage:
    content: str
    type: str = "human"

@dataclass
class AIMessage:
    content: str
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    usage_metadata: dict[str, object] | None = None
    response_metadata: dict[str, object] | None = None
    type: str = "ai"

@dataclass
class ToolMessage:
    content: str
    tool_call_id: str
    name: str | None = None
    type: str = "tool"
```

**File: `services/agent/tools.py`**

Replace `from langchain_core.tools import tool` with a minimal `@tool` decorator. The decorator needs to set `.name` and `.description` on the function, and provide a `.args_schema` for the OpenAI-compatible client's `_serialize_tool()`.

Check what `_serialize_tool()` in `openai_compatible_client.py` reads from tools:
- `tool.name`
- `tool.description`
- `tool.args_schema` → `.schema()` which returns a JSON Schema dict

Create a minimal replacement:
```python
from dataclasses import dataclass
import inspect
from typing import Any, Callable, get_type_hints

def tool(func: Callable[..., Any]) -> Any:
    """Minimal tool decorator replacing langchain_core.tools.tool."""
    func.name = func.__name__
    func.description = (func.__doc__ or "").strip()
    func.args_schema = _build_args_schema(func)
    return func

class _SimpleSchema:
    def __init__(self, schema: dict[str, object]) -> None:
        self._schema = schema
    def schema(self) -> dict[str, object]:
        return self._schema

def _build_args_schema(func: Callable[..., Any]) -> _SimpleSchema:
    hints = get_type_hints(func)
    params = inspect.signature(func).parameters
    properties: dict[str, object] = {}
    required: list[str] = []
    for name, param in params.items():
        if name == "return":
            continue
        prop: dict[str, str] = {"type": "string"}
        hint = hints.get(name)
        if hint is str:
            prop["type"] = "string"
        elif hint is int:
            prop["type"] = "integer"
        elif hint is float:
            prop["type"] = "number"
        elif hint is bool:
            prop["type"] = "boolean"
        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return _SimpleSchema({
        "type": "object",
        "properties": properties,
        "required": required,
    })
```

**Files that import from langchain in tests:**
- `tests/test_agent_runtime.py:8` — `from langchain_core.tools import tool`
- `tests/test_agent_openai_compatible_client.py:7` — `from langchain_core.messages import ...`

Update these imports to use the new message classes and tool decorator from the anima codebase.

### 1C: Verify OpenAI-compatible client works without langchain

The `openai_compatible_client.py` uses `_serialize_message()` and `_serialize_tool()` which inspect message/tool objects via `getattr`. Check that our replacement dataclasses provide the same attributes. The key methods:

- `_serialize_message()` (line 177) checks `.type` attribute: `"system"`, `"human"`, `"ai"`, `"tool"`
- `_serialize_tool()` (line 347) reads `.name`, `.description`, `.args_schema.schema()`
- `_serialize_content()` (line 210) reads `.content`

Our dataclasses match these patterns.

### Acceptance Criteria for Task 1

- [ ] `psycopg` removed from hard dependencies
- [ ] `langchain-core` removed from all dependencies
- [ ] Server starts with `ANIMA_DATABASE_URL` unset (uses SQLite default)
- [ ] All existing tests pass (`pytest apps/server/tests/`)
- [ ] No import of `langchain_core` anywhere in the codebase
- [ ] Alembic `upgrade head` succeeds with SQLite
- [ ] Chat flow works end-to-end with SQLite (register → login → chat → persist → reload history)

---

## Task 2: SQLCipher Integration

### 2A: Add dependency

**File: `pyproject.toml`**

Add to `dependencies`:
```
"sqlcipher3-binary>=0.5.4",
```

### 2B: Update engine creation

**File: `config.py`**

Add setting:
```python
core_passphrase: str = ""  # Set via ANIMA_CORE_PASSPHRASE env var
```

**File: `db/session.py`**

Current code:
```python
engine = create_engine(
    settings.database_url,
    echo=settings.database_echo,
    future=True,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)
```

Change to:
```python
import sqlcipher3

def _make_engine():
    url = settings.database_url

    if not url.startswith("sqlite"):
        # Non-SQLite (PostgreSQL) — legacy support
        return create_engine(url, echo=settings.database_echo, future=True, pool_pre_ping=True)

    passphrase = settings.core_passphrase.strip()

    if passphrase:
        # SQLCipher encrypted database
        from sqlalchemy import event

        engine = create_engine(
            url,
            echo=settings.database_echo,
            future=True,
            module=sqlcipher3,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def set_sqlcipher_key(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute(f"PRAGMA key = \"{passphrase}\"")
            cursor.close()

        return engine
    else:
        # Unencrypted SQLite (development / testing)
        return create_engine(
            url,
            echo=settings.database_echo,
            future=True,
            connect_args={"check_same_thread": False},
        )

engine = _make_engine()
```

**Important**: The `PRAGMA key` must be the FIRST statement executed on every new connection. The `@event.listens_for(engine, "connect")` pattern ensures this.

**Security note**: The passphrase should NOT be logged or echoed. When `database_echo=True`, SQLAlchemy logs SQL — verify that PRAGMA key is not logged, or suppress it.

### 2C: Core manifest

**New file: `services/core.py`**

```python
import json
from datetime import UTC, datetime
from pathlib import Path
from anima_server.config import settings

CORE_VERSION = 1
SCHEMA_VERSION = "1.0.0"

def get_core_dir() -> Path:
    """Return the Core directory (parent of the SQLite database file)."""
    return settings.data_dir

def get_manifest_path() -> Path:
    return get_core_dir() / "manifest.json"

def ensure_core_manifest() -> dict:
    """Create or update the Core manifest. Called at server startup."""
    path = get_manifest_path()
    now = datetime.now(UTC).isoformat()

    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["last_opened_at"] = now
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    manifest = {
        "version": CORE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at": now,
        "last_opened_at": now,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
```

**File: `main.py`**

Call `ensure_core_manifest()` at app startup:
```python
from anima_server.services.core import ensure_core_manifest

def create_app() -> FastAPI:
    ensure_core_manifest()
    # ... rest of app creation
```

### 2D: Update vault export/import

**File: `services/vault.py`**

`read_data_snapshot()` currently reads all files under `data_dir`. Once memory is in SQLite, there may be no user files (or only the manifest). Update to handle the case where no user files exist gracefully (it already returns `{}` if the directory doesn't exist, but verify it doesn't fail if the directory exists but contains no files).

`export_database_snapshot()` currently only exports `users` and `user_keys` tables. It needs to also export the new memory tables (Task 3). Add to the snapshot:
```python
def export_database_snapshot(db: Session) -> dict[str, list[dict[str, Any]]]:
    return {
        "users": [...],
        "userKeys": [...],
        "memoryItems": [serialize_memory_item(row) for row in db.scalars(select(MemoryItem)).all()],
        "memoryEpisodes": [...],
        "memoryDailyLogs": [...],
        # agent_threads, agent_messages, etc. are also needed for full Core portability
    }
```

This can be completed after Task 3 (memory tables exist).

### Acceptance Criteria for Task 2

- [ ] `sqlcipher3-binary` in dependencies
- [ ] `ANIMA_CORE_PASSPHRASE=secret123` → database encrypted, server works
- [ ] `ANIMA_CORE_PASSPHRASE` unset → database unencrypted (dev mode), server works
- [ ] Wrong passphrase → clear error on startup, not a crash
- [ ] `manifest.json` created on first run with version info
- [ ] `manifest.json` updated with `last_opened_at` on subsequent runs
- [ ] Copying `.anima/` directory + using same passphrase on another machine works
- [ ] Opening the `.db` file in a SQLite browser without passphrase shows gibberish / fails
- [ ] All tests still pass (tests use in-memory SQLite without SQLCipher, which is fine)

---

## Task 3: Memory Tables (Replace File-Based Memory)

### 3A: New database models

**File: `apps/server/src/anima_server/models/agent_runtime.py`** (append to existing file)

```python
class MemoryItem(Base):
    __tablename__ = "memory_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(24), nullable=False
    )  # fact, preference, goal, relationship
    importance: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )  # 1-5
    source: Mapped[str] = mapped_column(
        String(24), nullable=False, default="extraction"
    )  # extraction, user, reflection
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_referenced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reference_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MemoryEpisode(Base):
    __tablename__ = "memory_episodes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="SET NULL"),
        nullable=True,
    )
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    time: Mapped[str | None] = mapped_column(String(8), nullable=True)  # HH:MM:SS
    topics_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    emotional_arc: Mapped[str | None] = mapped_column(String(128), nullable=True)
    significance_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )  # 1-5
    turn_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MemoryDailyLog(Base):
    __tablename__ = "memory_daily_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_response: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
```

**File: `apps/server/src/anima_server/models/__init__.py`**

Add imports:
```python
from anima_server.models.agent_runtime import MemoryItem, MemoryEpisode, MemoryDailyLog
```

Add to `__all__`.

### 3B: Alembic migration

Create a new migration file: `apps/server/alembic/versions/20260314_0001_create_memory_tables.py`

Use `op.create_table()` for all three tables. Use `server_default=sa.text("CURRENT_TIMESTAMP")` for all datetime columns (NOT `now()`).

### 3C: Rewrite memory store

**File: `services/agent/memory_store.py`**

Complete rewrite. Replace file-based CRUD with SQLAlchemy queries. The public API that other modules use:

Current public functions used elsewhere:
- `append_daily_log_entry()` — used in `consolidation.py`
- `append_unique_bullets()` — used in `consolidation.py`
- `write_current_focus()` — used in `consolidation.py`
- `FACTS_PATH`, `PREFERENCES_PATH` — used in `consolidation.py`
- `read_memory_text()` — used internally

New public API:
```python
def add_memory_item(db: Session, *, user_id: int, content: str, category: str, importance: int = 3, source: str = "extraction") -> MemoryItem | None:
    """Add a memory item if not duplicate. Return the item if added, None if duplicate."""

def get_memory_items(db: Session, *, user_id: int, category: str | None = None, limit: int = 50, active_only: bool = True) -> list[MemoryItem]:
    """Get memory items, optionally filtered by category. active_only excludes superseded items."""

def supersede_memory_item(db: Session, *, old_item_id: int, new_content: str, importance: int | None = None) -> MemoryItem:
    """Replace an existing memory item with updated content. Sets superseded_by on old item."""

def add_daily_log(db: Session, *, user_id: int, user_message: str, assistant_response: str) -> MemoryDailyLog:
    """Append a daily log entry."""

def get_current_focus(db: Session, *, user_id: int) -> str | None:
    """Get the current focus. Stored as a memory_item with category='focus'."""

def set_current_focus(db: Session, *, user_id: int, focus: str) -> MemoryItem:
    """Set/replace the current focus."""
```

Note: `current_focus` is now just a memory item with `category="focus"` — no special file needed.

The module should NOT import `get_user_data_dir` or `Path` — no file I/O.

### 3D: Update memory blocks

**File: `services/agent/memory_blocks.py`**

Replace file-reading functions with DB queries:

```python
def build_runtime_memory_blocks(db: Session, *, user_id: int, thread_id: int) -> tuple[MemoryBlock, ...]:
    blocks = []

    human_block = build_human_memory_block(db, user_id=user_id)
    if human_block: blocks.append(human_block)

    facts_block = build_facts_memory_block(db, user_id=user_id)
    if facts_block: blocks.append(facts_block)

    preferences_block = build_preferences_memory_block(db, user_id=user_id)
    if preferences_block: blocks.append(preferences_block)

    current_focus_block = build_current_focus_memory_block(db, user_id=user_id)
    if current_focus_block: blocks.append(current_focus_block)

    summary_block = build_thread_summary_block(db, thread_id=thread_id)
    if summary_block: blocks.append(summary_block)

    return tuple(blocks)
```

New functions:
```python
def build_facts_memory_block(db: Session, *, user_id: int) -> MemoryBlock | None:
    items = get_memory_items(db, user_id=user_id, category="fact", limit=30)
    if not items: return None
    value = "\n".join(f"- {item.content}" for item in items)
    if len(value) > 2000: value = value[:2000]  # truncate
    return MemoryBlock(label="facts", description="Known facts about the user.", value=value)

def build_preferences_memory_block(db: Session, *, user_id: int) -> MemoryBlock | None:
    items = get_memory_items(db, user_id=user_id, category="preference", limit=20)
    if not items: return None
    value = "\n".join(f"- {item.content}" for item in items)
    if len(value) > 2000: value = value[:2000]
    return MemoryBlock(label="preferences", description="User preferences.", value=value)

def build_current_focus_memory_block(db: Session, *, user_id: int) -> MemoryBlock | None:
    focus = get_current_focus(db, user_id=user_id)
    if not focus: return None
    return MemoryBlock(label="current_focus", description="User's current focus.", value=focus)
```

Remove: `load_current_focus_memory()`, `strip_frontmatter()`, `is_placeholder_current_focus()`, `CURRENT_FOCUS_PATH`, `_FRONTMATTER_RE`, `_CHECKBOX_LINE_RE` — all file-based logic.

Remove import of `get_user_data_dir` and `Path`.

### 3E: Update consolidation

**File: `services/agent/consolidation.py`**

The `consolidate_turn_memory()` function currently calls:
- `append_daily_log_entry()` — change to `add_daily_log()`
- `append_unique_bullets()` — change to `add_memory_item()`
- `write_current_focus()` — change to `set_current_focus()`

The function needs a `db: Session` parameter now. Update `schedule_background_memory_consolidation()` and `run_background_memory_consolidation()` to accept and pass the session.

**Important**: The background task currently runs fire-and-forget after the response. It will need its own DB session since the request session may be closed. Create a new session inside the background task:
```python
async def run_background_memory_consolidation(...):
    from anima_server.db import SessionLocal
    with SessionLocal() as db:
        consolidate_turn_memory(db=db, ...)
        db.commit()
```

### 3F: Clean up unused files/code

After migration:
- `services/storage.py` — Remove `get_user_data_dir()` if nothing else uses it (check `api/routes/users.py` line 92 which uses it for `shutil.rmtree` on user deletion — this needs to change to delete memory rows from DB instead)
- Remove file-based constants from `memory_store.py`: `MEMORY_ROOT`, `CURRENT_FOCUS_PATH`, `FACTS_PATH`, `PREFERENCES_PATH`, `DAILY_PATH`
- Remove file I/O functions: `resolve_memory_path`, `read_memory_text`, `write_memory_text`, `append_memory_text`, `render_current_focus`, `render_daily_log_entry`, `to_blockquote`

### Acceptance Criteria for Task 3

- [ ] Three new tables created via Alembic migration
- [ ] `memory_store.py` does all CRUD via SQLAlchemy, zero file I/O
- [ ] `memory_blocks.py` loads facts, preferences, current_focus, thread_summary from DB
- [ ] `consolidation.py` writes daily logs and extracted items to DB
- [ ] Facts and preferences appear in the system prompt (verify via a test or debug log)
- [ ] No `import Path` or file operations in memory-related code
- [ ] All existing tests pass (update test mocks as needed)
- [ ] New unit tests for memory store CRUD operations

---

## Task 4: LLM-Based Memory Extraction

### 4A: Add extraction via LLM

**File: `services/agent/consolidation.py`**

Keep the existing regex extractors as a fast path. Add an LLM-based extraction function that runs after regex:

```python
EXTRACTION_PROMPT = """You are a memory extraction system for a personal AI companion.
Given a conversation turn between a user and an assistant, extract personal facts and preferences about the user.

Return a JSON array. Each item:
- "content": concise statement (e.g. "Works as a software engineer")
- "category": one of "fact", "preference", "goal", "relationship"
- "importance": 1-5 (5 = identity-defining like name/age/occupation, 1 = casual mention)

Rules:
- Only extract what the user explicitly stated or clearly implied
- Do not infer or speculate
- Do not extract information about the assistant
- Return [] if nothing worth remembering was said

User message:
{user_message}

Assistant response:
{assistant_response}"""
```

Create a function:
```python
async def extract_memories_via_llm(
    user_message: str,
    assistant_response: str,
) -> list[dict[str, Any]]:
    """Call the LLM to extract structured memories from a conversation turn."""
```

This should use the same `OpenAICompatibleChatClient` and provider config that the main chat uses. Create a simple non-streaming invocation:
```python
from anima_server.services.agent.llm import create_llm

async def extract_memories_via_llm(...):
    llm = create_llm()
    prompt = EXTRACTION_PROMPT.format(
        user_message=user_message,
        assistant_response=assistant_response,
    )
    # Build a simple HumanMessage with the prompt
    # Invoke without tools
    response = await llm.ainvoke([SystemMessage(content="You extract memories. Respond only with JSON."), HumanMessage(content=prompt)])
    # Parse JSON from response content
    # Return list of dicts, or [] on parse failure
```

### 4B: Integrate into consolidation flow

In `consolidate_turn_memory()`:
1. Run regex extraction first (fast, free)
2. Run LLM extraction (async, background)
3. Merge results: deduplicate by checking if content is similar to regex results
4. Write all unique items to `memory_items` table via `add_memory_item()`

If the LLM call fails (model unreachable, timeout, parse error), log the error and continue — regex results are still saved. The LLM extraction is best-effort.

### 4C: Config for extraction model

**File: `config.py`**

Add optional settings for using a different (cheaper/faster) model for extraction:
```python
agent_extraction_model: str = ""  # If empty, uses agent_model
agent_extraction_provider: str = ""  # If empty, uses agent_provider
```

### Acceptance Criteria for Task 4

- [ ] LLM extraction runs in background after each turn
- [ ] Both user message AND assistant response are processed
- [ ] Extracted items stored in `memory_items` with correct category and importance
- [ ] LLM failure does not break chat flow (graceful fallback to regex-only)
- [ ] Regex extraction still runs as fast path
- [ ] Duplicate items (same content) not inserted
- [ ] New test: mock LLM returns extraction JSON → verify items stored in DB

---

## Task 5: Conflict Resolution

### 5A: Detect conflicts before insert

**File: `services/agent/memory_store.py`**

Update `add_memory_item()`:

```python
def add_memory_item(db, *, user_id, content, category, importance=3, source="extraction"):
    # 1. Check for exact duplicates
    existing = get_memory_items(db, user_id=user_id, category=category)
    for item in existing:
        if _is_duplicate(item.content, content):
            return None  # Skip exact duplicate

    # 2. Check for conflicts (same topic, different info)
    candidates = [item for item in existing if _similarity(item.content, content) > 0.5]
    # If candidates found, return them for conflict resolution
    # (caller decides UPDATE vs DIFFERENT via LLM)

    # 3. Insert new item
    ...
```

### 5B: LLM conflict check

**File: `services/agent/consolidation.py`**

```python
CONFLICT_CHECK_PROMPT = """Given an EXISTING memory and a NEW memory about the same user, determine if the new one updates/replaces the existing one, or if they are about different topics.

Respond with exactly one word: UPDATE or DIFFERENT

EXISTING: {existing}
NEW: {new_content}"""
```

When a candidate conflict is found:
1. Ask LLM: UPDATE or DIFFERENT?
2. If UPDATE: call `supersede_memory_item()` — marks old as superseded, inserts new
3. If DIFFERENT: insert as new item
4. Log replacements: add a daily log entry noting the change

### 5C: String similarity

Add a simple fuzzy matching function (no external dependency needed):

```python
def _similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity. Returns 0.0-1.0."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)
```

### Acceptance Criteria for Task 5

- [ ] Exact duplicates are not inserted
- [ ] Similar items (>0.5 word overlap) trigger LLM conflict check
- [ ] UPDATE response → old item superseded, new item inserted
- [ ] DIFFERENT response → both items kept
- [ ] Superseded items have `superseded_by` set and are excluded from active queries
- [ ] Changes logged to daily log
- [ ] New test: insert "Works as engineer", then "Works as PM" → first one superseded

---

## Task 6: Episodic Memory

### 6A: Episode generation

**New file: `services/agent/episodes.py`**

```python
EPISODE_PROMPT = """Generate an episodic memory summary for this conversation between a user and their AI companion.

Output JSON:
{
  "topics": ["topic1", "topic2"],
  "summary": "2-3 sentence summary of what happened",
  "emotional_arc": "brief emotional description (e.g. 'frustrated then relieved')",
  "significance_score": 3
}

Focus on: What was the user trying to accomplish? How did they feel? What's worth remembering?
Return null if the conversation was too brief or trivial."""
```

```python
async def maybe_generate_episode(
    db: Session,
    *,
    user_id: int,
    thread_id: int,
) -> MemoryEpisode | None:
    """Generate an episode if the conversation had 3+ user turns since last episode."""
```

This function:
1. Count user messages in the thread since the last episode (or all time if no episodes)
2. If < 3 user turns, return None
3. Load recent messages from thread
4. Call LLM with episode prompt
5. Parse response, create `MemoryEpisode` row
6. Return the episode

### 6B: Wire into consolidation

In `schedule_background_memory_consolidation()`, after turn memory extraction, also call `maybe_generate_episode()`.

### 6C: Episodes memory block

**File: `services/agent/memory_blocks.py`**

```python
def build_episodes_memory_block(db: Session, *, user_id: int) -> MemoryBlock | None:
    episodes = db.scalars(
        select(MemoryEpisode)
        .where(MemoryEpisode.user_id == user_id)
        .order_by(MemoryEpisode.created_at.desc())
        .limit(5)
    ).all()
    if not episodes: return None
    lines = []
    for ep in reversed(episodes):  # chronological order
        topics = ", ".join(ep.topics_json or [])
        lines.append(f"- {ep.date}: {ep.summary} (Topics: {topics})")
    return MemoryBlock(
        label="recent_episodes",
        description="Recent conversation experiences with the user.",
        value="\n".join(lines),
    )
```

Add to `build_runtime_memory_blocks()`.

### Acceptance Criteria for Task 6

- [ ] Episodes generated after 3+ user turn conversations
- [ ] Trivial conversations (< 3 turns) don't generate episodes
- [ ] Episodes stored in `memory_episodes` table
- [ ] Last 5 episodes appear in system prompt as `<recent_episodes>` block
- [ ] Episode generation failure doesn't break chat flow
- [ ] New test: mock conversation with 5 turns → episode generated with correct fields

---

## Task 7: Sleep-Time Quick Reflection

### 7A: Inactivity timer

**New file: `services/agent/reflection.py`**

```python
import asyncio
from datetime import UTC, datetime, timedelta

_reflection_timers: dict[int, asyncio.TimerHandle] = {}
REFLECTION_DELAY_SECONDS = 300  # 5 minutes

def schedule_reflection(user_id: int, thread_id: int) -> None:
    """Schedule a reflection to run after 5 minutes of inactivity."""
    cancel_reflection(user_id)
    loop = asyncio.get_running_loop()
    handle = loop.call_later(
        REFLECTION_DELAY_SECONDS,
        lambda: loop.create_task(run_reflection(user_id, thread_id)),
    )
    _reflection_timers[user_id] = handle

def cancel_reflection(user_id: int) -> None:
    handle = _reflection_timers.pop(user_id, None)
    if handle is not None:
        handle.cancel()
```

### 7B: Reflection logic

```python
async def run_reflection(user_id: int, thread_id: int) -> None:
    """Run post-conversation reflection. Generates better episodes and scans for contradictions."""
    from anima_server.db import SessionLocal

    with SessionLocal() as db:
        # 1. Generate episode for the full conversation (better quality than per-turn)
        await maybe_generate_episode(db, user_id=user_id, thread_id=thread_id)

        # 2. Scan for contradictions across all active facts
        await scan_contradictions(db, user_id=user_id)

        db.commit()
```

### 7C: Wire into service

**File: `services/agent/service.py`**

After `schedule_background_memory_consolidation()`, add:
```python
from anima_server.services.agent.reflection import schedule_reflection
schedule_reflection(user_id=user_id, thread_id=thread.id)
```

Each new message reschedules the timer (cancels previous, starts new 5-minute countdown).

### Acceptance Criteria for Task 7

- [ ] Reflection fires ~5 minutes after last message
- [ ] New message during wait cancels and restarts the timer
- [ ] Reflection generates episode from full conversation
- [ ] Reflection scans for contradictions
- [ ] Reflection errors are logged, not raised
- [ ] New test: simulate inactivity → verify reflection fires

---

## Execution Order and Dependencies

```
Task 1 (Remove pg + langchain)     ← DO FIRST, unblocks everything
    ↓
Task 2 (SQLCipher + manifest)      ← depends on Task 1 (SQLite is default)
    ↓
Task 3 (Memory tables + rewire)    ← depends on Task 1 (SQLite working)
    ↓
Task 4 (LLM extraction)            ← depends on Task 3 (memory tables exist)
    ↓
Task 5 (Conflict resolution)       ← depends on Task 4 (LLM pipeline exists)
    ↓
Task 6 (Episodes)                  ← depends on Task 3 + 4
    ↓
Task 7 (Reflection)                ← depends on Task 6 (episodes to generate)
```

Tasks 2 and 3 can be parallelized after Task 1 is complete.
Tasks 5 and 6 can be parallelized after Task 4 is complete.

---

## The Core After All Tasks

```
.anima/
    manifest.json       ← 50 bytes, unencrypted, version + timestamps
    anima.db            ← SQLCipher encrypted, contains EVERYTHING:
                           - users, user_keys (auth)
                           - agent_threads, agent_messages, agent_runs, agent_steps (conversation)
                           - memory_items (facts, preferences, goals, relationships, focus)
                           - memory_episodes (shared experiences)
                           - memory_daily_logs (conversation logs)
```

Two files. One passphrase. Portable. Encrypted. The AI's entire being.
