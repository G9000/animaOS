# Multi-Thread Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ChatGPT-style thread history — multiple conversation threads per user, all sharing one soul DB, with archived threads rehydrated from JSONL on continuation.

**Architecture:** Drop the single-active-thread uniqueness constraint. Add `is_archived_history` to `RuntimeMessage` to flag rehydrated messages (visible in UI, excluded from agent context). When a user continues an archived thread, insert old messages as archived history and a summary system message, then run normally.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), Alembic (migrations), TypeScript/React (frontend), existing `transcript_archive.py` for JSONL read/write.

---

## File Map

**Create:**
- `apps/server/alembic_runtime/versions/009_multi_thread.py` — migration
- `apps/server/src/anima_server/services/agent/thread_manager.py` — thread listing, creation, reactivation
- `apps/server/tests/test_multi_thread.py` — tests for new backend logic

**Modify:**
- `apps/server/src/anima_server/models/runtime.py` — add `is_archived_history` column
- `apps/server/src/anima_server/services/agent/persistence.py` — `load_thread_history` filter, `create_thread`, `list_threads`
- `apps/server/src/anima_server/services/agent/service.py` — `run_agent`/`stream_agent`/`_prepare_turn_context` accept `thread_id`
- `apps/server/src/anima_server/api/routes/threads.py` — add GET list, POST create, GET messages endpoints
- `apps/server/src/anima_server/schemas/chat.py` — add `threadId` to `ChatRequest`
- `apps/server/src/anima_server/api/routes/chat.py` — pass `thread_id` to agent calls
- `packages/api-client/src/types.ts` — add `Thread`, `ThreadListResponse`, `ThreadMessagesResponse`
- `packages/api-client/src/client.ts` — add `threads.list`, `threads.create`, `threads.messages`; add `threadId` to chat send
- `apps/desktop/src/pages/chat/Chat.tsx` — thread sidebar, state, switching

---

## Task 1: Alembic migration — drop unique index, add is_archived_history

**Files:**
- Create: `apps/server/alembic_runtime/versions/009_multi_thread.py`

- [ ] **Step 1: Write the migration**

```python
"""Multi-thread support: drop single-active-thread constraint, add is_archived_history."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the constraint that only allowed one active thread per user.
    op.drop_index("uq_runtime_threads_active_user", table_name="runtime_threads")

    # Add is_archived_history to runtime_messages.
    # Messages rehydrated from JSONL archive are flagged here so the agent
    # context loader can skip them (while the UI still shows them).
    op.add_column(
        "runtime_messages",
        sa.Column(
            "is_archived_history",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_runtime_messages_thread_archived_history",
        "runtime_messages",
        ["thread_id", "is_archived_history"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_messages_thread_archived_history",
        table_name="runtime_messages",
    )
    op.drop_column("runtime_messages", "is_archived_history")
    op.create_index(
        "uq_runtime_threads_active_user",
        "runtime_threads",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
```

- [ ] **Step 2: Verify Alembic picks it up**

Run from `apps/server/`:
```bash
python -c "
from anima_server.db.runtime_session import RuntimeSessionLocal
from alembic.config import Config
from alembic import command
cfg = Config('alembic_runtime.ini')
command.heads(cfg)
"
```
Expected: prints `009` (or both `008` and `009` if branched).

- [ ] **Step 3: Commit**

```bash
git add apps/server/alembic_runtime/versions/009_multi_thread.py
git commit -m "feat(migration): multi-thread — drop single-active constraint, add is_archived_history"
```

---

## Task 2: Update RuntimeMessage model

**Files:**
- Modify: `apps/server/src/anima_server/models/runtime.py`

The `RuntimeMessage` SQLAlchemy model needs the new column so ORM operations work.

- [ ] **Step 1: Add is_archived_history to RuntimeMessage**

In `runtime.py`, find the `RuntimeMessage` class. After the `is_in_context` column (line ~225), add:

```python
is_archived_history: Mapped[bool] = mapped_column(
    Boolean,
    nullable=False,
    default=False,
    server_default=text("false"),
)
```

Also update `__table_args__` to add the new index (after the existing index declarations):

```python
Index("ix_runtime_messages_thread_archived_history", "thread_id", "is_archived_history"),
```

- [ ] **Step 2: Quick smoke test — import succeeds**

```bash
cd apps/server
python -c "from anima_server.models.runtime import RuntimeMessage; print(RuntimeMessage.is_archived_history)"
```
Expected: `<sqlalchemy.orm.attributes.InstrumentedAttribute ...>`

- [ ] **Step 3: Commit**

```bash
git add apps/server/src/anima_server/models/runtime.py
git commit -m "feat(model): add RuntimeMessage.is_archived_history"
```

---

## Task 3: Update persistence layer — load_thread_history filter + helpers

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/persistence.py`
- Test: `apps/server/tests/test_multi_thread.py`

Two changes: (1) `load_thread_history` must exclude archived-history rows from agent context. (2) Add `create_thread` and `list_threads` helpers.

- [ ] **Step 1: Write failing tests**

Create `apps/server/tests/test_multi_thread.py`:

```python
"""Tests for multi-thread persistence helpers."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.persistence import (
    append_message,
    create_run,
    get_or_create_thread,
    list_threads,
    load_thread_history,
)
from conftest_runtime import runtime_db_session

_db_session = runtime_db_session

_COUNTER = 0


def _uid() -> int:
    global _COUNTER
    _COUNTER += 1
    return _COUNTER


def test_load_thread_history_excludes_archived_history(db: Session) -> None:
    uid = _uid()
    thread = get_or_create_thread(db, uid)
    run = create_run(db, thread_id=thread.id, user_id=uid, provider="test", model="m", mode="blocking")
    db.flush()

    # Real message (visible to agent)
    real_msg = append_message(
        db,
        thread_id=thread.id,
        user_id=uid,
        run_id=run.id,
        role="user",
        content_text="hello",
    )

    # Archived history message (should NOT appear in agent context)
    archived_msg = append_message(
        db,
        thread_id=thread.id,
        user_id=uid,
        run_id=run.id,
        role="user",
        content_text="old message from archive",
        is_archived_history=True,
    )
    db.flush()

    history = load_thread_history(db, thread.id)
    contents = [m.content for m in history]
    assert "hello" in contents
    assert "old message from archive" not in contents


def test_list_threads_sorted_by_last_message(db: Session) -> None:
    from datetime import UTC, datetime, timedelta

    uid = _uid()
    t1 = RuntimeThread(user_id=uid, status="active", last_message_at=datetime(2026, 1, 1, tzinfo=UTC))
    t2 = RuntimeThread(user_id=uid, status="closed", last_message_at=datetime(2026, 3, 1, tzinfo=UTC))
    db.add_all([t1, t2])
    db.flush()

    threads = list_threads(db, user_id=uid)
    assert len(threads) == 2
    assert threads[0].id == t2.id  # most recent first


def test_list_threads_excludes_other_users(db: Session) -> None:
    uid_a = _uid()
    uid_b = _uid()
    t = RuntimeThread(user_id=uid_a, status="active")
    db.add(t)
    db.flush()

    threads = list_threads(db, user_id=uid_b)
    assert len(threads) == 0


@pytest.fixture
def db(runtime_db_session: Session):
    yield runtime_db_session
```

- [ ] **Step 2: Run tests — expect failures**

```bash
cd apps/server
pytest tests/test_multi_thread.py -v 2>&1 | head -40
```
Expected: `FAILED` — `list_threads` not defined, `append_message` missing `is_archived_history` param.

- [ ] **Step 3: Update load_thread_history to exclude archived_history rows**

In `persistence.py`, find `load_thread_history`. The current WHERE clause is:
```python
RuntimeMessage.is_in_context.is_(True),
RuntimeMessage.role.in_(("user", "assistant", "tool")),
```

Add:
```python
RuntimeMessage.is_archived_history.is_(False),
```

So the full filter becomes:
```python
.where(
    RuntimeMessage.thread_id == thread_id,
    RuntimeMessage.is_in_context.is_(True),
    RuntimeMessage.is_archived_history.is_(False),
    RuntimeMessage.role.in_(("user", "assistant", "tool")),
)
```

- [ ] **Step 4: Update append_message to accept is_archived_history**

Find `append_message` in `persistence.py`. Add parameter `is_archived_history: bool = False` and pass it to the `RuntimeMessage(...)` constructor:

```python
def append_message(
    db: Session,
    *,
    thread_id: int,
    user_id: int,
    run_id: int | None = None,
    step_id: int | None = None,
    role: str,
    content_text: str | None = None,
    content_json: dict[str, object] | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    tool_args_json: dict[str, object] | None = None,
    source: str | None = None,
    is_archived_history: bool = False,   # <-- ADD THIS
) -> RuntimeMessage:
```

And in the `RuntimeMessage(...)` constructor call inside the function body, add:
```python
is_archived_history=is_archived_history,
```

- [ ] **Step 5: Add list_threads helper**

At the end of `persistence.py`, add:

```python
def list_threads(db: Session, user_id: int) -> list[RuntimeThread]:
    """Return all threads for a user sorted by last_message_at DESC."""
    from sqlalchemy import nulls_last

    return list(
        db.scalars(
            select(RuntimeThread)
            .where(RuntimeThread.user_id == user_id)
            .order_by(nulls_last(desc(RuntimeThread.last_message_at)))
        ).all()
    )


def create_thread(db: Session, user_id: int) -> RuntimeThread:
    """Create a new thread (does not flush)."""
    thread = RuntimeThread(user_id=user_id, status="active")
    db.add(thread)
    db.flush()
    return thread
```

- [ ] **Step 6: Run tests — expect pass**

```bash
cd apps/server
pytest tests/test_multi_thread.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 7: Run existing persistence tests to check no regressions**

```bash
cd apps/server
pytest tests/test_agent_persistence.py -v
```
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/server/src/anima_server/services/agent/persistence.py \
        apps/server/tests/test_multi_thread.py
git commit -m "feat(persistence): multi-thread helpers — list_threads, create_thread, is_archived_history filter"
```

---

## Task 4: Thread reactivation service

**Files:**
- Create: `apps/server/src/anima_server/services/agent/thread_manager.py`
- Modify: `apps/server/tests/test_multi_thread.py`

When a user sends a message to an archived/closed thread whose PG messages have been pruned, we rehydrate from JSONL: insert old messages as `is_archived_history=True`, then insert a summary system message.

- [ ] **Step 1: Add reactivation tests to test_multi_thread.py**

Add these test functions to `tests/test_multi_thread.py`:

```python
def test_reactivate_thread_with_pg_messages(db: Session) -> None:
    """If PG messages still exist (within TTL), reactivation sets status=active only."""
    from anima_server.services.agent.thread_manager import reactivate_thread_if_needed

    uid = _uid()
    thread = RuntimeThread(user_id=uid, status="closed", is_archived=True)
    db.add(thread)
    db.flush()

    # Message still in PG (within TTL window)
    msg = RuntimeMessage(
        thread_id=thread.id,
        user_id=uid,
        sequence_id=1,
        role="user",
        content_text="old message still in PG",
        is_in_context=True,
        is_archived_history=False,
    )
    db.add(msg)
    db.flush()

    reactivate_thread_if_needed(db, thread=thread, user_id=uid, transcripts_dir=None, dek=None)

    assert thread.status == "active"
    assert thread.is_archived is False
    # No extra messages were inserted (PG messages suffice)
    msgs = db.scalars(
        select(RuntimeMessage).where(RuntimeMessage.thread_id == thread.id)
    ).all()
    assert len(msgs) == 1


def test_reactivate_thread_from_jsonl(db: Session, tmp_path) -> None:
    """If PG messages are gone, rehydrate from JSONL and insert summary."""
    import json
    from anima_server.services.agent.thread_manager import reactivate_thread_if_needed

    uid = _uid()
    thread = RuntimeThread(user_id=uid, status="closed", is_archived=True)
    db.add(thread)
    db.flush()

    # Write JSONL and meta sidecar
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    jsonl_path = transcripts_dir / f"2026-01-01_thread-{thread.id}.jsonl"
    meta_path = transcripts_dir / f"2026-01-01_thread-{thread.id}.meta.json"

    messages = [
        {"role": "user", "content": "old user message", "ts": "2026-01-01T00:00:00Z", "seq": 1},
        {"role": "assistant", "content": "old reply", "ts": "2026-01-01T00:01:00Z", "seq": 2},
    ]
    jsonl_path.write_text(
        "\n".join(json.dumps(m) for m in messages), encoding="utf-8"
    )
    meta_path.write_text(
        json.dumps({"thread_id": thread.id, "user_id": uid, "summary": "Talked about old stuff"}),
        encoding="utf-8",
    )

    reactivate_thread_if_needed(
        db, thread=thread, user_id=uid, transcripts_dir=transcripts_dir, dek=None
    )

    assert thread.status == "active"
    assert thread.is_archived is False

    all_msgs = db.scalars(
        select(RuntimeMessage)
        .where(RuntimeMessage.thread_id == thread.id)
        .order_by(RuntimeMessage.sequence_id)
    ).all()

    # 2 archived history + 1 summary system message
    assert len(all_msgs) == 3
    archived = [m for m in all_msgs if m.is_archived_history]
    summary_msg = [m for m in all_msgs if not m.is_archived_history]
    assert len(archived) == 2
    assert len(summary_msg) == 1
    assert summary_msg[0].role == "system"
    assert "Talked about old stuff" in (summary_msg[0].content_text or "")
```

- [ ] **Step 2: Run new tests — expect failures**

```bash
cd apps/server
pytest tests/test_multi_thread.py::test_reactivate_thread_with_pg_messages \
       tests/test_multi_thread.py::test_reactivate_thread_from_jsonl -v 2>&1 | head -20
```
Expected: `ImportError` — `thread_manager` does not exist.

- [ ] **Step 3: Create thread_manager.py**

Create `apps/server/src/anima_server/services/agent/thread_manager.py`:

```python
"""Thread lifecycle management: listing, creation, and archive reactivation."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.persistence import append_message, list_threads, create_thread

logger = logging.getLogger(__name__)


def reactivate_thread_if_needed(
    db: Session,
    *,
    thread: RuntimeThread,
    user_id: int,
    transcripts_dir: Path | None,
    dek: bytes | None,
) -> None:
    """Reactivate a closed/archived thread so the agent can continue it.

    If PG messages still exist (within TTL), just flip status to active.
    If messages are gone, rehydrate from JSONL archive and insert a summary
    system message so the agent has context without loading raw history.
    """
    # Check whether PG messages still exist for this thread.
    has_pg_messages = db.scalar(
        select(RuntimeMessage.id)
        .where(RuntimeMessage.thread_id == thread.id)
        .limit(1)
    ) is not None

    if has_pg_messages:
        # Messages still in PG — just reactivate, no rehydration needed.
        _set_active(thread)
        return

    # No PG messages — load from JSONL archive.
    summary = "Previous conversation"  # fallback
    if transcripts_dir is not None:
        messages, summary = _load_from_archive(transcripts_dir, thread_id=thread.id, dek=dek)
        if messages:
            _bulk_insert_archived_history(db, thread=thread, user_id=user_id, messages=messages)

    # Insert summary as a system message so agent has context.
    _insert_summary_message(db, thread=thread, user_id=user_id, summary=summary)
    _set_active(thread)


def _set_active(thread: RuntimeThread) -> None:
    thread.status = "active"
    thread.is_archived = False
    thread.closed_at = None


def _load_from_archive(
    transcripts_dir: Path,
    *,
    thread_id: int,
    dek: bytes | None,
) -> tuple[list[dict], str]:
    """Find and decrypt the JSONL archive for a thread. Returns (messages, summary)."""
    from anima_server.services.agent.transcript_archive import decrypt_transcript

    # Find matching transcript file. Pattern: *_thread-{id}.jsonl or .jsonl.enc
    candidates = list(transcripts_dir.glob(f"*_thread-{thread_id}.jsonl*"))
    enc_candidates = [p for p in candidates if p.suffix in (".jsonl", ".enc")]
    if not enc_candidates:
        logger.warning("No transcript archive found for thread %d", thread_id)
        return [], "Previous conversation"

    enc_path = sorted(enc_candidates)[-1]  # take most recent if multiple
    meta_path = enc_path.parent / enc_path.name.replace(".jsonl.enc", ".meta.json").replace(".jsonl", ".meta.json")

    summary = "Previous conversation"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("summary"):
                summary = str(meta["summary"])
        except (OSError, json.JSONDecodeError):
            pass

    try:
        messages = decrypt_transcript(enc_path, dek=dek, thread_id=thread_id)
    except Exception:
        logger.exception("Failed to decrypt transcript for thread %d", thread_id)
        return [], summary

    return messages, summary


def _bulk_insert_archived_history(
    db: Session,
    *,
    thread: RuntimeThread,
    user_id: int,
    messages: list[dict],
) -> None:
    """Insert JSONL messages into runtime_messages with is_archived_history=True."""
    # Determine starting sequence_id (after any existing messages, though there shouldn't be any).
    max_seq = thread.next_message_sequence
    for i, msg in enumerate(messages):
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", ""))
        if not content and role in ("user", "assistant"):
            continue  # skip empty messages

        db.add(
            RuntimeMessage(
                thread_id=thread.id,
                user_id=user_id,
                sequence_id=max_seq + i,
                role=role,
                content_text=content,
                is_in_context=False,       # excluded from agent context
                is_archived_history=True,  # marked as rehydrated history
            )
        )

    thread.next_message_sequence = max_seq + len(messages)
    db.flush()


def _insert_summary_message(
    db: Session,
    *,
    thread: RuntimeThread,
    user_id: int,
    summary: str,
) -> None:
    """Insert a system message summarizing the previous conversation."""
    seq = thread.next_message_sequence
    db.add(
        RuntimeMessage(
            thread_id=thread.id,
            user_id=user_id,
            sequence_id=seq,
            role="system",
            content_text=f"[Previous conversation summary]: {summary}",
            is_in_context=True,
            is_archived_history=False,
        )
    )
    thread.next_message_sequence = seq + 1
    db.flush()


def get_thread_messages_for_display(
    db: Session,
    *,
    thread: RuntimeThread,
    user_id: int,
    transcripts_dir: Path | None,
    dek: bytes | None,
) -> list[dict]:
    """Return all messages for UI display in chronological order.

    Active threads: query runtime_messages (all rows, including archived history).
    Archived threads (no PG messages): read from JSONL.
    """
    pg_messages = db.scalars(
        select(RuntimeMessage)
        .where(
            RuntimeMessage.thread_id == thread.id,
            RuntimeMessage.role.in_(("user", "assistant", "tool")),
        )
        .order_by(RuntimeMessage.sequence_id)
    ).all()

    if pg_messages:
        return [
            {
                "role": _display_role(m),
                "content": m.content_text or "",
                "ts": m.created_at.isoformat() if m.created_at else None,
                "isArchivedHistory": m.is_archived_history,
            }
            for m in pg_messages
        ]

    # No PG messages — serve from JSONL archive.
    if transcripts_dir is None:
        return []
    messages, _summary = _load_from_archive(transcripts_dir, thread_id=thread.id, dek=dek)
    return [
        {
            "role": str(m.get("role", "user")),
            "content": str(m.get("content", "")),
            "ts": m.get("ts"),
            "isArchivedHistory": True,
        }
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]


def _display_role(msg: RuntimeMessage) -> str:
    if msg.role == "tool" and msg.tool_name == "send_message":
        return "assistant"
    return msg.role
```

- [ ] **Step 4: Run reactivation tests**

```bash
cd apps/server
pytest tests/test_multi_thread.py -v
```
Expected: all tests PASS including the 2 new reactivation tests.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/thread_manager.py \
        apps/server/tests/test_multi_thread.py
git commit -m "feat(threads): thread reactivation — rehydrate JSONL archive with summary context"
```

---

## Task 5: Thread title generation

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py`

On the first user message in a thread, set `thread.title` from the first 60 chars of the message. The right place is in `_prepare_turn_context`, after the thread is resolved and the user message is appended.

- [ ] **Step 1: Add title-generation test to test_multi_thread.py**

Add to `tests/test_multi_thread.py`:

```python
def test_set_thread_title_from_message() -> None:
    from anima_server.services.agent.thread_manager import maybe_set_thread_title

    thread = RuntimeThread(user_id=1, status="active", title=None)
    maybe_set_thread_title(thread, "Tell me about the weather in Paris today please")
    assert thread.title == "Tell me about the weather in Paris today"  # 40 chars, no truncation needed

    # Title already set — do not overwrite
    maybe_set_thread_title(thread, "New message")
    assert thread.title == "Tell me about the weather in Paris today"

    # Long message — truncate at 60 chars
    thread2 = RuntimeThread(user_id=1, status="active", title=None)
    maybe_set_thread_title(thread2, "A" * 100)
    assert len(thread2.title) <= 63  # 60 chars + "..."
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd apps/server
pytest tests/test_multi_thread.py::test_set_thread_title_from_message -v 2>&1 | head -20
```
Expected: `ImportError` — `maybe_set_thread_title` not defined.

- [ ] **Step 3: Add maybe_set_thread_title to thread_manager.py**

Add at the end of `thread_manager.py`:

```python
def maybe_set_thread_title(thread: RuntimeThread, user_message: str) -> None:
    """Set thread.title from the first user message if not already set."""
    if thread.title is not None:
        return
    text = user_message.strip()
    if len(text) <= 60:
        thread.title = text
    else:
        thread.title = text[:60] + "..."
```

- [ ] **Step 4: Call maybe_set_thread_title in _prepare_turn_context**

In `service.py`, find `_prepare_turn_context`. After `thread = get_or_create_thread(runtime_db, user_id)` and before `run = create_run(...)`, add:

```python
from anima_server.services.agent.thread_manager import maybe_set_thread_title
maybe_set_thread_title(thread, user_message)
```

- [ ] **Step 5: Run test**

```bash
cd apps/server
pytest tests/test_multi_thread.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/services/agent/thread_manager.py \
        apps/server/src/anima_server/services/agent/service.py \
        apps/server/tests/test_multi_thread.py
git commit -m "feat(threads): auto-title thread from first user message"
```

---

## Task 6: Wire thread_id through the agent service

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Modify: `apps/server/src/anima_server/schemas/chat.py`

Allow callers to pass an explicit `thread_id`. The agent will use that thread instead of creating/finding the default active one.

- [ ] **Step 1: Add thread_id to ChatRequest schema**

In `schemas/chat.py`, update `ChatRequest`:

```python
class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    userId: int = Field(ge=0)
    stream: bool = False
    source: str | None = None
    threadId: int | None = None   # <-- ADD
```

- [ ] **Step 2: Update run_agent and stream_agent signatures**

In `service.py`:

```python
async def run_agent(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    source: str | None = None,
    thread_id: int | None = None,   # <-- ADD
) -> AgentResult:
    return await _execute_agent_turn(
        user_message, user_id, db, runtime_db,
        source=source,
        thread_id=thread_id,        # <-- ADD
    )
```

```python
async def stream_agent(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    source: str | None = None,
    thread_id: int | None = None,   # <-- ADD
    tool_delegate: ...,
    delegated_tool_names: ...,
    extra_tool_schemas: ...,
) -> AsyncGenerator[AgentStreamEvent, None]:
    ...
    async def worker() -> None:
        try:
            await _execute_agent_turn(
                user_message,
                user_id,
                db,
                runtime_db,
                event_callback=emit,
                source=source,
                thread_id=thread_id,    # <-- ADD
                ...
            )
```

- [ ] **Step 3: Update _execute_agent_turn to resolve explicit thread**

In `_execute_agent_turn`, the current logic is:
```python
if thread_id is not None:
    thread_lock = get_thread_lock(thread_id)
    async with thread_lock:
        return await _execute_agent_turn_locked(...)
```

The existing `thread_id` parameter is already there for locking, but it's not passed to `_execute_agent_turn_locked`. Update so that the explicit `thread_id` is forwarded:

```python
if thread_id is not None:
    thread_lock = get_thread_lock(thread_id)
    async with thread_lock:
        return await _execute_agent_turn_locked(
            user_message,
            user_id,
            db,
            runtime_db,
            thread_id=thread_id,           # <-- ADD (pass explicit id)
            event_callback=event_callback,
            source=source,
            tool_delegate=tool_delegate,
            delegated_tool_names=delegated_tool_names,
            extra_tool_schemas=extra_tool_schemas,
        )
```

- [ ] **Step 4: Update _execute_agent_turn_locked signature**

```python
async def _execute_agent_turn_locked(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    thread_id: int | None = None,          # <-- ADD
    event_callback: ...,
    source: ...,
    tool_delegate: ...,
    delegated_tool_names: ...,
    extra_tool_schemas: ...,
) -> AgentResult:
    thread, run, user_msg, initial_sequence_id, turn_ctx = await _prepare_turn_context(
        user_message,
        user_id,
        db,
        runtime_db,
        event_callback=event_callback,
        source=source,
        thread_id=thread_id,              # <-- ADD
    )
```

- [ ] **Step 5: Update _prepare_turn_context to accept and use thread_id**

```python
async def _prepare_turn_context(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    event_callback: ... = None,
    source: str | None = None,
    thread_id: int | None = None,         # <-- ADD
) -> tuple[RuntimeThread, RuntimeRun, RuntimeMessage, int, _TurnContext]:
    from anima_server.services.agent.thread_manager import (
        maybe_set_thread_title,
        reactivate_thread_if_needed,
    )
    from anima_server.services.data_crypto import get_active_dek
    from anima_server.config import settings as _settings

    companion = _get_companion(user_id)

    if thread_id is not None:
        # Use the explicitly requested thread.
        thread = runtime_db.get(RuntimeThread, thread_id)
        if thread is None or thread.user_id != user_id:
            raise ValueError(f"Thread {thread_id} not found for user {user_id}")
        if thread.status != "active":
            dek = get_active_dek(user_id, "conversations")
            reactivate_thread_if_needed(
                runtime_db,
                thread=thread,
                user_id=user_id,
                transcripts_dir=_settings.data_dir / "transcripts",
                dek=dek,
            )
            runtime_db.flush()
    else:
        thread = get_or_create_thread(runtime_db, user_id)

    maybe_set_thread_title(thread, user_message)
    companion.thread_id = thread.id
    # ... rest of function unchanged ...
```

- [ ] **Step 6: Update chat route to pass thread_id**

In `api/routes/chat.py`, update the `send_message` handler:

For the non-streaming path:
```python
result = await run_agent(
    payload.message,
    payload.userId,
    db,
    runtime_db,
    source=payload.source,
    thread_id=payload.threadId,    # <-- ADD
)
```

For the streaming path (find the `stream_agent(...)` call):
```python
async for event in stream_agent(
    payload.message,
    payload.userId,
    db,
    runtime_db,
    source=payload.source,
    thread_id=payload.threadId,    # <-- ADD
):
```

- [ ] **Step 7: Run existing chat/service tests**

```bash
cd apps/server
pytest tests/test_agent_service.py tests/test_chat.py tests/test_agent_persistence.py -v 2>&1 | tail -20
```
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/server/src/anima_server/services/agent/service.py \
        apps/server/src/anima_server/schemas/chat.py \
        apps/server/src/anima_server/api/routes/chat.py
git commit -m "feat(service): wire thread_id through run_agent/stream_agent for explicit thread selection"
```

---

## Task 7: New thread API endpoints

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/threads.py`

Add `GET /api/threads`, `POST /api/threads`, and `GET /api/threads/{id}/messages`.

- [ ] **Step 1: Update threads.py**

Replace the full content of `threads.py` with:

```python
"""Thread management endpoints."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.config import settings
from anima_server.db import get_db, get_runtime_db
from anima_server.db.session import build_session_factory_for_db
from anima_server.models.runtime import RuntimeThread
from anima_server.services.agent.eager_consolidation import on_thread_close
from anima_server.services.agent.persistence import close_thread, create_thread, list_threads
from anima_server.services.agent.thread_manager import get_thread_messages_for_display
from anima_server.services.data_crypto import get_active_dek

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.get("")
async def list_user_threads(
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """List all threads for the authenticated user, newest first."""
    unlock_session = require_unlocked_session(request)
    threads = list_threads(runtime_db, user_id=unlock_session.user_id)
    return {
        "threads": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "isArchived": t.is_archived,
                "lastMessageAt": t.last_message_at.isoformat() if t.last_message_at else None,
                "createdAt": t.created_at.isoformat() if t.created_at else None,
            }
            for t in threads
        ]
    }


@router.post("")
async def create_new_thread(
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Create a new conversation thread."""
    unlock_session = require_unlocked_session(request)
    thread = create_thread(runtime_db, user_id=unlock_session.user_id)
    runtime_db.commit()
    return {"threadId": thread.id, "status": thread.status}


@router.get("/{thread_id}/messages")
async def get_thread_messages(
    thread_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Return all messages for a thread (active from PG, archived from JSONL)."""
    unlock_session = require_unlocked_session(request)
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None or thread.user_id != unlock_session.user_id:
        raise HTTPException(status_code=404, detail="Thread not found")

    dek = get_active_dek(unlock_session.user_id, "conversations")
    transcripts_dir: Path = settings.data_dir / "transcripts"

    messages = get_thread_messages_for_display(
        runtime_db,
        thread=thread,
        user_id=unlock_session.user_id,
        transcripts_dir=transcripts_dir,
        dek=dek,
    )
    return {"threadId": thread_id, "messages": messages}


@router.post("/{thread_id}/close")
async def close_thread_endpoint(
    thread_id: int,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Close a thread and trigger background consolidation."""
    unlock_session = require_unlocked_session(request)
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None or thread.user_id != unlock_session.user_id:
        raise HTTPException(status_code=404, detail="Thread not found")

    if thread.status == "closed":
        return {"status": "already_closed", "threadId": thread_id}

    changed = close_thread(runtime_db, thread_id=thread_id)
    runtime_db.commit()

    if changed:
        soul_db_factory = build_session_factory_for_db(db)
        asyncio.get_running_loop().create_task(
            on_thread_close(
                thread_id=thread_id,
                user_id=thread.user_id,
                soul_db_factory=soul_db_factory,
            )
        )

    return {"status": "closed", "threadId": thread_id}
```

- [ ] **Step 2: Smoke test the routes exist**

```bash
cd apps/server
python -c "
from anima_server.api.routes.threads import router
routes = [r.path for r in router.routes]
print(routes)
assert '/api/threads' in routes or any('threads' in r for r in routes)
print('OK')
"
```
Expected: prints route paths, then `OK`.

- [ ] **Step 3: Run existing thread tests**

```bash
cd apps/server
pytest tests/ -k "thread" -v 2>&1 | tail -20
```
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/api/routes/threads.py
git commit -m "feat(api): thread endpoints — list, create, messages"
```

---

## Task 8: API client — Thread types and methods

**Files:**
- Modify: `packages/api-client/src/types.ts`
- Modify: `packages/api-client/src/client.ts`

- [ ] **Step 1: Add Thread types to types.ts**

At the end of `packages/api-client/src/types.ts`, add:

```typescript
export interface Thread {
  id: number;
  title: string | null;
  status: string;
  isArchived: boolean;
  lastMessageAt: string | null;
  createdAt: string | null;
}

export interface ThreadListResponse {
  threads: Thread[];
}

export interface ThreadMessage {
  role: string;
  content: string;
  ts: string | null;
  isArchivedHistory: boolean;
}

export interface ThreadMessagesResponse {
  threadId: number;
  messages: ThreadMessage[];
}

export interface CreateThreadResponse {
  threadId: number;
  status: string;
}
```

- [ ] **Step 2: Add thread methods to client.ts**

In `client.ts`, find the `threads` section (it currently has `close`). Replace or extend it so it has all four methods:

```typescript
threads: {
  list: (): Promise<ThreadListResponse> =>
    this.request<ThreadListResponse>("/api/threads", { method: "GET" }),

  create: (): Promise<CreateThreadResponse> =>
    this.request<CreateThreadResponse>("/api/threads", { method: "POST" }),

  messages: (threadId: number): Promise<ThreadMessagesResponse> =>
    this.request<ThreadMessagesResponse>(`/api/threads/${threadId}/messages`, {
      method: "GET",
    }),

  close: (threadId: number): Promise<{ status: string; threadId: number }> =>
    this.request(`/api/threads/${threadId}/close`, { method: "POST" }),
},
```

- [ ] **Step 3: Add threadId to chat send**

Find the `chat` section in `client.ts`. Update `streamMessage` (or equivalent send method) to accept `threadId`:

```typescript
chat: {
  streamMessage: (
    message: string,
    userId: number,
    options: {
      source?: string;
      threadId?: number;        // <-- ADD
      onEvent?: (event: TraceEvent) => void;
      onChunk?: (chunk: string) => void;
      onDone?: () => void;
      signal?: AbortSignal;
    } = {}
  ): Promise<AgentResponse> => {
    // In the request body, include threadId:
    // body: JSON.stringify({ message, userId, stream: true, source, threadId })
```

Find the actual body serialization in the send method and add `threadId: options.threadId` to the JSON body.

- [ ] **Step 4: TypeScript build check**

```bash
cd packages/api-client
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add packages/api-client/src/types.ts packages/api-client/src/client.ts
git commit -m "feat(api-client): thread list/create/messages methods + threadId in chat send"
```

---

## Task 9: Frontend — Thread sidebar and switching

**Files:**
- Modify: `apps/desktop/src/pages/chat/Chat.tsx`

Add a collapsible left sidebar listing threads. Clicking a thread loads its messages. "New chat" creates a new thread.

- [ ] **Step 1: Read the current Chat.tsx**

```bash
# Read the file to understand current state before editing
# Note: Chat.tsx is large — focus on the top-level state declarations and JSX return
```

Use the Read tool to load `apps/desktop/src/pages/chat/Chat.tsx` before editing.

- [ ] **Step 2: Add thread state**

At the top of the `Chat` component, add after existing state declarations:

```typescript
const [threads, setThreads] = useState<Thread[]>([])
const [currentThreadId, setCurrentThreadId] = useState<number | null>(null)
const [sidebarOpen, setSidebarOpen] = useState(true)
```

Import `Thread`, `ThreadMessage` from the api-client types:
```typescript
import type { Thread, ThreadMessage } from '@anima/api-client'
```

- [ ] **Step 3: Load threads on mount**

Add a `useEffect` to load the thread list when the component mounts (after existing effects):

```typescript
useEffect(() => {
  api.threads.list().then((res) => {
    setThreads(res.threads)
    // If there's an active thread, select it
    const active = res.threads.find((t) => t.status === 'active')
    if (active) setCurrentThreadId(active.id)
  }).catch(() => {/* silently ignore on first load */})
}, [])
```

- [ ] **Step 4: Add thread switch handler**

```typescript
const handleSelectThread = async (threadId: number) => {
  setCurrentThreadId(threadId)
  setMessages([])  // clear current messages while loading
  try {
    const res = await api.threads.messages(threadId)
    // Map ThreadMessage[] to the local message shape used by the Chat component
    const mapped = res.messages
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m, i) => ({
        id: i,
        userId: userId,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        createdAt: m.ts ?? undefined,
      }))
    setMessages(mapped)
  } catch {
    // restore thread id on failure
    setCurrentThreadId(null)
  }
}
```

- [ ] **Step 5: Add new thread handler**

```typescript
const handleNewThread = async () => {
  const res = await api.threads.create()
  const newThread: Thread = {
    id: res.threadId,
    title: null,
    status: 'active',
    isArchived: false,
    lastMessageAt: null,
    createdAt: new Date().toISOString(),
  }
  setThreads((prev) => [newThread, ...prev])
  setCurrentThreadId(res.threadId)
  setMessages([])
}
```

- [ ] **Step 6: Pass threadId in message send**

Find where `api.chat.streamMessage` (or equivalent) is called in the submit/send handler. Add `threadId: currentThreadId ?? undefined`:

```typescript
await api.chat.streamMessage(inputText, userId, {
  threadId: currentThreadId ?? undefined,
  onEvent: handleTraceEvent,
  onChunk: handleChunk,
  onDone: handleDone,
  signal: abortController.signal,
})
```

- [ ] **Step 7: Update thread list when done event arrives**

In the trace event handler (where `evt.type === 'done'` is handled), also refresh the thread title:

```typescript
if (evt.type === 'done' && evt.threadId != null) {
  currentThreadIdRef.current = evt.threadId
  setCurrentThreadId(evt.threadId)
  // Refresh thread list to pick up new title
  api.threads.list().then((res) => setThreads(res.threads)).catch(() => {})
}
```

- [ ] **Step 8: Add sidebar JSX**

In the component's JSX return, wrap the existing chat area and add a sidebar before it. The overall structure:

```tsx
<div className="flex h-full">
  {/* Sidebar */}
  {sidebarOpen && (
    <div className="w-60 flex-shrink-0 border-r flex flex-col">
      <div className="p-2 border-b">
        <button
          onClick={handleNewThread}
          className="w-full text-left px-3 py-2 rounded hover:bg-accent text-sm font-medium"
        >
          + New chat
        </button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {threads.map((thread) => (
          <button
            key={thread.id}
            onClick={() => handleSelectThread(thread.id)}
            className={`w-full text-left px-3 py-2 text-sm truncate hover:bg-accent ${
              thread.id === currentThreadId ? 'bg-accent font-medium' : ''
            }`}
          >
            <div className="truncate">{thread.title ?? 'New conversation'}</div>
            <div className="text-xs text-muted-foreground">
              {thread.lastMessageAt
                ? new Date(thread.lastMessageAt).toLocaleDateString()
                : ''}
            </div>
          </button>
        ))}
      </div>
    </div>
  )}

  {/* Main chat area — existing JSX goes here */}
  <div className="flex-1 flex flex-col min-w-0">
    {/* toggle sidebar button */}
    <button
      onClick={() => setSidebarOpen((v) => !v)}
      className="absolute top-2 left-2 z-10 p-1 rounded hover:bg-accent"
      aria-label="Toggle sidebar"
    >
      ☰
    </button>
    {/* ... rest of existing chat JSX ... */}
  </div>
</div>
```

- [ ] **Step 9: TypeScript check**

```bash
cd apps/desktop
npx tsc --noEmit
```
Expected: no errors. Fix any type errors before proceeding.

- [ ] **Step 10: Manual smoke test**

Start the server and desktop app. Verify:
1. Thread sidebar appears on the left
2. Existing conversation shows in sidebar
3. "New chat" creates a new thread, messages clear
4. After sending a message, the thread gets a title (first 60 chars of message)
5. Switching to a previous thread loads its messages

- [ ] **Step 11: Commit**

```bash
git add apps/desktop/src/pages/chat/Chat.tsx
git commit -m "feat(ui): thread sidebar — list, create, switch, title display"
```

---

## Task 10: Full regression run

- [ ] **Step 1: Run full backend test suite**

```bash
cd apps/server
pytest tests/ -x --ignore=tests/test_health_api.py --ignore=tests/test_event_logger.py -q 2>&1 | tail -30
```
Expected: all pass (the two ignored files have pre-existing unrelated failures).

- [ ] **Step 2: Run multi-thread tests explicitly**

```bash
cd apps/server
pytest tests/test_multi_thread.py -v
```
Expected: all PASS.

- [ ] **Step 3: Final commit**

```bash
git add -A
git status  # review before committing
git commit -m "feat: multi-thread chat — thread history, sidebar, archive reactivation"
```
