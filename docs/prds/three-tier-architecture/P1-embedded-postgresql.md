---
title: "Phase 1: Embedded PostgreSQL Infrastructure"
description: Add embedded PostgreSQL as the Runtime store alongside the existing SQLCipher Soul store, establishing dual-engine infrastructure for the three-tier cognitive architecture
category: prd
version: "1.0"
---

# Phase 1: Embedded PostgreSQL Infrastructure

**Version**: 1.0
**Date**: 2026-03-26
**Status**: Draft
**Priority**: P0 -- Foundation
**Depends on**: None (first phase)
**Blocks**: P2 (Runtime Messages), P3 (Self-Model Split), all subsequent phases
**Parent PRD**: [Three-Tier Cognitive Architecture + N-Agent Spawning](../three-tier-architecture.md)
**Design Spec**: [Three-Store + N-Agent Spawning Design](../../superpowers/specs/2026-03-26-three-store-n-agent-spawning-design.md)

---

## 1. Overview

This phase adds the physical infrastructure for the Runtime tier: an embedded PostgreSQL instance managed by the Python server process. After this phase, the server boots with two database engines -- the existing synchronous SQLCipher engine for the Soul store and a new asynchronous PostgreSQL engine for the Runtime store. No application data moves in this phase; it is purely infrastructure.

The goal is to have a working, tested dual-engine setup that subsequent phases can build on without worrying about connection management, lifecycle, or crash recovery.

---

## 2. Problem Statement

### Current State

The server runs a single SQLAlchemy engine (`db/session.py`) backed by SQLite/SQLCipher. All data -- identity, messages, threads, runs, memories, emotions, intentions -- lives in `anima.db`. This engine is synchronous and uses `check_same_thread=False` for FastAPI compatibility.

### Why This Phase Exists

Before any data can move to PostgreSQL (P2-P8), the infrastructure must exist:

- An embedded PostgreSQL process that starts and stops with the server
- An async SQLAlchemy engine and session factory for PostgreSQL
- Crash recovery for stale PostgreSQL lockfiles
- Configuration plumbing for the new database URL
- CI fallback for environments where embedded PG is unavailable
- A separate `DeclarativeBase` for runtime models (distinct metadata namespace)

Without this foundation, every subsequent phase would need to solve infrastructure problems alongside application logic.

---

## 3. Goals and Non-Goals

### Goals

1. Embedded PostgreSQL starts automatically when the server boots (no Docker, no manual install)
2. PostgreSQL process stops cleanly on server shutdown (`atexit` + lifespan)
3. Stale lockfile recovery handles crash-restart scenarios
4. Async SQLAlchemy engine (`asyncpg`) is available via `get_runtime_session()`
5. Existing `get_db()` and all SQLCipher code paths remain completely unchanged
6. Configuration auto-derives `runtime_database_url` from the embedded PG instance
7. CI environments can override with `ANIMA_RUNTIME_DATABASE_URL` pointing to Docker PG
8. All 846+ existing tests continue to pass without modification

### Non-Goals

- Moving any existing data to PostgreSQL (that is P2)
- Creating runtime models or tables (that is P2+)
- Changing the Soul store or encryption layer
- Supporting multi-user or remote PostgreSQL deployments
- Adding Alembic migration support for the runtime database (deferred to P2, when the first runtime models are created)

---

## 4. Scope

### In Scope

| Component | Description |
|-----------|-------------|
| `pgserver` integration | Python dependency for embedded PostgreSQL |
| PG lifecycle management | Start, stop, health check, crash recovery |
| Async engine + sessions | SQLAlchemy async engine with `asyncpg` driver |
| Runtime Base class | Separate `DeclarativeBase` for runtime models |
| FastAPI lifespan | Server startup/shutdown hooks for PG lifecycle |
| Config additions | New settings for runtime DB, spawn limits, TTL |
| Test infrastructure | Tests for lifecycle, sessions, dual-engine coexistence |

### Out of Scope

| Component | Phase |
|-----------|-------|
| Runtime table definitions | P2 |
| Data migration from SQLite to PG | P2 |
| Alembic for runtime DB | P2 |
| pgvector extension | P6 |
| Connection pooling tuning | P7 |

---

## 5. Implementation Details

### 5.1 Embedded PostgreSQL Lifecycle

The `EmbeddedPG` class manages the PostgreSQL server process. It wraps `pgserver` to provide start/stop/recovery semantics appropriate for an application-embedded database.

```python
# db/pg_lifecycle.py

import atexit
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class EmbeddedPG:
    """Manages an embedded PostgreSQL instance.

    The PG data directory lives at `.anima/runtime/pg_data/`.
    The process starts with the Python server and stops on shutdown.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._server: pgserver.PostgresServer | None = None
        self._started = False

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def running(self) -> bool:
        return self._started and self._server is not None

    @property
    def database_url(self) -> str:
        """Return asyncpg connection URL for the running instance."""
        if not self.running:
            raise RuntimeError("Embedded PG is not running")
        # pgserver exposes a psycopg2 URI; convert to asyncpg
        return self._to_asyncpg_url(self._server.get_uri())

    def start(self) -> None:
        """Start the embedded PG instance.

        Handles stale lockfile recovery: if a previous server
        crashed and left a lockfile, pgserver detects this and
        cleans up automatically on startup.
        """
        ...

    def stop(self) -> None:
        """Stop the embedded PG instance cleanly."""
        ...

    def _recover_stale_lockfile(self) -> None:
        """Detect and remove stale postmaster.pid if the owning
        process is no longer running."""
        ...

    @staticmethod
    def _to_asyncpg_url(psycopg_url: str) -> str:
        """Convert pgserver's psycopg2 URL to asyncpg format.

        pgserver returns: postgresql://user:pass@host:port/db
        asyncpg needs:    postgresql+asyncpg://user:pass@host:port/db
        """
        ...
```

**Lifecycle rules:**

1. `start()` is called from the FastAPI lifespan (before the app accepts requests)
2. `stop()` is called from both the FastAPI lifespan shutdown and an `atexit` handler
3. `stop()` is idempotent -- calling it multiple times is safe
4. If `start()` finds a stale `postmaster.pid` whose owning PID no longer exists, it removes the lockfile and proceeds with startup
5. The data directory is created if it does not exist (`parents=True, exist_ok=True`)

**Stale lockfile recovery logic:**

```python
def _recover_stale_lockfile(self) -> None:
    pid_file = self._data_dir / "postmaster.pid"
    if not pid_file.exists():
        return

    try:
        pid = int(pid_file.read_text().splitlines()[0])
    except (ValueError, IndexError):
        logger.warning("Malformed postmaster.pid, removing")
        pid_file.unlink(missing_ok=True)
        return

    # Check if the PID is still alive
    import os
    import signal
    try:
        os.kill(pid, 0)  # signal 0 = existence check
    except OSError:
        logger.warning(
            "Stale postmaster.pid found (PID %d not running), removing",
            pid,
        )
        pid_file.unlink(missing_ok=True)
```

**Windows compatibility note:** `os.kill(pid, 0)` works on Windows for checking process existence (Python 3.12+). The `signal` module is not used for actual signaling on Windows; only the zero-signal existence check is needed here.

### 5.2 Async Engine and Session Factory

```python
# db/runtime.py

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Module-level state (initialized by init_runtime_engine)
_runtime_engine: AsyncEngine | None = None
_runtime_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_runtime_engine(database_url: str, *, echo: bool = False) -> None:
    """Initialize the async engine for the Runtime store.

    Called once during server startup, after embedded PG is running.
    """
    global _runtime_engine, _runtime_session_factory

    _runtime_engine = create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
    _runtime_session_factory = async_sessionmaker(
        bind=_runtime_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def dispose_runtime_engine() -> None:
    """Dispose the async engine. Called during server shutdown."""
    global _runtime_engine, _runtime_session_factory
    if _runtime_engine is not None:
        await _runtime_engine.dispose()
        _runtime_engine = None
        _runtime_session_factory = None


def get_runtime_engine() -> AsyncEngine:
    """Return the runtime async engine. Raises if not initialized."""
    if _runtime_engine is None:
        raise RuntimeError(
            "Runtime engine not initialized. "
            "Call init_runtime_engine() during server startup."
        )
    return _runtime_engine


@asynccontextmanager
async def get_runtime_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session for the Runtime store.

    Usage:
        async with get_runtime_session() as session:
            result = await session.execute(...)
    """
    if _runtime_session_factory is None:
        raise RuntimeError("Runtime session factory not initialized.")
    async with _runtime_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

**Design decisions:**

- `pool_size=5, max_overflow=10` is conservative. A single-user local app with N-agent spawns (default max 10) needs at most ~12 concurrent connections. This will be revisited in P7 when actual concurrency patterns are measured.
- `expire_on_commit=False` matches the existing SQLCipher session factory behavior.
- Auto-commit-on-exit simplifies caller code. Callers that need explicit transaction control can use `session.begin()`.

### 5.3 Runtime Base

```python
# db/runtime_base.py

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

RUNTIME_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class RuntimeBase(DeclarativeBase):
    """Base class for all Runtime (PostgreSQL) models.

    Uses a separate MetaData instance from the Soul store's Base,
    ensuring that runtime models and soul models have independent
    table namespaces and migration tracking.
    """
    metadata = MetaData(naming_convention=RUNTIME_NAMING_CONVENTION)
```

The naming convention mirrors `db/base.py` for consistency. The separate `MetaData` instance is critical: it prevents Alembic (when added in P2) from seeing soul models in runtime migrations and vice versa.

### 5.4 FastAPI Lifespan Integration

The current `main.py` uses `@app.on_event("shutdown")` for cleanup. This phase migrates to the `lifespan` context manager pattern (the modern FastAPI approach), which provides both startup and shutdown hooks in a single function.

```python
# Changes to main.py

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from .db.pg_lifecycle import EmbeddedPG
from .db.runtime import init_runtime_engine, dispose_runtime_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # --- Startup ---
    embedded_pg = _start_embedded_pg()
    if embedded_pg is not None:
        runtime_url = embedded_pg.database_url
    else:
        # Explicit URL provided (CI, or Docker fallback)
        runtime_url = settings.runtime_database_url

    if runtime_url:
        init_runtime_engine(runtime_url, echo=settings.database_echo)

    yield

    # --- Shutdown ---
    from .services.agent.consolidation import drain_background_memory_tasks
    from .services.agent.reflection import cancel_pending_reflection

    await cancel_pending_reflection()
    await drain_background_memory_tasks()
    await dispose_runtime_engine()

    if embedded_pg is not None:
        embedded_pg.stop()


def _start_embedded_pg() -> EmbeddedPG | None:
    """Start embedded PG unless an explicit runtime URL is configured."""
    if settings.runtime_database_url:
        # Explicit URL means external PG (CI Docker, or user preference)
        return None

    pg_data_dir = Path(settings.runtime_pg_data_dir) if settings.runtime_pg_data_dir else (
        settings.data_dir / "runtime" / "pg_data"
    )

    pg = EmbeddedPG(data_dir=pg_data_dir)
    pg.start()
    return pg
```

The existing `@app.on_event("shutdown")` handler is removed; its logic moves into the `lifespan` shutdown block.

### 5.5 Configuration Additions

New fields added to the `Settings` class in `config.py`:

```python
class Settings(BaseSettings):
    # ... existing fields unchanged ...

    # --- Runtime store (Phase 1) ---
    runtime_database_url: str = ""
    runtime_pg_data_dir: str = ""

    # --- N-Agent spawning (infrastructure, used in P7/P8) ---
    agent_max_concurrent_spawns: int = 10
    agent_spawn_timeout: float = 300.0
    agent_spawn_max_steps: int = 4

    # --- Data lifecycle (used in P2/P5) ---
    message_ttl_days: int = 30
    consolidation_health_threshold_minutes: int = 30
```

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `runtime_database_url` | `ANIMA_RUNTIME_DATABASE_URL` | `""` (empty = use embedded PG) | Async PostgreSQL URL. When set, embedded PG is not started. |
| `runtime_pg_data_dir` | `ANIMA_RUNTIME_PG_DATA_DIR` | `""` (defaults to `data_dir/runtime/pg_data`) | Directory for embedded PG data files. |
| `agent_max_concurrent_spawns` | `ANIMA_AGENT_MAX_CONCURRENT_SPAWNS` | `10` | Maximum concurrent spawn agents (LLM semaphore size). |
| `agent_spawn_timeout` | `ANIMA_AGENT_SPAWN_TIMEOUT` | `300.0` | Per-spawn timeout in seconds. |
| `agent_spawn_max_steps` | `ANIMA_AGENT_SPAWN_MAX_STEPS` | `4` | Maximum steps a spawn can execute. |
| `message_ttl_days` | `ANIMA_MESSAGE_TTL_DAYS` | `30` | Runtime messages older than this are eligible for pruning. |
| `consolidation_health_threshold_minutes` | `ANIMA_CONSOLIDATION_HEALTH_THRESHOLD_MINUTES` | `30` | Alert if no consolidation runs in this window. |

**Note on forward-declared settings:** `agent_max_concurrent_spawns`, `agent_spawn_timeout`, `agent_spawn_max_steps`, `message_ttl_days`, and `consolidation_health_threshold_minutes` are defined in P1 so the configuration schema is stable for all subsequent phases. They are not consumed by any code in this phase.

---

## 6. Files to Create

| File | Purpose |
|------|---------|
| `apps/server/src/anima_server/db/pg_lifecycle.py` | `EmbeddedPG` class managing PG start/stop/recovery |
| `apps/server/src/anima_server/db/runtime.py` | Async engine, session factory, `get_runtime_session()` |
| `apps/server/src/anima_server/db/runtime_base.py` | `RuntimeBase` declarative base for runtime models |
| `apps/server/tests/test_runtime_db.py` | Tests for embedded PG lifecycle and session management |

## 7. Files to Modify

| File | Change |
|------|--------|
| `apps/server/src/anima_server/config.py` | Add 7 new settings fields (see Section 5.5) |
| `apps/server/src/anima_server/main.py` | Replace `@app.on_event("shutdown")` with `lifespan` context manager; add PG startup/shutdown |
| `apps/server/src/anima_server/db/__init__.py` | Export `RuntimeBase`, `get_runtime_session`, `get_runtime_engine` |
| `apps/server/pyproject.toml` | Add `pgserver` to dependencies; move `psycopg` from dev group to main dependencies; add `asyncpg` |

---

## 8. Models / Schemas

No new database models are created in this phase. The `RuntimeBase` class is defined but has no subclasses until P2.

### RuntimeBase (empty in P1)

| Attribute | Value |
|-----------|-------|
| Class | `RuntimeBase(DeclarativeBase)` |
| Metadata | Separate `MetaData` instance with same naming convention as `Base` |
| Tables | None in P1 |

### Configuration Schema (Pydantic)

| Field | Type | Default | Consumed By |
|-------|------|---------|-------------|
| `runtime_database_url` | `str` | `""` | P1: `_start_embedded_pg()`, `init_runtime_engine()` |
| `runtime_pg_data_dir` | `str` | `""` | P1: `_start_embedded_pg()` |
| `agent_max_concurrent_spawns` | `int` | `10` | P8 |
| `agent_spawn_timeout` | `float` | `300.0` | P8 |
| `agent_spawn_max_steps` | `int` | `4` | P8 |
| `message_ttl_days` | `int` | `30` | P2/P5 |
| `consolidation_health_threshold_minutes` | `int` | `30` | P4 |

---

## 9. Dependencies

| Package | Version | License | Why |
|---------|---------|---------|-----|
| `pgserver` | `>=0.2.0` | MIT | Embedded PostgreSQL -- bundles PG binaries, manages process lifecycle |
| `asyncpg` | `>=0.30.0` | Apache 2.0 | Async PostgreSQL driver for SQLAlchemy async engine |
| `sqlalchemy[asyncio]` | (already present) | MIT | Async session support; the `asyncio` extra pulls in `greenlet` |

**Changes to `pyproject.toml`:**

```toml
dependencies = [
  # ... existing ...
  "pgserver>=0.2.0",
  "asyncpg>=0.30.0",
]
```

The existing `psycopg[binary]` in the `[dependency-groups] postgres` group stays as-is for now. `pgserver` bundles its own PostgreSQL binaries and uses its own connection internally. The application-facing async engine uses `asyncpg`, not `psycopg`.

**Windows compatibility:** `pgserver` ships pre-built PostgreSQL binaries for Windows, macOS, and Linux. The package handles platform-specific binary selection internally. Verified: `pgserver` supports Windows 11 on x86_64.

---

## 10. Configuration

### Environment Variables

All settings follow the existing `ANIMA_` prefix convention from `pydantic_settings`.

```bash
# Use embedded PG (default -- no env vars needed)
# PG data stored in .anima/dev/runtime/pg_data/

# Use external PG (CI, Docker, or user preference)
ANIMA_RUNTIME_DATABASE_URL=postgresql+asyncpg://localhost:5432/anima_runtime

# Custom PG data directory
ANIMA_RUNTIME_PG_DATA_DIR=/path/to/pg_data
```

### Auto-Configuration Flow

```
Server starts
    |
    v
Is ANIMA_RUNTIME_DATABASE_URL set?
    |
    +-- YES --> Use that URL, skip embedded PG
    |           init_runtime_engine(settings.runtime_database_url)
    |
    +-- NO  --> Start embedded PG
                |
                v
                EmbeddedPG(data_dir).start()
                |
                v
                init_runtime_engine(embedded_pg.database_url)
```

### Directory Layout After P1

```
.anima/
  dev/                          (data_dir)
    anima.db                    (Soul store -- unchanged)
    runtime/
      pg_data/                  (Embedded PG data directory)
        postmaster.pid
        PG_VERSION
        base/
        global/
        pg_wal/
        ...
```

---

## 11. Test Plan

All tests go in `apps/server/tests/test_runtime_db.py`.

| # | Type | Test | Details |
|---|------|------|---------|
| T1 | Unit | `EmbeddedPG.start()` creates data directory | Assert `pg_data/` exists after `start()`. Assert `running` property is `True`. |
| T2 | Unit | `EmbeddedPG.stop()` is idempotent | Call `stop()` twice. No exception on second call. Assert `running` is `False`. |
| T3 | Unit | `EmbeddedPG.database_url` returns asyncpg URL | Assert URL starts with `postgresql+asyncpg://`. |
| T4 | Unit | `EmbeddedPG.database_url` raises when not running | Assert `RuntimeError` when accessed before `start()`. |
| T5 | Unit | Stale lockfile recovery | Create a fake `postmaster.pid` with a non-existent PID. Call `_recover_stale_lockfile()`. Assert lockfile is removed. |
| T6 | Unit | Valid lockfile not removed | Create a `postmaster.pid` with the current process PID. Call `_recover_stale_lockfile()`. Assert lockfile still exists. |
| T7 | Integration | Session factory creates working async sessions | Start embedded PG, init engine, open session, execute `SELECT 1`. Assert result is `1`. |
| T8 | Integration | `get_runtime_session()` context manager commits | Insert a row in a temp table, exit context, re-read in new session, assert row exists. |
| T9 | Integration | `get_runtime_session()` rolls back on exception | Insert a row, raise exception inside context, re-read in new session, assert row does not exist. |
| T10 | Integration | `dispose_runtime_engine()` cleans up | Dispose engine, assert `get_runtime_engine()` raises `RuntimeError`. |
| T11 | Integration | Dual-engine coexistence | Open a SQLCipher session (`get_db` path) and a PG session (`get_runtime_session`). Execute queries on both. Assert both return results. Assert they are independent engines. |
| T12 | Integration | Config auto-derives URL from embedded PG | Start server with no `ANIMA_RUNTIME_DATABASE_URL`. Assert runtime engine is initialized with the embedded PG URL. |
| T13 | Integration | Explicit URL skips embedded PG | Set `ANIMA_RUNTIME_DATABASE_URL` in config. Assert `_start_embedded_pg()` returns `None`. Assert runtime engine uses the explicit URL. |
| T14 | Unit | `init_runtime_engine()` raises on invalid URL | Pass a malformed URL. Assert `SQLAlchemyError` or connection failure. |
| T15 | Regression | All existing tests pass | Run full test suite. Assert 846+ tests pass. No modifications to existing test files. |

### CI Considerations

Embedded `pgserver` may not work inside all CI container environments (e.g., minimal Docker images without shared memory configuration). For CI:

1. **Preferred**: Use `pgserver` directly if the CI runner supports it (bare-metal runners, GitHub Actions `ubuntu-latest`)
2. **Fallback**: Start a Docker PostgreSQL service container and set `ANIMA_RUNTIME_DATABASE_URL` to point to it

```yaml
# Example GitHub Actions CI config
services:
  postgres:
    image: postgres:16
    env:
      POSTGRES_DB: anima_runtime
      POSTGRES_USER: anima
      POSTGRES_PASSWORD: test
    ports:
      - 5432:5432

env:
  ANIMA_RUNTIME_DATABASE_URL: postgresql+asyncpg://anima:test@localhost:5432/anima_runtime
```

Tests T1-T6 (embedded PG lifecycle) should be marked with `@pytest.mark.skipif` when `ANIMA_RUNTIME_DATABASE_URL` is explicitly set, since the embedded PG is not started in that case.

Tests T7-T14 should work against either embedded PG or Docker PG -- they test the engine/session layer, not the embedded process.

---

## 12. Acceptance Criteria

| # | Criterion | How to Verify |
|---|-----------|---------------|
| AC1 | Server starts with embedded PG when no `ANIMA_RUNTIME_DATABASE_URL` is set | Start server, check logs for "Embedded PostgreSQL started", verify `.anima/dev/runtime/pg_data/` exists |
| AC2 | Server stops embedded PG cleanly on shutdown | Stop server (SIGTERM or Ctrl+C), check logs for "Embedded PostgreSQL stopped", verify no orphan `postgres` process |
| AC3 | `get_runtime_session()` yields a working async session | Test T7 passes: `SELECT 1` returns `1` |
| AC4 | Existing `get_db()` is completely unchanged | Diff `db/session.py` -- the `get_db` function has zero modifications. All 846+ existing tests pass. |
| AC5 | Stale lockfile is detected and recovered | Test T5 passes: fake lockfile with dead PID is removed, server starts successfully |
| AC6 | `atexit` handler stops PG even if lifespan shutdown is not reached | Kill server process (not SIGTERM), restart. Server should not fail with "lockfile exists" (stale recovery handles it). |
| AC7 | CI Docker fallback works | Set `ANIMA_RUNTIME_DATABASE_URL` to a Docker PG instance. All session tests (T7-T14) pass. Embedded PG is not started. |
| AC8 | New config settings are recognized | Set `ANIMA_AGENT_MAX_CONCURRENT_SPAWNS=5` via env var. Assert `settings.agent_max_concurrent_spawns == 5`. |
| AC9 | Dual-engine independence | Test T11 passes: SQLCipher and PG sessions coexist without interference |
| AC10 | No regression in existing test suite | `pytest` reports 846+ tests passing, 0 failures, 0 errors |

---

## 13. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `pgserver` instability on Windows 11 | Medium | High | Test on target Windows 11 machine early. Stale lockfile recovery handles most crash scenarios. Docker fallback exists for CI. |
| `pgserver` binary size increases distribution | Low | Medium | `pgserver` bundles PG binaries (~50-100 MB). Acceptable for a desktop app. |
| Port conflicts on `localhost` | Low | Low | `pgserver` uses Unix domain sockets by default (macOS/Linux) or finds a free port (Windows). No fixed port allocation. |
| `asyncpg` compatibility with embedded PG version | Low | Medium | `pgserver` bundles PG 16+. `asyncpg` supports PG 10+. Version mismatch is unlikely. |
| Startup time increase | Medium | Low | Embedded PG cold start takes 1-3 seconds. Warm start (existing data dir) is sub-second. Acceptable for a desktop app that starts once per session. |
| `atexit` handler not called on hard kill (SIGKILL / power loss) | Medium | Low | Stale lockfile recovery on next startup handles this. No data loss because PG uses WAL. |

---

## 14. Architecture Decision Record

### ADR-P1-001: Embedded PostgreSQL via `pgserver`

**Status**: Proposed

**Context**: The Runtime store needs PostgreSQL for concurrent write support (N-agent spawning). The application must remain zero-dependency and portable -- no Docker Desktop, no system-level PostgreSQL installation. The primary development platform is Windows 11.

**Decision**: Use the `pgserver` Python package to embed PostgreSQL directly in the Python process. PG data lives in `.anima/runtime/pg_data/`. The PG process lifecycle is tied to the Python server via FastAPI lifespan + `atexit`.

**Consequences**:
- Easier: No Docker, no install steps, works on any platform `pgserver` supports
- Easier: PG data is inside `.anima/` -- portable by construction
- Harder: Binary size increases by ~50-100 MB (PG binaries bundled)
- Harder: Debugging PG issues requires understanding the embedded abstraction
- Harder: First startup is slower (PG initialization)

### ADR-P1-002: Separate DeclarativeBase for Runtime Models

**Status**: Proposed

**Context**: The Soul store (SQLCipher) and Runtime store (PostgreSQL) have different models. They need independent metadata so Alembic migrations (when added in P2) can target each store independently without seeing the other's tables.

**Decision**: Create `RuntimeBase(DeclarativeBase)` in `db/runtime_base.py` with its own `MetaData` instance. Soul models continue to inherit from `Base` in `db/base.py`. Runtime models will inherit from `RuntimeBase`.

**Consequences**:
- Easier: Clean separation of migration targets
- Easier: `RuntimeBase.metadata.create_all()` only creates runtime tables
- Harder: Developers must choose the correct base class for new models

### ADR-P1-003: Async Engine for Runtime, Sync Engine for Soul

**Status**: Proposed

**Context**: The existing SQLCipher engine is synchronous (SQLite does not benefit from async I/O -- it is in-process). PostgreSQL benefits from async I/O because queries go over a network socket (even localhost). The FastAPI routes are already async.

**Decision**: The Runtime engine uses `create_async_engine` with `asyncpg`. The Soul engine remains synchronous with `create_engine`. They coexist in the same process. Soul access from async routes continues to use `run_in_executor` or sync `Depends(get_db)` (FastAPI handles the thread pool).

**Consequences**:
- Easier: PG queries are non-blocking in the event loop
- Easier: No changes to existing Soul access patterns
- Harder: Two different session APIs (`Session` vs `AsyncSession`) in the codebase
- Trade-off: Developers must know which session type to use for which store

---

## 15. Out of Scope

| Item | Reason | Phase |
|------|--------|-------|
| Runtime table definitions (messages, threads, runs, spawns) | No data moves until P2 | P2 |
| Alembic migration infrastructure for runtime DB | No models to migrate until P2 | P2 |
| pgvector extension installation | Not needed until embeddings migrate | P6 |
| Connection pool tuning for concurrency | Not needed until N concurrent agents | P7 |
| SpawnManager and spawn tools | Application-level feature | P8 |
| Transcript archive (encrypted JSONL) | Separate tier | P5 |
| Modifying `get_db()` or any Soul store code | Explicit non-goal of this phase | -- |
| Multi-user or remote PostgreSQL support | Single-user desktop app | -- |

---

## 16. Rollout

1. Add `pgserver` and `asyncpg` to `pyproject.toml`
2. Create `db/runtime_base.py` with `RuntimeBase`
3. Create `db/pg_lifecycle.py` with `EmbeddedPG`
4. Create `db/runtime.py` with async engine, session factory, `get_runtime_session()`
5. Add new config fields to `config.py`
6. Refactor `main.py` to use `lifespan` context manager with PG startup/shutdown
7. Update `db/__init__.py` exports
8. Write tests in `test_runtime_db.py`
9. Run full test suite (846+ existing tests must pass unchanged)
10. Ship as a single PR

No feature flag is needed. The embedded PG starts alongside the server but is not consumed by any application code until P2. Existing behavior is unchanged.
