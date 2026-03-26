---
title: "Phase 4: Write Boundary + Pending Memory Ops"
description: Enforce the architectural invariant that runtime never writes to Soul; introduce PendingMemoryOp staging in PostgreSQL and rewire core_memory tools to write there instead of directly to SelfModelBlock.
category: prd
version: "1.0"
---

# Phase 4: Write Boundary + Pending Memory Ops

**Version**: 1.0
**Date**: 2026-03-26
**Status**: Proposed
**Depends on**: P3 (Self-Model Split)
**Blocks**: P5 (Transcript Archive), P8 (N-Agent Spawning)

---

## Overview

Phase 4 enforces the single most important architectural invariant of the three-tier architecture: **Runtime never writes to Soul. Only Consolidation does.**

Today, `core_memory_append` and `core_memory_replace` directly mutate `SelfModelBlock` rows in the Soul store (SQLCipher) during an agent turn. This is a runtime write to identity -- exactly what the write boundary forbids. Phase 4 introduces `PendingMemoryOp`, a staging table in PostgreSQL where these tools deposit their operations. The existing consolidation gateway reads pending ops from PostgreSQL and promotes them to the Soul, applying them in causal order with idempotency and failure handling.

The agent's user-facing behavior does not change. `core_memory_append` still returns "Appended to human memory." The agent still sees the updated content in its next reasoning step. But the underlying write goes to PostgreSQL, not SQLCipher.

### Why This Phase Matters

Without the write boundary, N-agent spawning (P8) is unsafe. If the main agent and two spawned agents all call `core_memory_replace` on the same `human` block concurrently, last-writer-wins corrupts identity. The pending-op pattern serializes all identity writes through the consolidation gateway, which processes them in deterministic order.

---

## Scope

### In Scope

1. `PendingMemoryOp` model in PostgreSQL (RuntimeBase)
2. Rewire `core_memory_append` to write `PendingMemoryOp(op_type="append")` to PostgreSQL
3. Rewire `core_memory_replace` to write `PendingMemoryOp(op_type="replace")` to PostgreSQL
4. Rewire `update_human_memory` to write `PendingMemoryOp(op_type="replace")` to PostgreSQL (full-block replace)
5. Consolidation gateway reads pending ops from PostgreSQL, applies them to Soul (SQLCipher)
6. Cross-conversation continuity: pending ops rendered in system prompt alongside soul blocks
7. `soul_writer.py` module that isolates soul-write functions; only consolidation imports it
8. Write boundary enforcement test (lint-level guard)

### Out of Scope

- Moving `save_to_memory` to pending ops (it already writes `MemoryItem` candidates; consolidation handles these through the existing extraction pipeline)
- Changing `recall_memory` or `recall_conversation` (read paths are unchanged)
- Transcript archival (P5)
- pgvector migration (P6)
- Per-thread locking (P7)

---

## Implementation Details

### 1. Tool Rewiring

#### core_memory_append (current behavior)

```python
# Today: direct SQLCipher write
block = db.scalar(select(SelfModelBlock).where(...))
block.content = ef(user_id, (existing + "\n" + content).strip(), ...)
block.version += 1
db.flush()
ctx.memory_modified = True
```

#### core_memory_append (new behavior)

```python
# Phase 4: write PendingMemoryOp to PostgreSQL
from anima_server.services.agent.pending_ops import create_pending_op

create_pending_op(
    runtime_db,
    user_id=ctx.user_id,
    op_type="append",
    target_block=label,            # "human" or "persona"
    content=content.strip(),
    old_content=None,
    source_run_id=ctx.run_id,      # added to ToolContext in this phase
    source_tool_call_id=ctx.current_tool_call_id,  # added to ToolContext
)
ctx.memory_modified = True
```

The tool still returns `"Appended to {label} memory. It will be visible in your next step."` The agent sees the update because pending ops are loaded alongside soul blocks in the system prompt (see Cross-Conversation Continuity below).

#### core_memory_replace (new behavior)

```python
create_pending_op(
    runtime_db,
    user_id=ctx.user_id,
    op_type="replace",
    target_block=label,
    content=new_text.strip(),
    old_content=old_text,
    source_run_id=ctx.run_id,
    source_tool_call_id=ctx.current_tool_call_id,
)
ctx.memory_modified = True
```

**Validation**: The tool still checks that `old_text` exists in the current block content (soul + pending ops merged view) before creating the op. If `old_text` is not found, the tool returns the same error message as today. This catches obvious mismatches at tool-call time rather than deferring all validation to consolidation.

#### update_human_memory (new behavior)

`update_human_memory` does a full-block replace of the `human` section. In the new model, it creates a `PendingMemoryOp` with `op_type="replace"` where `old_content` is the entire existing block and `content` is the full new block.

However, full-block replaces have a subtlety: if two pending ops both try to replace the entire block, the second one's `old_content` won't match what consolidation sees after applying the first. This is handled by using a special `op_type="full_replace"` that unconditionally overwrites the target block, ignoring `old_content`. Consolidation applies the latest `full_replace` for a given `target_block` and skips earlier ones.

```python
create_pending_op(
    runtime_db,
    user_id=ctx.user_id,
    op_type="full_replace",
    target_block="human",
    content=content.strip(),
    old_content=None,   # not used for full_replace
    source_run_id=ctx.run_id,
    source_tool_call_id=ctx.current_tool_call_id,
)
```

### 2. In-Turn Visibility (Merged View)

Within a single conversation turn, the agent must see the effects of its own `core_memory_append`/`core_memory_replace` calls in subsequent reasoning steps. Today, this works because `ctx.memory_modified = True` triggers a memory-block rebuild, which reads the freshly-written `SelfModelBlock`.

In Phase 4, the memory-block builder must merge:
1. The committed soul block from SQLCipher
2. All unconsolidated, unfailed pending ops for the same `target_block` from PostgreSQL, applied in `id` order

This merged view is computed in `memory_blocks.py` and returned as the block the agent sees. The merge is read-only -- it does not mutate either store.

```python
def build_merged_block_content(
    soul_db: Session,
    runtime_db: Session,
    *,
    user_id: int,
    section: str,
) -> str:
    """Return the soul block content with pending ops applied on top."""
    # 1. Read committed content from soul
    base_content = _read_soul_block_content(soul_db, user_id, section)

    # 2. Read unconsolidated pending ops from PostgreSQL
    pending = _get_pending_ops(runtime_db, user_id, section)

    # 3. Apply in order
    for op in pending:
        if op.op_type == "append":
            base_content = (base_content.rstrip() + "\n" + op.content).strip()
        elif op.op_type == "replace":
            if op.old_content and op.old_content in base_content:
                base_content = base_content.replace(op.old_content, op.content, 1)
            # If old_content not found, skip (will fail at consolidation)
        elif op.op_type == "full_replace":
            base_content = op.content

    return base_content
```

### 3. ToolContext Extensions

`ToolContext` gains two fields to support pending op traceability:

```python
@dataclass(slots=True)
class ToolContext:
    db: Session                          # soul DB (SQLCipher) -- reads only
    runtime_db: Session                  # runtime DB (PostgreSQL) -- reads and writes
    user_id: int
    thread_id: int
    run_id: int | None = None            # NEW: current AgentRun id
    current_tool_call_id: str | None = None  # NEW: set per tool call
    memory_modified: bool = False
```

The `current_tool_call_id` is set by the executor before dispatching each tool call. It corresponds to the LLM-generated `tool_call.id` and serves as the idempotency key.

The `runtime_db` field provides the PostgreSQL session. All tool writes (pending ops, session notes, tasks) go through `runtime_db`. Soul reads continue through `db`.

---

## Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `models/pending_memory_op.py` | `PendingMemoryOp` SQLAlchemy model (RuntimeBase) |
| `services/agent/pending_ops.py` | `create_pending_op()`, `get_pending_ops()`, `apply_pending_ops()` |
| `services/agent/soul_writer.py` | Soul-write functions extracted from current inline writes; only consolidation imports this module |

### Modified Files

| File | Changes |
|------|---------|
| `services/agent/tools.py` | Rewire `core_memory_append`, `core_memory_replace`, `update_human_memory` to call `create_pending_op()` instead of writing `SelfModelBlock` directly |
| `services/agent/tool_context.py` | Add `runtime_db`, `run_id`, `current_tool_call_id` fields to `ToolContext` |
| `services/agent/memory_blocks.py` | `build_persona_block()` and `build_human_core_block()` merge soul content with pending ops; new `build_pending_ops_block()` for cross-conversation supplementary block |
| `services/agent/consolidation.py` | Add `consolidate_pending_ops()` function that reads from PostgreSQL, writes to SQLCipher, marks ops as consolidated |
| `services/agent/executor.py` | Set `ctx.current_tool_call_id = tool_call.id` before dispatch |
| `services/agent/service.py` | Pass `runtime_db` and `run_id` when constructing `ToolContext`; call `consolidate_pending_ops()` at appropriate trigger points |
| `api/routes/consciousness.py` | `update_self_model_section()` (user edits via API) writes through `soul_writer.py` (user edits bypass the pending op path -- they are explicit, immediate identity updates from the user) |
| `models/__init__.py` | Export `PendingMemoryOp` |

---

## Models / Schemas

### PendingMemoryOp (PostgreSQL -- RuntimeBase)

```python
from datetime import datetime
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column
from anima_server.db.runtime_base import RuntimeBase


class PendingMemoryOp(RuntimeBase):
    """Staging table for memory operations awaiting consolidation.

    Runtime tools write here. The consolidation gateway reads, applies to Soul,
    and marks as consolidated. The id column defines causal order.
    """
    __tablename__ = "pending_memory_ops"
    __table_args__ = (
        Index("ix_pending_ops_user_pending", "user_id", "consolidated", "failed"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    op_type: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )  # "append" | "replace" | "full_replace"
    target_block: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )  # "human", "persona", "user_facts", etc.
    content: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    old_content: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )  # for replace: text being replaced
    source_run_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    source_tool_call_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )  # idempotency key
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    consolidated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    consolidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    failure_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
```

### Key Design Decisions

**`id` as causal ordering**: Auto-increment integer guarantees that ops created later have higher ids. Consolidation processes ops in ascending `id` order, preserving the causal sequence of tool calls.

**`source_tool_call_id` for idempotency**: If consolidation crashes mid-run and retries, it skips ops whose `source_tool_call_id` has already been consolidated. This prevents double-appending.

**`failed` is terminal**: Failed ops are NOT retried. The rationale: if a replace op fails because `old_content` was already modified by a prior op, the content conflict is real and cannot be resolved by retry. The knowledge is not lost -- it exists in the conversation messages (PostgreSQL) and will be picked up by the LLM extraction pipeline during the next consolidation pass.

**No FK to users**: The `user_id` column does not have a foreign key constraint because `users` lives in SQLCipher (different database). The application enforces referential integrity.

---

## Consolidation Changes

### New Function: `consolidate_pending_ops()`

Added to `services/agent/consolidation.py` (or a new `services/agent/pending_ops.py` module).

```python
async def consolidate_pending_ops(
    *,
    user_id: int,
    soul_db_factory: Callable,
    runtime_db_factory: Callable,
) -> PendingOpsConsolidationResult:
    """Read pending ops from PostgreSQL, apply to Soul (SQLCipher).

    Processing order: ascending by id (causal order).
    Advisory lock: per-user in PostgreSQL to prevent concurrent runs.
    Idempotency: skip ops whose source_tool_call_id is already consolidated.
    """
```

#### Algorithm

```
1. Acquire PG advisory lock for user_id (pg_advisory_xact_lock)
2. SELECT * FROM pending_memory_ops
   WHERE user_id = :uid AND consolidated = false AND failed = false
   ORDER BY id ASC
3. Open SQLCipher session (soul_db)
4. For each op:
   a. Check idempotency: skip if source_tool_call_id already exists
      in a consolidated op
   b. Read current block from SelfModelBlock(section=op.target_block)
   c. Apply:
      - append: content = existing + "\n" + op.content
      - replace: if op.old_content in existing, do replacement; else mark failed
      - full_replace: content = op.content (unconditional)
   d. Write updated block to SelfModelBlock via soul_writer.py
   e. Mark op: consolidated=True, consolidated_at=now()
5. Commit SQLCipher session
6. Commit PostgreSQL session (marks consolidated)
7. Advisory lock released (transaction end)
```

#### Failure Handling

| Failure Mode | Behavior |
|--------------|----------|
| `replace` op: `old_content` not found | Mark `failed=True`, `failure_reason="old_content not found in target block"`. Continue processing remaining ops. |
| SQLCipher write fails | Rollback both sessions. Ops remain unconsolidated. Next run retries. |
| PostgreSQL crashes mid-commit | Ops remain unconsolidated (PostgreSQL transaction rolled back). Idempotent on retry. |
| Process crash after SQLCipher commit but before PG commit | Soul has the update but PG doesn't know. On retry, idempotency check (by `source_tool_call_id`) prevents double-apply. If `source_tool_call_id` is null, the re-apply is a no-op for appends (duplicate content is detectable) or may cause a duplicate append (acceptable -- consolidation can deduplicate via content hash). |

#### Advisory Lock

```python
# Per-user advisory lock prevents concurrent consolidation
runtime_db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": user_id})
```

This is a transaction-level lock -- it releases when the transaction commits or rolls back. No explicit unlock needed.

### Integration Points

`consolidate_pending_ops()` is called from two places:

1. **`run_background_memory_consolidation()`** -- existing background consolidation runs after every turn. Pending ops are processed before LLM extraction so that the soul is up-to-date when extraction runs.

2. **Thread close / eager consolidation** (P5 will formalize this) -- when a conversation ends, a full consolidation runs that includes pending ops + transcript archival.

---

## Cross-Conversation Continuity

### The Problem

User sends a message in Conversation A. The agent calls `core_memory_append("human", "Recently adopted a dog named Biscuit.")`. The op is written to PostgreSQL. Before consolidation runs, the user starts Conversation B. The agent must see the pending update.

### Solution: Supplementary Memory Block

When building the system prompt for a new conversation, `memory_blocks.py` loads both the committed soul blocks and any unconsolidated pending ops. Pending ops are rendered as part of the merged block content.

Two strategies work together:

**Strategy 1: Merged Block Content** (primary)

`build_human_core_block()` and `build_persona_block()` call `build_merged_block_content()` to merge pending ops into the block content before rendering. The agent sees a single coherent block with pending updates already applied. This is the cleanest UX -- the agent does not know that some updates are pending vs committed.

**Strategy 2: Supplementary Block** (fallback for edge cases)

If there are pending ops targeting blocks that don't exist yet in the soul (e.g., the agent appended to a section that has never been seeded), a supplementary memory block is rendered:

```python
def build_pending_ops_block(
    runtime_db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    """Render unconsolidated pending ops as a supplementary memory block.

    Only includes ops for target blocks that could not be merged
    into existing soul blocks (i.e., the soul block does not exist yet).
    """
    ops = get_pending_ops(runtime_db, user_id=user_id)
    if not ops:
        return None

    lines: list[str] = []
    for op in ops:
        if op.op_type == "append":
            lines.append(f"- [{op.target_block}] (pending append): {op.content}")
        elif op.op_type in ("replace", "full_replace"):
            lines.append(f"- [{op.target_block}] (pending update): {op.content}")

    if not lines:
        return None

    return MemoryBlock(
        label="pending_memory_updates",
        description="Memory updates from recent conversations that have not yet been fully integrated. Treat these as current knowledge.",
        value="\n".join(lines),
    )
```

This block is appended after the soul blocks in the system prompt. It only appears when there are unmerged pending ops.

---

## soul_writer.py -- Isolating Soul Writes

### Purpose

All functions that write to `SelfModelBlock` (Soul store) are extracted into a single module: `services/agent/soul_writer.py`. This module is the only code allowed to call `db.add(SelfModelBlock(...))` or mutate `block.content` on a soul model.

### Functions

```python
# services/agent/soul_writer.py

def append_to_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str = "consolidation",
) -> SelfModelBlock:
    """Append content to a SelfModelBlock. Creates the block if it doesn't exist."""

def replace_in_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
    old_content: str,
    new_content: str,
    updated_by: str = "consolidation",
) -> SelfModelBlock | None:
    """Replace old_content with new_content in a SelfModelBlock.
    Returns None if old_content not found."""

def full_replace_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str = "consolidation",
) -> SelfModelBlock:
    """Unconditionally set a SelfModelBlock's content."""

def set_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str,
) -> SelfModelBlock:
    """Create or overwrite a SelfModelBlock. Used by user edits and setup flows."""
```

### Who Can Import soul_writer.py

| Caller | Allowed | Rationale |
|--------|---------|-----------|
| `consolidation.py` | Yes | Consolidation is the only runtime path to soul writes |
| `api/routes/consciousness.py` | Yes | User-initiated edits are explicit identity changes, not runtime writes |
| `api/routes/soul.py` | Yes | User directive edits, same rationale |
| `services/agent/self_model.py` | Yes | `seed_self_model()` and `ensure_self_model_exists()` are setup/provisioning functions |
| `services/agent/tools.py` | **NO** | Tools write pending ops, never soul directly |
| `services/agent/service.py` | **NO** | Agent runtime code, never soul directly |

### Write Boundary Enforcement

A test (and optionally a ruff/ast-based lint rule) verifies the boundary:

```python
def test_write_boundary_enforcement():
    """No runtime module imports soul_writer or writes SelfModelBlock directly."""
    import ast
    from pathlib import Path

    FORBIDDEN_IMPORTERS = {
        "tools.py",
        "service.py",
        "executor.py",
    }
    SOUL_WRITE_INDICATORS = {
        "soul_writer",
        "set_self_model_block",    # old name, must not appear in tools
    }
    ALLOWED_CALLERS = {
        "consolidation.py",
        "consciousness.py",
        "soul.py",
        "self_model.py",
        "soul_writer.py",
    }

    agent_dir = Path("apps/server/src/anima_server/services/agent")
    for py_file in agent_dir.glob("*.py"):
        if py_file.name in ALLOWED_CALLERS:
            continue
        source = py_file.read_text()
        for indicator in SOUL_WRITE_INDICATORS:
            assert indicator not in source, (
                f"Write boundary violation: {py_file.name} references '{indicator}'. "
                f"Runtime code must not import soul-write functions."
            )
```

---

## Test Plan

### Unit Tests

| Test | Description |
|------|-------------|
| `test_create_pending_op_append` | `create_pending_op(op_type="append")` inserts a row in `pending_memory_ops` with correct fields |
| `test_create_pending_op_replace` | `create_pending_op(op_type="replace")` stores `old_content` and `content` |
| `test_create_pending_op_full_replace` | `create_pending_op(op_type="full_replace")` stores `content` with null `old_content` |
| `test_create_pending_op_idempotency_key` | Two ops with different `source_tool_call_id` are both created; duplicate `source_tool_call_id` is handled gracefully |
| `test_get_pending_ops_ordered` | `get_pending_ops()` returns ops in ascending `id` order |
| `test_get_pending_ops_excludes_consolidated` | Consolidated ops are not returned |
| `test_get_pending_ops_excludes_failed` | Failed ops are not returned |

### Integration Tests -- Tool Rewiring

| Test | Description |
|------|-------------|
| `test_core_memory_append_creates_pending_op` | Calling `core_memory_append("human", "new info")` creates a `PendingMemoryOp` in PostgreSQL, not a direct `SelfModelBlock` write |
| `test_core_memory_replace_creates_pending_op` | Calling `core_memory_replace("human", "old", "new")` creates a pending op with `old_content="old"` |
| `test_core_memory_replace_validates_old_content` | If `old_text` is not found in the merged view (soul + pending), the tool returns an error and does NOT create a pending op |
| `test_update_human_memory_creates_full_replace_op` | `update_human_memory("new content")` creates a `full_replace` pending op |
| `test_tool_sets_memory_modified_flag` | After creating a pending op, `ctx.memory_modified` is `True` |
| `test_in_turn_visibility` | Agent calls `core_memory_append("human", "X")`, then in the same turn the rebuilt memory blocks include "X" in the human block |

### Integration Tests -- Consolidation

| Test | Description |
|------|-------------|
| `test_consolidate_append_op` | Pending append op is applied to SelfModelBlock; op marked `consolidated=True` |
| `test_consolidate_replace_op_success` | Pending replace op with matching `old_content` succeeds |
| `test_consolidate_replace_op_failure` | Pending replace op with non-matching `old_content` is marked `failed=True` with reason |
| `test_consolidate_full_replace_op` | Full replace overwrites block content unconditionally |
| `test_consolidate_ordering` | Three ops (append, replace, append) are applied in `id` order; final block content is correct |
| `test_consolidate_idempotent` | Running consolidation twice does not double-apply ops (deduplication by `source_tool_call_id`) |
| `test_consolidate_advisory_lock` | Two concurrent consolidation coroutines for the same user serialize (one waits for the other) |
| `test_consolidate_partial_failure` | If op 2 of 3 fails, ops 1 and 3 are still applied; op 2 is marked failed |

### Integration Tests -- Cross-Conversation Continuity

| Test | Description |
|------|-------------|
| `test_pending_ops_visible_in_new_conversation` | After creating pending ops in conversation A, building memory blocks for conversation B includes the pending content in the merged block |
| `test_pending_ops_block_rendered` | `build_pending_ops_block()` returns a `MemoryBlock` with pending ops when they exist |
| `test_no_pending_ops_block_when_empty` | `build_pending_ops_block()` returns `None` when no pending ops exist |
| `test_merged_view_applies_ops_in_order` | `build_merged_block_content()` applies append, replace, and full_replace in correct order |

### Boundary Enforcement Tests

| Test | Description |
|------|-------------|
| `test_write_boundary_enforcement` | No file in `services/agent/` (except allowed callers) imports `soul_writer` or references soul-write functions |
| `test_tools_py_no_selfmodelblock_write` | `tools.py` does not contain `db.add(SelfModelBlock` or `block.content =` patterns |

### Regression Tests

| Test | Description |
|------|-------------|
| `test_existing_consolidation_still_works` | The existing `consolidate_turn_memory_with_llm()` pipeline continues to function (it writes `MemoryItem` rows through `store_memory_item`, which is a different path) |
| `test_user_edit_bypasses_pending_ops` | `PUT /api/consciousness/{user_id}/self-model/{section}` writes directly to Soul via `soul_writer.py`, not through pending ops |

---

## Acceptance Criteria

1. **`core_memory_append` writes to PostgreSQL**: Calling the tool creates a `PendingMemoryOp` row. No `SelfModelBlock` is written during the agent turn.

2. **`core_memory_replace` writes to PostgreSQL**: Same as above, with `old_content` populated for replace validation.

3. **`update_human_memory` writes to PostgreSQL**: Creates a `full_replace` pending op.

4. **In-turn visibility**: After the agent calls `core_memory_append("human", "X")`, the rebuilt memory blocks in the same turn include "X" in the human block content.

5. **Cross-conversation continuity**: If the user starts a new conversation before consolidation runs, the system prompt includes pending ops merged into the relevant soul blocks.

6. **Consolidation applies ops**: `consolidate_pending_ops()` reads pending ops from PostgreSQL, applies them to SelfModelBlock in SQLCipher in ascending `id` order, and marks them `consolidated=True`.

7. **Consolidation is idempotent**: Running consolidation twice on the same ops does not double-apply (deduplicated by `source_tool_call_id`).

8. **Failed ops are terminal**: A replace op whose `old_content` doesn't match is marked `failed=True` with a reason. It is not retried.

9. **Advisory lock prevents concurrent consolidation**: Two consolidation runs for the same user serialize via `pg_advisory_xact_lock`.

10. **Write boundary enforced**: A test verifies that `tools.py`, `service.py`, and `executor.py` do not import `soul_writer` or reference soul-write functions.

11. **User edits bypass pending ops**: Self-model edits through the REST API (`PUT /api/consciousness/.../self-model/...`) write directly to Soul via `soul_writer.py`. These are explicit user actions, not runtime writes.

12. **All existing tests pass**: No regressions in the 846+ test suite.

---

## Out of Scope

- **`save_to_memory` rewiring**: This tool writes `MemoryItem` rows (discrete searchable facts), not `SelfModelBlock` rows. The existing consolidation extraction pipeline handles `MemoryItem` promotion. No change needed in this phase.
- **`recall_memory` changes**: Read path from SQLCipher is unchanged.
- **`recall_conversation` changes**: Read path from PostgreSQL is unchanged.
- **Transcript archival**: P5 scope.
- **pgvector migration**: P6 scope.
- **Per-thread locking**: P7 scope.
- **Spawn tools**: P8 scope (but this phase enables them by establishing the write boundary).
- **Knowledge graph pending ops**: KG writes (entities, relations) are not routed through `PendingMemoryOp` in this phase. KG placement is a separate decision (see master PRD, section 3.6).
- **Encryption of pending ops**: Pending ops in PostgreSQL are not encrypted in this phase. PostgreSQL is local (embedded) and ephemeral. If encryption of runtime data becomes a requirement, it is handled as a cross-cutting concern across all runtime tables.
