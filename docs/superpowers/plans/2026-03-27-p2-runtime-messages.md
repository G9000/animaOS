# P2: Runtime Messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate agent runtime models (threads, messages, runs, steps, background tasks) from per-user SQLCipher to shared PostgreSQL, enabling concurrent multi-agent writes.

**Architecture:** Dual-session pattern. Soul data (memory, identity, self-model) stays in SQLCipher. Runtime data (threads, messages, runs, steps) moves to PG. Service functions receive the session they need from callers. No feature flag — single PG code path.

**Tech Stack:** SQLAlchemy 2.0 (sync sessions via psycopg), PostgreSQL (embedded via pgserver), Alembic (separate runtime migration env), FastAPI dependencies.

**Key references:**
- PRD: `docs/prds/three-tier-architecture/P2-runtime-messages.md`
- Design spec: `docs/superpowers/specs/2026-03-27-p2-runtime-messages-design.md`

**Driver note:** pyproject.toml already has `psycopg[binary]>=3.2.9` in the `postgres` dep group. We use psycopg v3 (dialect `postgresql+psycopg://`) instead of the spec's psycopg2, since it's already available and is the modern SQLAlchemy 2.0 driver.

---

## File Map

### New files

| File | Responsibility |
|---|---|
| `src/anima_server/models/runtime.py` | 5 PG-native runtime model classes |
| `alembic_runtime.ini` | Alembic config for runtime PG |
| `alembic_runtime/env.py` | Runtime migration environment |
| `alembic_runtime/script.py.mako` | Migration template |
| `alembic_runtime/versions/001_create_runtime_tables.py` | Initial migration |
| `tests/conftest_runtime.py` | Runtime PG test fixtures |
| `tests/test_runtime_persistence.py` | Runtime persistence unit tests |

### Modified files

| File | Change |
|---|---|
| `src/anima_server/db/runtime.py` | Replace async engine with sync engine, add `get_runtime_db`, `ensure_runtime_tables()` |
| `src/anima_server/db/pg_lifecycle.py` | URL converter produces `postgresql+psycopg://` instead of `+asyncpg` |
| `src/anima_server/db/__init__.py` | Export new runtime functions |
| `src/anima_server/config.py` | Add `runtime_pool_size`, `runtime_pool_max_overflow` |
| `src/anima_server/main.py` | Sync engine init in lifespan, call `ensure_runtime_tables()` |
| `src/anima_server/models/__init__.py` | Re-export runtime models |
| `src/anima_server/services/agent/persistence.py` | Swap models to `Runtime*`, remove `ef()`/`df()` |
| `src/anima_server/services/agent/sequencing.py` | Swap model, upgrade to `FOR UPDATE` |
| `src/anima_server/services/agent/compaction.py` | Swap models, remove `ef()`/`df()` |
| `src/anima_server/services/agent/conversation_search.py` | Dual-session signature |
| `src/anima_server/services/agent/tool_context.py` | Add `runtime_db` field |
| `src/anima_server/services/agent/service.py` | Thread `runtime_db` through all functions |
| `src/anima_server/services/agent/companion.py` | History loading uses runtime session |
| `src/anima_server/services/agent/reflection.py` | Accept `runtime_db_factory` |
| `src/anima_server/services/agent/sleep_agent.py` | BackgroundTaskRun uses runtime session |
| `src/anima_server/services/agent/tools.py` | `recall_conversation` uses dual sessions |
| `src/anima_server/api/routes/chat.py` | Add `runtime_db` dependency |
| `src/anima_server/api/routes/ws.py` | Create runtime session alongside soul session |
| `pyproject.toml` | Move `psycopg[binary]` to main dependencies |

---

## Task 1: Runtime Models

**Files:**
- Create: `apps/server/src/anima_server/models/runtime.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`

- [ ] **Step 1: Create runtime models file**

Create `apps/server/src/anima_server/models/runtime.py` with 5 PG-native model classes. These mirror the soul models but use `RuntimeBase`, `BigInteger` PKs, `TIMESTAMPTZ`, and `postgresql.JSON`.

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
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
        ForeignKey("runtime_threads.id", ondelete="CASCADE"),
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
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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

- [ ] **Step 2: Update models `__init__.py` to re-export runtime models**

Add to `apps/server/src/anima_server/models/__init__.py`:

```python
from anima_server.models.runtime import (
    RuntimeBackgroundTaskRun,
    RuntimeMessage,
    RuntimeRun,
    RuntimeStep,
    RuntimeThread,
)
```

And add to the `__all__` list:
```python
"RuntimeBackgroundTaskRun",
"RuntimeMessage",
"RuntimeRun",
"RuntimeStep",
"RuntimeThread",
```

- [ ] **Step 3: Commit**

```bash
git add apps/server/src/anima_server/models/runtime.py apps/server/src/anima_server/models/__init__.py
git commit -m "feat(p2): add runtime PG model definitions"
```

---

## Task 2: Runtime DB Infrastructure (sync engine)

**Files:**
- Modify: `apps/server/src/anima_server/db/runtime.py`
- Modify: `apps/server/src/anima_server/db/pg_lifecycle.py`
- Modify: `apps/server/src/anima_server/db/__init__.py`
- Modify: `apps/server/src/anima_server/config.py`
- Modify: `apps/server/pyproject.toml`

- [ ] **Step 1: Replace async runtime engine with sync engine**

Rewrite `apps/server/src/anima_server/db/runtime.py` to use sync `Session` via psycopg:

```python
from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

_runtime_engine: Engine | None = None
_runtime_session_factory: sessionmaker[Session] | None = None

_ALEMBIC_RUNTIME_INI = Path(__file__).resolve().parents[3] / "alembic_runtime.ini"


def init_runtime_engine(database_url: str, *, echo: bool = False, pool_size: int = 5, max_overflow: int = 10) -> None:
    """Initialize the Runtime store sync engine (psycopg)."""
    global _runtime_engine, _runtime_session_factory

    sync_url = _to_sync_url(database_url)
    _runtime_engine = create_engine(
        sync_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )
    _runtime_session_factory = sessionmaker(
        bind=_runtime_engine,
        expire_on_commit=False,
    )
    logger.info("Runtime PG engine initialized: %s", sync_url.split("@")[-1] if "@" in sync_url else sync_url)


def dispose_runtime_engine() -> None:
    """Dispose the Runtime store engine (sync, not async)."""
    global _runtime_engine, _runtime_session_factory

    if _runtime_engine is not None:
        _runtime_engine.dispose()
        _runtime_engine = None
        _runtime_session_factory = None


def get_runtime_engine() -> Engine:
    """Return the Runtime store engine."""
    if _runtime_engine is None:
        raise RuntimeError(
            "Runtime engine not initialized. "
            "Call init_runtime_engine() during server startup."
        )
    return _runtime_engine


def get_runtime_session_factory() -> sessionmaker[Session]:
    """Return the Runtime session factory."""
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
    from alembic import command
    from alembic.config import Config

    engine = get_runtime_engine()
    cfg = Config(str(_ALEMBIC_RUNTIME_INI))

    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")

    logger.info("Runtime Alembic migrations complete.")


def _to_sync_url(url: str) -> str:
    """Convert any PG URL variant to psycopg (sync) format."""
    url = url.replace("+asyncpg", "+psycopg")
    if "://" in url and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url
```

- [ ] **Step 2: Update pg_lifecycle.py URL converter**

In `apps/server/src/anima_server/db/pg_lifecycle.py`, the `database_url` property and `_to_asyncpg_url` produce asyncpg URLs. Add a sync URL property and rename the converter:

Replace the `database_url` property and `_to_asyncpg_url` method:

```python
    @property
    def database_url(self) -> str:
        """Return the connection URL for the running instance.

        Returns the raw pgserver URL — callers convert to the dialect they need.
        """
        if not self.running:
            raise RuntimeError("Embedded PG is not running")
        return self._server.get_uri()

    @staticmethod
    def to_sync_url(raw_url: str) -> str:
        """Convert pgserver's raw URL to psycopg (sync) format."""
        if "+psycopg" in raw_url:
            return raw_url
        # Strip any existing dialect suffix
        base = raw_url.replace("+asyncpg://", "://").replace("+psycopg2://", "://")
        return base.replace("postgresql://", "postgresql+psycopg://", 1)
```

Remove the `_to_asyncpg_url` static method entirely.

- [ ] **Step 3: Update db/__init__.py exports**

Replace `apps/server/src/anima_server/db/__init__.py`:

```python
from .base import Base
from .runtime import (
    dispose_runtime_engine,
    get_runtime_db,
    get_runtime_engine,
    get_runtime_session_factory,
    init_runtime_engine,
)
from .runtime_base import RuntimeBase
from .session import (
    SessionLocal,
    build_session_factory_for_db,
    dispose_all_user_engines,
    dispose_cached_engines,
    engine,
    get_db,
)

__all__ = [
    "Base",
    "RuntimeBase",
    "SessionLocal",
    "build_session_factory_for_db",
    "dispose_all_user_engines",
    "dispose_cached_engines",
    "dispose_runtime_engine",
    "engine",
    "get_db",
    "get_runtime_db",
    "get_runtime_engine",
    "get_runtime_session_factory",
    "init_runtime_engine",
]
```

- [ ] **Step 4: Add runtime pool settings to config.py**

In `apps/server/src/anima_server/config.py`, add after `runtime_pg_data_dir`:

```python
    runtime_pool_size: int = 5
    runtime_pool_max_overflow: int = 10
```

- [ ] **Step 5: Move psycopg to main dependencies in pyproject.toml**

In `apps/server/pyproject.toml`, add `"psycopg[binary]>=3.2.9"` to the main `dependencies` list and remove it from the `[dependency-groups] postgres` section (or leave it there for redundancy).

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/db/runtime.py apps/server/src/anima_server/db/pg_lifecycle.py apps/server/src/anima_server/db/__init__.py apps/server/src/anima_server/config.py apps/server/pyproject.toml
git commit -m "feat(p2): sync runtime engine, pg_lifecycle URL converter, config"
```

---

## Task 3: Alembic Runtime Environment

**Files:**
- Create: `apps/server/alembic_runtime.ini`
- Create: `apps/server/alembic_runtime/env.py`
- Create: `apps/server/alembic_runtime/script.py.mako`
- Create: `apps/server/alembic_runtime/versions/001_create_runtime_tables.py`

- [ ] **Step 1: Create alembic_runtime.ini**

Create `apps/server/alembic_runtime.ini`:

```ini
[alembic]
script_location = %(here)s/alembic_runtime
prepend_sys_path = %(here)s/src
sqlalchemy.url = postgresql+psycopg://localhost/anima_runtime
path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 2: Create alembic_runtime/env.py**

Create `apps/server/alembic_runtime/env.py`:

```python
from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from anima_server.db.runtime_base import RuntimeBase

# Import runtime models so their tables register on RuntimeBase.metadata.
from anima_server.models import runtime as _runtime_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = RuntimeBase.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection", None)

    if connectable is not None:
        # Programmatic usage: connection passed from ensure_runtime_tables()
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        # CLI usage
        from anima_server.db.runtime import get_runtime_engine

        engine = get_runtime_engine()
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Create alembic_runtime/script.py.mako**

Create `apps/server/alembic_runtime/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Create initial runtime migration**

Create `apps/server/alembic_runtime/versions/001_create_runtime_tables.py`:

```python
"""Create runtime tables.

Revision ID: 001
Revises:
Create Date: 2026-03-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, TIMESTAMPTZ

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # runtime_threads
    op.create_table(
        "runtime_threads",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("last_message_at", TIMESTAMPTZ, nullable=True),
        sa.Column("next_message_sequence", sa.Integer, nullable=False, server_default=sa.text("1")),
    )
    op.create_index("ix_runtime_threads_user_id", "runtime_threads", ["user_id"], unique=True)

    # runtime_runs
    op.create_table(
        "runtime_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.BigInteger, sa.ForeignKey("runtime_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column("stop_reason", sa.String(64), nullable=True),
        sa.Column("error_text", sa.Text, nullable=True),
        sa.Column("started_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", TIMESTAMPTZ, nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("pending_approval_message_id", sa.BigInteger, nullable=True),
    )
    op.create_index("ix_runtime_runs_thread_id", "runtime_runs", ["thread_id"])
    op.create_index("ix_runtime_runs_user_id", "runtime_runs", ["user_id"])
    op.create_index("ix_runtime_runs_pending_approval_message_id", "runtime_runs", ["pending_approval_message_id"])

    # runtime_steps
    op.create_table(
        "runtime_steps",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.BigInteger, sa.ForeignKey("runtime_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.BigInteger, nullable=False),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("request_json", JSON, nullable=False),
        sa.Column("response_json", JSON, nullable=False),
        sa.Column("tool_calls_json", JSON, nullable=True),
        sa.Column("usage_json", JSON, nullable=True),
        sa.Column("error_text", sa.Text, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "step_index", name="uq_runtime_steps_run_step"),
    )
    op.create_index("ix_runtime_steps_run_id", "runtime_steps", ["run_id"])
    op.create_index("ix_runtime_steps_thread_id", "runtime_steps", ["thread_id"])

    # runtime_messages
    op.create_table(
        "runtime_messages",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.BigInteger, sa.ForeignKey("runtime_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.BigInteger, nullable=True),
        sa.Column("step_id", sa.BigInteger, nullable=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("sequence_id", sa.Integer, nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("content_text", sa.Text, nullable=True),
        sa.Column("content_json", JSON, nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("tool_call_id", sa.String(128), nullable=True),
        sa.Column("tool_args_json", JSON, nullable=True),
        sa.Column("is_in_context", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("token_estimate", sa.Integer, nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("thread_id", "sequence_id", name="uq_runtime_messages_thread_seq"),
    )
    op.create_index("ix_runtime_messages_thread_id", "runtime_messages", ["thread_id"])
    op.create_index("ix_runtime_messages_run_id", "runtime_messages", ["run_id"])
    op.create_index("ix_runtime_messages_user_id", "runtime_messages", ["user_id"])
    op.create_index("ix_runtime_messages_user_created", "runtime_messages", ["user_id", "created_at"])
    op.create_index("ix_runtime_messages_thread_context", "runtime_messages", ["thread_id", "is_in_context"])

    # runtime_background_task_runs
    op.create_table(
        "runtime_background_task_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("result_json", JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", TIMESTAMPTZ, nullable=True),
        sa.Column("completed_at", TIMESTAMPTZ, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_runtime_bg_task_user_id", "runtime_background_task_runs", ["user_id"])
    op.create_index("ix_runtime_bg_task_user_status", "runtime_background_task_runs", ["user_id", "status"])


def downgrade() -> None:
    op.drop_table("runtime_background_task_runs")
    op.drop_table("runtime_messages")
    op.drop_table("runtime_steps")
    op.drop_table("runtime_runs")
    op.drop_table("runtime_threads")
```

- [ ] **Step 5: Create alembic_runtime/versions/__init__.py** (empty)

- [ ] **Step 6: Commit**

```bash
git add apps/server/alembic_runtime.ini apps/server/alembic_runtime/
git commit -m "feat(p2): add Alembic runtime migration environment and initial migration"
```

---

## Task 4: Lifespan Updates (main.py)

**Files:**
- Modify: `apps/server/src/anima_server/main.py`

- [ ] **Step 1: Update lifespan to sync engine init and run migrations**

In `apps/server/src/anima_server/main.py`:

1. Change `dispose_runtime_engine` import (no longer async).
2. Update `lifespan` to call `ensure_runtime_tables()` after engine init.
3. Remove `await` from `dispose_runtime_engine()`.

The import line changes from:
```python
from .db.runtime import dispose_runtime_engine, init_runtime_engine
```
to:
```python
from .db.runtime import dispose_runtime_engine, ensure_runtime_tables, init_runtime_engine
```

In `_start_embedded_pg`, the `database_url` property no longer adds `+asyncpg`. The lifespan block changes:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    embedded_pg = _start_embedded_pg()
    runtime_url = embedded_pg.database_url if embedded_pg is not None else settings.runtime_database_url

    try:
        if runtime_url:
            init_runtime_engine(
                runtime_url,
                echo=settings.database_echo,
                pool_size=settings.runtime_pool_size,
                max_overflow=settings.runtime_pool_max_overflow,
            )
            ensure_runtime_tables()
    except Exception:
        if embedded_pg is not None:
            embedded_pg.stop()
        raise

    try:
        yield
    finally:
        from .services.agent.consolidation import drain_background_memory_tasks
        from .services.agent.reflection import cancel_pending_reflection

        try:
            await cancel_pending_reflection()
            await drain_background_memory_tasks()
        finally:
            dispose_runtime_engine()
            if embedded_pg is not None:
                embedded_pg.stop()
```

- [ ] **Step 2: Commit**

```bash
git add apps/server/src/anima_server/main.py
git commit -m "feat(p2): sync runtime engine init and migration in lifespan"
```

---

## Task 5: Persistence Layer Rewiring

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/persistence.py`

This is the largest single-file change. Every function swaps model imports from `Agent*` to `Runtime*` and removes `ef()`/`df()` on content_text.

- [ ] **Step 1: Update imports and model references**

Replace the model import line:
```python
from anima_server.models import AgentMessage, AgentRun, AgentStep, AgentThread
```
with:
```python
from anima_server.models.runtime import RuntimeMessage, RuntimeRun, RuntimeStep, RuntimeThread
```

Remove the `ef`/`df` import:
```python
from anima_server.services.data_crypto import df, ef
```

- [ ] **Step 2: Rewrite `get_or_create_thread`**

```python
def get_or_create_thread(db: Session, user_id: int) -> RuntimeThread:
    thread = db.scalar(select(RuntimeThread).where(RuntimeThread.user_id == user_id))
    if thread is not None:
        return thread

    thread = RuntimeThread(
        user_id=user_id,
        status="active",
    )
    db.add(thread)
    db.flush()
    return thread
```

- [ ] **Step 3: Rewrite `load_thread_history` — remove df()**

```python
def load_thread_history(
    db: Session, thread_id: int, *, user_id: int | None = None
) -> list[StoredMessage]:
    rows = db.scalars(
        select(RuntimeMessage)
        .where(
            RuntimeMessage.thread_id == thread_id,
            RuntimeMessage.is_in_context.is_(True),
            RuntimeMessage.role.in_(("user", "assistant", "tool")),
        )
        .order_by(RuntimeMessage.sequence_id)
    ).all()

    history: list[StoredMessage] = []
    for row in rows:
        content = row.content_text or ""
        history.append(
            StoredMessage(
                role=row.role,
                content=content,
                tool_name=row.tool_name,
                tool_call_id=row.tool_call_id,
                tool_calls=_deserialize_tool_calls(row.content_json),
            )
        )
    return history
```

- [ ] **Step 4: Rewrite remaining functions**

Apply the same pattern to all remaining functions — swap `AgentThread` -> `RuntimeThread`, `AgentMessage` -> `RuntimeMessage`, `AgentRun` -> `RuntimeRun`, `AgentStep` -> `RuntimeStep`.

In `append_message`, remove the `ef()` call:
```python
content_text=content_text,  # was: ef(uid, content_text, ...)
```

In `list_transcript_messages`, swap all model references. The `user_id` is now on `RuntimeMessage` directly (denormalized), so the thread lookup is still needed for the thread join but the query structure stays the same.

In `create_step`, swap `AgentStep` -> `RuntimeStep`.

The function signatures stay `db: Session` — callers pass the right session.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/persistence.py
git commit -m "feat(p2): rewire persistence to Runtime* models, remove ef/df"
```

---

## Task 6: Sequencing Rewiring

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/sequencing.py`

- [ ] **Step 1: Swap model and upgrade to FOR UPDATE**

Replace entire file content:

```python
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeThread
from anima_server.services.agent.state import AgentResult


def reserve_message_sequences(
    db: Session,
    *,
    thread_id: int,
    count: int,
) -> int:
    """Reserve a contiguous sequence range for a thread and return its start.

    Uses SELECT ... FOR UPDATE for true row-level locking on PG.
    """
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


def count_persisted_result_messages(result: AgentResult) -> int:
    count = 0
    for trace in result.step_traces:
        if trace.assistant_text or trace.tool_calls:
            count += 1
        count += len(trace.tool_results)
    return count
```

- [ ] **Step 2: Commit**

```bash
git add apps/server/src/anima_server/services/agent/sequencing.py
git commit -m "feat(p2): rewire sequencing to RuntimeThread, use FOR UPDATE"
```

---

## Task 7: Compaction Rewiring

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/compaction.py`

- [ ] **Step 1: Update imports**

Replace:
```python
from anima_server.models import AgentMessage, AgentThread
```
with:
```python
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
```

Remove:
```python
from anima_server.services.data_crypto import df, ef
```

- [ ] **Step 2: Swap all model references throughout the file**

- `AgentMessage` -> `RuntimeMessage` (every occurrence in queries, type hints, and constructors)
- `AgentThread` -> `RuntimeThread` (in function signatures)
- Remove `ef()` calls when creating summary messages: `content_text=summary_text` instead of `content_text=ef(thread.user_id, summary_text, ...)`
- Remove `df()` calls in `render_summary_text` and `_summarize_row`: use `summary_row.content_text or ""` directly instead of `df(user_id, (summary_row.content_text or ""), ...)`
- Remove `_build_transcript`'s implicit dependency on df (it reads `row.content_text` directly which is now plaintext)

- [ ] **Step 3: Commit**

```bash
git add apps/server/src/anima_server/services/agent/compaction.py
git commit -m "feat(p2): rewire compaction to Runtime* models, remove ef/df"
```

---

## Task 8: Tool Context Update

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/tool_context.py`

- [ ] **Step 1: Add runtime_db field to ToolContext**

```python
@dataclass(slots=True)
class ToolContext:
    db: Session          # soul session
    runtime_db: Session  # runtime session
    user_id: int
    thread_id: int
    memory_modified: bool = False
```

- [ ] **Step 2: Commit**

```bash
git add apps/server/src/anima_server/services/agent/tool_context.py
git commit -m "feat(p2): add runtime_db to ToolContext"
```

---

## Task 9: Conversation Search — Dual Session

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/conversation_search.py`

- [ ] **Step 1: Update imports and function signature**

Replace:
```python
from anima_server.models import AgentMessage, AgentThread, MemoryDailyLog
```
with:
```python
from anima_server.models import MemoryDailyLog
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
```

Change `search_conversation_history` to take two sessions:

```python
async def search_conversation_history(
    runtime_db: Session,
    soul_db: Session,
    *,
    user_id: int,
    query: str,
    role_filter: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 10,
) -> list[ConversationHit]:
```

Pass `runtime_db` to `_search_messages` and `soul_db` to `_search_daily_logs`.

- [ ] **Step 2: Update `_search_messages` to use Runtime models**

Swap `AgentThread` -> `RuntimeThread`, `AgentMessage` -> `RuntimeMessage`. Remove `df()` — `content_text` is plaintext in PG.

- [ ] **Step 3: `_search_daily_logs` stays on soul session**

This function keeps using `soul_db` and `df()` — daily logs are soul data and remain encrypted.

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/services/agent/conversation_search.py
git commit -m "feat(p2): dual-session conversation search"
```

---

## Task 10: Service Layer Rewiring

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py`

This is the largest change. Every function in the call chain gets `runtime_db: Session` added.

- [ ] **Step 1: Update imports**

Replace:
```python
from anima_server.models import AgentMessage, AgentRun, AgentThread
```
with:
```python
from anima_server.models.runtime import RuntimeMessage, RuntimeRun, RuntimeThread
```

- [ ] **Step 2: Update `run_agent`, `stream_agent`, `list_agent_history`, `reset_agent_thread`, `cancel_agent_run`, `dry_run_agent`**

Add `runtime_db: Session` parameter to each. These are the public API functions.

```python
async def run_agent(
    user_message: str, user_id: int, db: Session, runtime_db: Session, *, source: str | None = None
) -> AgentResult:
    return await _execute_agent_turn(user_message, user_id, db, runtime_db, source=source)

async def cancel_agent_run(run_id: int, user_id: int, runtime_db: Session) -> RuntimeRun | None:
    run = cancel_run(runtime_db, run_id)
    ...
    runtime_db.commit()
    return run

async def dry_run_agent(user_message: str, user_id: int, db: Session, runtime_db: Session) -> DryRunResult:
    ...
    thread = runtime_db.scalar(...)  # lookup in runtime
    history = companion.ensure_history_loaded(runtime_db)
    memory_blocks = companion.ensure_memory_loaded(db)
    ...

def list_agent_history(user_id: int, runtime_db: Session, *, limit: int = 50) -> list[RuntimeMessage]:
    return list_transcript_messages(runtime_db, user_id=user_id, limit=limit)

async def reset_agent_thread(user_id: int, runtime_db: Session) -> None:
    reset_thread(runtime_db, user_id)
    runtime_db.commit()
    ...
```

- [ ] **Step 3: Update `_execute_agent_turn` and `_execute_agent_turn_locked`**

Add `runtime_db: Session` parameter. All persistence calls use `runtime_db`. Memory calls use `db`.

- [ ] **Step 4: Update `_prepare_turn_context`**

Thread/run/message operations use `runtime_db`. Memory/semantic operations use `db`.

```python
async def _prepare_turn_context(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    ...
) -> tuple[RuntimeThread, RuntimeRun, RuntimeMessage, int, _TurnContext]:
    companion = _get_companion(user_id)
    thread = get_or_create_thread(runtime_db, user_id)
    companion.thread_id = thread.id
    history = companion.ensure_history_loaded(runtime_db)
    run = create_run(runtime_db, ...)
    initial_sequence_id = reserve_message_sequences(runtime_db, ...)
    user_msg = append_user_message(runtime_db, ...)
    conversation_turn_count = count_messages_by_role(runtime_db, thread.id, "user")
    # Semantic retrieval uses db (soul)
    search_result = await hybrid_search(db, ...)
    static_blocks = companion.ensure_memory_loaded(db)
    memory_blocks = build_runtime_memory_blocks(db, ...)
    ...
```

- [ ] **Step 5: Update `_invoke_turn_runtime`**

```python
set_tool_context(ToolContext(db=db, runtime_db=runtime_db, user_id=user_id, thread_id=thread.id))
```

The `_refresh_memory` callback uses `db` (soul session). Emergency compaction uses `runtime_db`.

- [ ] **Step 6: Update `_persist_turn_result`, `_proactive_compact_if_needed`, `_persist_approval_checkpoint`, `approve_or_deny_turn`**

All persistence operations → `runtime_db`. All memory operations → `db`.

- [ ] **Step 7: Update `_run_post_turn_hooks`**

Add `runtime_db_factory` parameter:

```python
def _run_post_turn_hooks(
    *,
    user_id: int,
    thread_id: int,
    user_message: str,
    result: AgentResult,
    db_factory: Callable[[], Session],
    runtime_db_factory: Callable[[], Session],
) -> None:
    ...
    schedule_reflection(
        user_id=user_id,
        thread_id=thread_id,
        db_factory=db_factory,
        runtime_db_factory=runtime_db_factory,
    )
```

Add a `_build_runtime_db_factory()`:

```python
def _build_runtime_db_factory() -> Callable[[], Session]:
    from anima_server.db.runtime import get_runtime_session_factory
    return get_runtime_session_factory()
```

- [ ] **Step 8: Update `_refresh_companion_history`**

```python
def _refresh_companion_history(*, user_id: int, runtime_db: Session) -> None:
    companion = get_companion(user_id)
    if companion is None:
        return
    companion.invalidate_history()
    companion.ensure_history_loaded(runtime_db)
```

- [ ] **Step 9: Update `stream_agent` and `stream_approve_or_deny`**

Add `runtime_db: Session` parameter and pass through.

- [ ] **Step 10: Update `__init__.py` exports**

The public API signatures change — callers now pass `runtime_db`. Update `services/agent/__init__.py` if needed (it just re-exports, so no signature changes there).

- [ ] **Step 11: Commit**

```bash
git add apps/server/src/anima_server/services/agent/service.py apps/server/src/anima_server/services/agent/__init__.py
git commit -m "feat(p2): thread runtime_db through service layer"
```

---

## Task 11: Companion Rewiring

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/companion.py`

- [ ] **Step 1: Update `ensure_history_loaded` and `warm`**

`ensure_history_loaded` takes the runtime session (it loads messages from PG):

```python
def ensure_history_loaded(self, runtime_db: Session) -> list[StoredMessage]:
    if self._conversation_window:
        return self._conversation_window
    if self._thread_id is None:
        return []
    history = load_thread_history(runtime_db, self._thread_id, user_id=self._user_id)
    self.set_conversation_window(history)
    return self._conversation_window
```

`warm` takes both sessions:

```python
def warm(self, db: Session, runtime_db: Session) -> None:
    if self._thread_id is None:
        from anima_server.services.agent.persistence import get_or_create_thread
        thread = get_or_create_thread(runtime_db, self._user_id)
        self._thread_id = thread.id
    blocks = build_runtime_memory_blocks(db, ...)
    self.set_memory_cache(blocks)
    history = load_thread_history(runtime_db, self._thread_id, user_id=self._user_id)
    self.set_conversation_window(history)
```

`ensure_memory_loaded` stays on `db: Session` (soul session) — no change needed.

- [ ] **Step 2: Commit**

```bash
git add apps/server/src/anima_server/services/agent/companion.py
git commit -m "feat(p2): companion uses runtime session for history"
```

---

## Task 12: Reflection and Sleep Agent

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/reflection.py`
- Modify: `apps/server/src/anima_server/services/agent/sleep_agent.py`

- [ ] **Step 1: Update reflection.py**

Add `runtime_db_factory` parameter to `schedule_reflection` and pass through to `_delayed_reflection` and `run_reflection`. The reflection tasks that read messages need a runtime session.

```python
def schedule_reflection(
    *,
    user_id: int,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
```

Pass `runtime_db_factory` through to `_delayed_reflection` and `run_reflection`.

In `run_reflection`, pass `runtime_db_factory` to `run_quick_reflection` and `run_sleeptime_agents`:

```python
async def run_reflection(
    *,
    user_id: int,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
    ...
    reflection = await run_quick_reflection(
        user_id=user_id,
        thread_id=thread_id,
        db_factory=db_factory,
    )
    ...
    run_ids = await run_sleeptime_agents(
        user_id=user_id,
        ...,
        db_factory=db_factory,
        runtime_db_factory=runtime_db_factory,
        force=True,
    )
```

- [ ] **Step 2: Update sleep_agent.py — BackgroundTaskRun uses runtime session**

In `_issue_background_task`, change from `BackgroundTaskRun` (soul) to `RuntimeBackgroundTaskRun` (runtime). The factory used for task run tracking should be `runtime_db_factory`:

```python
async def _issue_background_task(
    *,
    user_id: int,
    task_type: str,
    task_fn: Callable[..., Any],
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
    **kwargs: Any,
) -> str:
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun

    rt_factory = runtime_db_factory or get_runtime_session_factory()

    with rt_factory() as db:
        run = RuntimeBackgroundTaskRun(user_id=user_id, task_type=task_type, status="pending")
        db.add(run)
        db.commit()
        run_id = run.id
    ...
```

Update `run_sleeptime_agents` to accept and pass `runtime_db_factory`.

Update `get_last_processed_message_id` and `update_last_processed_message_id` to use `RuntimeBackgroundTaskRun` via runtime session.

- [ ] **Step 3: Commit**

```bash
git add apps/server/src/anima_server/services/agent/reflection.py apps/server/src/anima_server/services/agent/sleep_agent.py
git commit -m "feat(p2): reflection and sleep_agent use runtime session for task tracking"
```

---

## Task 13: recall_conversation Tool Update

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/tools.py`

- [ ] **Step 1: Update recall_conversation to use dual sessions**

In the `recall_conversation` tool function, get both sessions from `ToolContext`:

```python
ctx = get_tool_context()
# ...
hits = future.result(timeout=30)  # or asyncio.run(...)
```

Change the `search_conversation_history` call to pass both sessions:

```python
search_conversation_history(
    ctx.runtime_db,  # runtime session for messages
    ctx.db,          # soul session for daily logs
    user_id=ctx.user_id,
    ...
)
```

- [ ] **Step 2: Commit**

```bash
git add apps/server/src/anima_server/services/agent/tools.py
git commit -m "feat(p2): recall_conversation uses dual sessions"
```

---

## Task 14: API Routes

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/chat.py`
- Modify: `apps/server/src/anima_server/api/routes/ws.py`

- [ ] **Step 1: Update chat.py endpoints**

Add `runtime_db` dependency to endpoints that touch messages:

```python
from anima_server.db.runtime import get_runtime_db
```

`send_message`:
```python
@router.post("", response_model=ChatResponse)
async def send_message(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> ChatResponse | StreamingResponse:
    ...
    result = await run_agent(payload.message, payload.userId, db, runtime_db, source=payload.source)
    ...
    async for event in stream_agent(payload.message, payload.userId, db, runtime_db, source=payload.source):
```

`get_chat_history` — use `runtime_db` only (messages are in PG, no encryption):
```python
@router.get("/history", response_model=list[ChatHistoryMessage])
async def get_chat_history(
    request: Request,
    userId: int = Query(ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    runtime_db: Session = Depends(get_runtime_db),
) -> list[ChatHistoryMessage]:
    require_unlocked_user(request, userId)
    rows = list_agent_history(userId, runtime_db, limit=limit)
    return [
        ChatHistoryMessage(
            id=row.id,
            userId=userId,
            role="assistant" if row.role == "tool" else row.role,
            content=row.content_text or "",  # no df() needed
            createdAt=row.created_at,
            source=getattr(row, "source", None),
        )
        for row in rows
    ]
```

`clear_chat_history` and `reset_chat_thread` — use `runtime_db`:
```python
async def clear_chat_history(..., runtime_db: Session = Depends(get_runtime_db)):
    await reset_agent_thread(payload.userId, runtime_db)

async def reset_chat_thread(..., runtime_db: Session = Depends(get_runtime_db)):
    await reset_agent_thread(payload.userId, runtime_db)
```

`get_home` — message count from `runtime_db`, memory count from `db`:
```python
async def get_home(
    ...,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
):
    ...
    from anima_server.models.runtime import RuntimeMessage, RuntimeThread
    message_count = (
        runtime_db.scalar(
            select(func.count(RuntimeMessage.id))
            .join(RuntimeThread, RuntimeMessage.thread_id == RuntimeThread.id)
            .where(RuntimeThread.user_id == userId)
        )
        or 0
    )
```

`cancel_run` — use `runtime_db`:
```python
async def cancel_run(
    ...,
    runtime_db: Session = Depends(get_runtime_db),
):
    from anima_server.models.runtime import RuntimeRun
    run = runtime_db.get(RuntimeRun, run_id)
    ...
    cancelled = await cancel_agent_run(run_id, payload.userId, runtime_db)
```

`dry_run` — needs both:
```python
async def dry_run(
    ...,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
):
    result = await dry_run_agent(payload.message, payload.userId, db, runtime_db)
```

`handle_approval` — needs both:
```python
async def handle_approval(
    ...,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
):
    from anima_server.models.runtime import RuntimeRun
    run = runtime_db.get(RuntimeRun, run_id)
    ...
```

Endpoints that only touch soul data (`get_brief`, `get_greeting`, `get_nudges`, `consolidate`, `trigger_sleep_tasks`, `trigger_deep_monologue`) keep the single `db` dependency.

- [ ] **Step 2: Update ws.py — create runtime session alongside soul session**

In `_handle_user_message`:

```python
async def _handle_user_message(conn: ClientConnection, data: dict) -> None:
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.services.agent.delegation import ToolDelegator
    from anima_server.services.agent.service import stream_agent

    message = data.get("message", "")
    delegator = ToolDelegator(send_fn=lambda msg: conn.websocket.send_json(msg))
    conn._delegator = delegator

    action_tool_names = registry.get_action_tool_names(conn.user_id)
    action_tool_schemas = registry.get_action_tool_schemas(conn.user_id)

    db = get_user_session_factory(conn.user_id)()
    runtime_db = get_runtime_session_factory()()
    try:
        async for event in stream_agent(
            message,
            conn.user_id,
            db,
            runtime_db,
            tool_delegate=delegator.delegate,
            delegated_tool_names=action_tool_names,
            extra_tool_schemas=action_tool_schemas,
        ):
            ws_msg = _translate_event(event)
            if ws_msg is not None:
                await conn.websocket.send_json(ws_msg)
    except Exception as exc:
        logger.exception("Agent error for user_id=%d", conn.user_id)
        await conn.websocket.send_json({"type": "error", "message": str(exc), "code": "AGENT_ERROR"})
    finally:
        runtime_db.close()
        db.close()
        conn._delegator = None
```

- [ ] **Step 3: Commit**

```bash
git add apps/server/src/anima_server/api/routes/chat.py apps/server/src/anima_server/api/routes/ws.py
git commit -m "feat(p2): API routes use runtime_db for message operations"
```

---

## Task 15: Update approve_or_deny in service.py

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py`

- [ ] **Step 1: Update `approve_or_deny_turn` and `stream_approve_or_deny`**

These need both `db` and `runtime_db`:

```python
async def approve_or_deny_turn(
    run_id: int,
    user_id: int,
    approved: bool,
    db: Session,
    runtime_db: Session,
    *,
    denial_reason: str | None = None,
    event_callback: ...,
) -> AgentResult:
    checkpoint = load_approval_checkpoint(runtime_db, run_id)
    ...
    clear_approval_checkpoint(runtime_db, run, approval_msg)
    runtime_db.flush()
    ...
    thread = runtime_db.get(RuntimeThread, run.thread_id)
    ...
    history = companion.ensure_history_loaded(runtime_db)
    memory_blocks = companion.ensure_memory_loaded(db)
    conversation_turn_count = count_messages_by_role(runtime_db, thread.id, "user")
    set_tool_context(ToolContext(db=db, runtime_db=runtime_db, ...))
    ...
```

`stream_approve_or_deny` also gets `runtime_db`.

- [ ] **Step 2: Commit**

```bash
git add apps/server/src/anima_server/services/agent/service.py
git commit -m "feat(p2): approve/deny uses runtime_db"
```

---

## Task 16: Test Fixtures and Core Tests

**Files:**
- Create: `apps/server/tests/conftest_runtime.py`
- Create: `apps/server/tests/test_runtime_persistence.py`

- [ ] **Step 1: Create runtime test fixtures**

Create `apps/server/tests/conftest_runtime.py` with fixtures that provide a real PG session (using embedded PG or a test PG instance):

```python
from __future__ import annotations

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import runtime as _runtime_models  # noqa: F401


@pytest.fixture(scope="session")
def runtime_engine():
    """Create a test PG engine.

    Uses ANIMA_TEST_RUNTIME_DATABASE_URL if set, otherwise tries embedded PG.
    Falls back to SQLite for basic structural tests (some PG features won't work).
    """
    url = os.environ.get("ANIMA_TEST_RUNTIME_DATABASE_URL", "")
    if not url:
        try:
            from anima_server.db.pg_lifecycle import EmbeddedPG
            from pathlib import Path
            import tempfile

            pg_data = Path(tempfile.mkdtemp(prefix="anima-test-pg-"))
            pg = EmbeddedPG(data_dir=pg_data)
            pg.start()
            url = EmbeddedPG.to_sync_url(pg.database_url)
        except Exception:
            pytest.skip("No PG available for runtime tests")

    engine = create_engine(url, echo=False)
    RuntimeBase.metadata.create_all(engine)
    yield engine
    RuntimeBase.metadata.drop_all(engine)
    engine.dispose()
    if "pg" in dir():
        pg.stop()


@pytest.fixture()
def runtime_db(runtime_engine):
    """Yield a runtime session that rolls back after each test."""
    connection = runtime_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()
```

- [ ] **Step 2: Create basic runtime persistence tests**

Create `apps/server/tests/test_runtime_persistence.py`:

```python
from __future__ import annotations

import pytest
from anima_server.models.runtime import RuntimeThread, RuntimeMessage, RuntimeRun, RuntimeStep
from anima_server.services.agent.persistence import (
    get_or_create_thread,
    append_message,
    create_run,
    create_step,
    load_thread_history,
    finalize_run,
)
from anima_server.services.agent.sequencing import reserve_message_sequences
from anima_server.services.agent.compaction import estimate_message_tokens
from anima_server.services.agent.runtime_types import StepTrace, UsageStats
from anima_server.services.agent.state import AgentResult

pytest_plugins = ["tests.conftest_runtime"]


class TestRuntimeThreadCRUD:
    def test_create_thread(self, runtime_db):
        thread = get_or_create_thread(runtime_db, user_id=1)
        assert thread.id is not None
        assert thread.user_id == 1
        assert thread.status == "active"
        assert thread.next_message_sequence == 1

    def test_get_existing_thread(self, runtime_db):
        t1 = get_or_create_thread(runtime_db, user_id=1)
        t2 = get_or_create_thread(runtime_db, user_id=1)
        assert t1.id == t2.id


class TestRuntimeMessageCRUD:
    def test_append_and_load(self, runtime_db):
        thread = get_or_create_thread(runtime_db, user_id=1)
        seq = reserve_message_sequences(runtime_db, thread_id=thread.id, count=1)
        append_message(
            runtime_db,
            thread=thread,
            run_id=None,
            step_id=None,
            sequence_id=seq,
            role="user",
            content_text="Hello",
        )
        runtime_db.flush()

        history = load_thread_history(runtime_db, thread.id)
        assert len(history) == 1
        assert history[0].role == "user"
        assert history[0].content == "Hello"

    def test_content_not_encrypted(self, runtime_db):
        """Messages stored in PG are plaintext — no ef/df."""
        thread = get_or_create_thread(runtime_db, user_id=1)
        seq = reserve_message_sequences(runtime_db, thread_id=thread.id, count=1)
        msg = append_message(
            runtime_db,
            thread=thread,
            run_id=None,
            step_id=None,
            sequence_id=seq,
            role="user",
            content_text="Sensitive info",
        )
        runtime_db.flush()

        # Read raw from DB — should be plaintext
        from sqlalchemy import select
        row = runtime_db.scalar(
            select(RuntimeMessage).where(RuntimeMessage.id == msg.id)
        )
        assert row.content_text == "Sensitive info"


class TestSequenceReservation:
    def test_reserve_sequences(self, runtime_db):
        thread = get_or_create_thread(runtime_db, user_id=1)
        start1 = reserve_message_sequences(runtime_db, thread_id=thread.id, count=3)
        start2 = reserve_message_sequences(runtime_db, thread_id=thread.id, count=2)
        assert start1 == 1
        assert start2 == 4

    def test_reserve_zero_raises(self, runtime_db):
        thread = get_or_create_thread(runtime_db, user_id=1)
        with pytest.raises(ValueError):
            reserve_message_sequences(runtime_db, thread_id=thread.id, count=0)
```

- [ ] **Step 3: Run tests**

Run: `cd apps/server && python -m pytest tests/test_runtime_persistence.py -v`

- [ ] **Step 4: Commit**

```bash
git add apps/server/tests/conftest_runtime.py apps/server/tests/test_runtime_persistence.py
git commit -m "test(p2): runtime persistence unit tests with PG fixtures"
```

---

## Task 17: Update Existing Tests

**Files:**
- Modify: `apps/server/tests/test_agent_service.py`
- Modify: `apps/server/tests/test_agent_persistence.py`
- Modify: `apps/server/tests/test_agent_compaction.py`
- Modify: `apps/server/tests/test_companion.py`
- Modify: `apps/server/tests/test_sleep_agent.py`

- [ ] **Step 1: Update test files to provide runtime sessions**

Each test file that calls functions now requiring `runtime_db` needs to be updated. The exact changes depend on the current test structure, but the general pattern is:

1. Import `conftest_runtime` fixtures
2. Replace soul-session calls to persistence/sequencing/compaction with runtime-session calls
3. Update service function calls to pass both sessions

- [ ] **Step 2: Run full test suite**

Run: `cd apps/server && python -m pytest -v`

Verify all tests pass. Fix any breakage from the dual-session migration.

- [ ] **Step 3: Commit**

```bash
git add apps/server/tests/
git commit -m "test(p2): update existing tests for dual-session pattern"
```

---

## Task 18: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
cd apps/server && python -m pytest -v --tb=short
```

All 846+ tests should pass.

- [ ] **Step 2: Verify Alembic migrations work**

```bash
cd apps/server && python -c "
from anima_server.db.runtime import init_runtime_engine, ensure_runtime_tables
from anima_server.db.pg_lifecycle import EmbeddedPG
from pathlib import Path
import tempfile

pg = EmbeddedPG(data_dir=Path(tempfile.mkdtemp()) / 'pg_data')
pg.start()
init_runtime_engine(pg.database_url)
ensure_runtime_tables()
print('Runtime migrations OK')
pg.stop()
"
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(p2): runtime messages migration complete"
```
