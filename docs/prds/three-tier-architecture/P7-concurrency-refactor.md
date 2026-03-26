---
title: "Phase 7: Concurrency Refactor"
description: Replace per-user turn serialization with per-thread locking and eliminate shared mutable state in ToolExecutor, enabling concurrent agent threads for N-agent spawning in P8
category: prd
version: "1.0"
---

# Phase 7: Concurrency Refactor

**Version**: 1.0
**Date**: 2026-03-26
**Status**: Proposed
**Depends on**: P2 (Runtime Messages -- threads/runs live in PostgreSQL)
**Enables**: P8 (N-Agent Spawning)
**Estimated scope**: 1 PR, ~300 lines changed

---

## Overview

AnimaOS currently serializes all agent activity per user behind a single asyncio lock (`get_user_lock(user_id)`) and shares mutable delegation state on a singleton `ToolExecutor`. Both of these are hard blockers for N-agent spawning: a spawned background agent would either block the main conversation or race on shared mutable fields.

This phase makes two surgical changes:

1. **Turn coordinator** -- Replace per-user locking with per-thread locking so that the main conversation thread and any number of spawn threads can execute turns concurrently without blocking each other.
2. **ToolExecutor** -- Eliminate shared mutable state by creating a new `ToolExecutor` instance per invocation, with delegation configuration passed at construction time rather than mutated at runtime.

Neither change alters the agent's behavior from the user's perspective. A single-agent system with one main thread behaves identically -- it just acquires a thread lock instead of a user lock, and creates a fresh executor instead of reusing a shared one. The changes are purely structural, unlocking concurrency without adding it.

---

## Scope

### In scope

- Rewrite `turn_coordinator.py` to key locks by `thread_id` instead of `user_id`
- Refactor `ToolExecutor` to accept delegation config at construction time and remove `set_delegation()` / `clear_delegation()` mutation methods
- Update `service.py` to pass `thread_id` to lock acquisition and construct per-turn executors
- Update `runtime.py` to accept a `ToolExecutor` per invocation (or build one internally)
- Regression tests confirming single-agent behavior is unchanged
- Concurrency tests confirming two threads can execute turns simultaneously

### Out of scope

- SpawnManager, spawn tools, or any spawn lifecycle (P8)
- LLM semaphore for concurrent inference gating (P8)
- Database schema changes (none required)
- Changes to `companion.py` cache structure (companion remains per-user; threads within a user share the same companion)
- Changes to `tool_context.py` (already uses `contextvars.ContextVar`, which is per-task safe)

---

## Implementation Details

### 1. Turn Coordinator: Per-Thread Locking

**Current state** (`turn_coordinator.py`):

```python
_user_locks: OrderedDict[int, asyncio.Lock] = OrderedDict()

def get_user_lock(user_id: int) -> asyncio.Lock:
    # LRU cache of asyncio.Lock instances keyed by user_id
    # Max 256 entries, evicts oldest unlocked entry
```

**New state**:

```python
_MAX_THREAD_LOCKS = 512

_global_lock = Lock()
_thread_locks: OrderedDict[int, asyncio.Lock] = OrderedDict()


def get_thread_lock(thread_id: int) -> asyncio.Lock:
    """Return a per-thread asyncio.Lock, creating one if needed.

    Evicts the least-recently-used entry when the cache exceeds
    ``_MAX_THREAD_LOCKS``, skipping any lock that is currently held.
    """
    with _global_lock:
        lock = _thread_locks.get(thread_id)
        if lock is not None:
            _thread_locks.move_to_end(thread_id)
            return lock

        lock = asyncio.Lock()
        _thread_locks[thread_id] = lock

        while len(_thread_locks) > _MAX_THREAD_LOCKS:
            oldest_id, oldest_lock = next(iter(_thread_locks.items()))
            if oldest_lock.locked():
                break
            _thread_locks.pop(oldest_id)

        return lock
```

Key design decisions:

- **Cache size increased to 512**: With N-agent spawning, each spawn gets its own thread. A user running 10 concurrent spawns creates 11 thread locks (1 main + 10 spawns). 512 accommodates ~46 users at max spawn concurrency. This is generous but memory cost per lock is negligible.
- **Same LRU eviction pattern**: Proven safe -- skips locked entries, evicts oldest unlocked. No behavioral change.
- **Backward compatibility shim**: `get_user_lock` is retained as a deprecated alias that raises a warning, pointing callers to `get_thread_lock`. This prevents silent breakage in any code path that still references the old function.

```python
def get_user_lock(user_id: int) -> asyncio.Lock:
    """Deprecated. Use get_thread_lock(thread_id) instead."""
    import warnings
    warnings.warn(
        "get_user_lock() is deprecated. Use get_thread_lock(thread_id).",
        DeprecationWarning,
        stacklevel=2,
    )
    # Fall through to thread lock with user_id as key.
    # This is a temporary shim -- callers must be migrated.
    return get_thread_lock(user_id)
```

### 2. ToolExecutor: Per-Invocation Construction (Option A)

**Current state** (`executor.py`):

```python
class ToolExecutor:
    def __init__(self, tools: list[Any]) -> None:
        self._tools = {name: tool for ...}
        self._tool_delegate = None             # mutable!
        self._delegated_tool_names = frozenset()  # mutable!

    def set_delegation(self, delegate, tool_names):  # called per-turn
        self._tool_delegate = delegate
        self._delegated_tool_names = tool_names

    def clear_delegation(self):  # called in finally block
        self._tool_delegate = None
        self._delegated_tool_names = frozenset()
```

The problem: `set_delegation` and `clear_delegation` mutate shared state on a singleton. If two turns run concurrently (main + spawn), one turn's delegation overwrites the other's. The `try/finally` cleanup in `_invoke_turn_runtime` cannot prevent races because both turns interleave within the same `ToolExecutor` instance.

**New state**:

```python
class ToolExecutor:
    def __init__(
        self,
        tools: list[Any],
        *,
        delegate: Callable[..., Awaitable[Any]] | None = None,
        delegated_tool_names: frozenset[str] = frozenset(),
    ) -> None:
        self._tools = {name: tool for ...}
        self._delegate = delegate                   # immutable after construction
        self._delegated_tool_names = delegated_tool_names  # immutable after construction

    # set_delegation() and clear_delegation() are REMOVED
```

Delegation is now a constructor parameter. Each turn that needs delegation creates a new `ToolExecutor` with the appropriate config. Turns without delegation use a shared base executor (no delegation = no mutation = safe to share).

The `ToolExecutor` is lightweight. Its `__init__` builds a `dict[str, tool]` from the tools list -- the same tools list that already exists on the `AgentRuntime`. Construction cost is a single dict comprehension over ~15 tools, which is negligible compared to a single LLM call.

### 3. AgentRuntime: Accept Executor Per Invocation

**Current state** (`runtime.py`):

```python
class AgentRuntime:
    def __init__(self, *, ..., tool_executor: ToolExecutor | None = None):
        self._tool_executor = tool_executor or ToolExecutor(...)

    # All invoke paths use self._tool_executor
```

**New state**:

```python
class AgentRuntime:
    def __init__(self, *, ..., tool_executor: ToolExecutor | None = None):
        self._tool_executor = tool_executor or ToolExecutor(...)

    async def invoke(
        self,
        ...,
        tool_executor: ToolExecutor | None = None,  # NEW: per-invocation override
    ) -> AgentResult | DryRunResult:
        executor = tool_executor or self._tool_executor
        # All internal calls use `executor` instead of `self._tool_executor`
```

The `tool_executor` parameter on `invoke()` (and `resume_after_approval()`) allows callers to pass a custom executor for turns that need delegation. The default falls back to the runtime's base executor, which has no delegation and is safe for concurrent read-only use.

This avoids changing the `AgentRuntime` constructor or the `build_loop_runtime()` factory. The runtime remains a singleton; only the executor varies per turn.

### 4. Service Layer: Wiring It Together

**Current state** (`service.py`):

```python
async def _execute_agent_turn(...):
    user_lock = get_user_lock(user_id)
    async with user_lock:
        return await _execute_agent_turn_locked(...)

async def _invoke_turn_runtime(...):
    # ...
    if tool_delegate:
        runner._tool_executor.set_delegation(tool_delegate, delegated_tool_names)
    try:
        return await runner.invoke(...)
    finally:
        if tool_delegate:
            runner._tool_executor.clear_delegation()
```

**New state**:

```python
async def _execute_agent_turn(..., thread_id: int | None = None):
    # Thread must be resolved before locking.
    # For the main conversation, thread_id comes from get_or_create_thread().
    # For spawns (P8), it comes from the spawn's own thread.
    resolved_thread_id = thread_id or _resolve_thread_id(user_id, db)
    thread_lock = get_thread_lock(resolved_thread_id)
    async with thread_lock:
        return await _execute_agent_turn_locked(...)

async def _invoke_turn_runtime(...):
    # ...
    executor: ToolExecutor | None = None
    if tool_delegate:
        executor = ToolExecutor(
            get_tools(),
            delegate=tool_delegate,
            delegated_tool_names=delegated_tool_names,
        )
    # No set/clear -- executor is scoped to this call
    return await runner.invoke(
        ...,
        tool_executor=executor,
    )
```

The thread ID resolution happens early, before lock acquisition. For the current single-agent case, `_prepare_turn_context` already calls `get_or_create_thread(db, user_id)`, which returns the thread. We hoist the thread ID out of that function or resolve it before entry.

A helper function handles the common case:

```python
def _resolve_thread_id(user_id: int, db: Session) -> int:
    """Resolve the main conversation thread ID for lock acquisition."""
    thread = get_or_create_thread(db, user_id)
    return thread.id
```

**Note on ordering**: The current code acquires the user lock *before* creating the thread or run. With per-thread locking, we need the thread ID before we can acquire the lock. This means `get_or_create_thread` is called outside the lock. This is safe because `get_or_create_thread` is idempotent (returns existing thread or creates one) and the subsequent operations inside the lock are still serialized per thread.

---

## Files to Create/Modify

| File | Action | Change Summary |
|------|--------|----------------|
| `services/agent/turn_coordinator.py` | **Modify** | Rename `_user_locks` to `_thread_locks`, add `get_thread_lock(thread_id)`, deprecate `get_user_lock()`, increase cache size to 512 |
| `services/agent/executor.py` | **Modify** | Add `delegate` and `delegated_tool_names` as constructor kwargs, remove `set_delegation()` and `clear_delegation()` methods, update `execute()` to use `self._delegate` |
| `services/agent/service.py` | **Modify** | Replace `get_user_lock(user_id)` with `get_thread_lock(thread_id)`, construct per-turn `ToolExecutor` when delegation is needed, remove `set_delegation`/`clear_delegation` calls, add `_resolve_thread_id()` helper |
| `services/agent/runtime.py` | **Modify** | Add `tool_executor` parameter to `invoke()` and `resume_after_approval()`, use passed executor when provided |
| `tests/test_turn_coordinator.py` | **Create** | Unit tests for `get_thread_lock()`: creation, LRU eviction, locked-entry protection, concurrent access |
| `tests/test_executor_isolation.py` | **Create** | Tests verifying two `ToolExecutor` instances do not share delegation state |
| `tests/test_concurrency.py` | **Create** | Integration test: two threads acquire locks simultaneously, same thread serializes |

---

## Concurrency Model

### Before (P7)

```
User A sends message ──> get_user_lock(user_id=1) ──> BLOCKED until prior turn finishes
User A spawn task   ──> get_user_lock(user_id=1) ──> BLOCKED (same lock!)
                         ^^^^^^^^^^^^^^^^^^^^^^^^
                         Everything for user 1 is serialized
```

### After (P7)

```
User A main thread  ──> get_thread_lock(thread_id=10) ──> runs
User A spawn-1      ──> get_thread_lock(thread_id=11) ──> runs concurrently
User A spawn-2      ──> get_thread_lock(thread_id=12) ──> runs concurrently
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                         Independent locks, no cross-dependencies
```

### Invariants

1. **Within a single thread**: Turns are strictly serialized. The lock guarantees that sequence allocation, message persistence, and context window state remain consistent. This invariant is unchanged from the current system.

2. **Across threads**: No ordering guarantees. Main conversation and spawns run concurrently. This is safe because:
   - Each thread has its own message sequence (PostgreSQL, from P2)
   - `ToolContext` uses `contextvars.ContextVar`, which is per-asyncio-task
   - `ToolExecutor` is per-invocation (no shared mutable state)
   - `AnimaCompanion` is per-user but only the main thread writes to it; spawns (P8) will use read-only soul snapshots

3. **No cross-lock dependencies**: Thread A never needs Thread B's lock. This prevents deadlocks by construction. If a future requirement introduces cross-thread coordination, it must use a different mechanism (e.g., asyncio.Event, message queue) rather than nested lock acquisition.

4. **LLM concurrency**: This phase does not add an LLM semaphore. That is P8's responsibility. After P7, multiple threads *could* make concurrent LLM calls, but the spawn infrastructure that would trigger this does not exist yet.

### Thread Safety Analysis

| Component | Current Safety | After P7 | Notes |
|-----------|---------------|----------|-------|
| `turn_coordinator` | Safe (per-user serial) | Safe (per-thread serial) | Lock granularity changes, serialization preserved within thread |
| `ToolExecutor` | Unsafe under concurrency | Safe (per-invocation) | No shared mutable state |
| `ToolContext` | Safe (`ContextVar`) | Safe (`ContextVar`) | No change -- already per-task |
| `AnimaCompanion` | Safe (single writer) | Safe (single writer for now) | P8 must ensure spawns use snapshots, not the live companion |
| `AgentRuntime` | Safe (stateless invoke) | Safe (stateless invoke) | `_tool_executor` fallback is read-only (no delegation) |
| LLM client | Safe (httpx) | Safe (httpx) | httpx `AsyncClient` is documented as concurrent-safe |
| DB sessions | Safe (per-request) | Safe (per-request) | Each FastAPI request gets its own SQLAlchemy session |

---

## Test Plan

### Unit Tests: `test_turn_coordinator.py`

| Test | Description |
|------|-------------|
| `test_get_thread_lock_creates_new` | First call for a `thread_id` creates and returns a new `asyncio.Lock` |
| `test_get_thread_lock_returns_same` | Second call for same `thread_id` returns the same lock instance |
| `test_different_threads_get_different_locks` | Two different `thread_id` values return different lock instances |
| `test_lru_eviction_respects_max` | After inserting `_MAX_THREAD_LOCKS + 1` entries, the cache size does not exceed the max (oldest unlocked entry evicted) |
| `test_lru_eviction_skips_locked` | A locked entry is not evicted even when the cache is full |
| `test_move_to_end_on_access` | Accessing an existing lock moves it to the end of the LRU (most recently used) |
| `test_deprecated_get_user_lock_warns` | Calling `get_user_lock()` emits a `DeprecationWarning` |

### Unit Tests: `test_executor_isolation.py`

| Test | Description |
|------|-------------|
| `test_executor_without_delegation` | A `ToolExecutor` created without `delegate` executes local tools normally |
| `test_executor_with_delegation` | A `ToolExecutor` created with `delegate` and `delegated_tool_names` forwards matching tool calls to the delegate |
| `test_two_executors_independent` | Two `ToolExecutor` instances with different delegation configs do not interfere with each other |
| `test_executor_delegation_immutable` | `ToolExecutor` has no `set_delegation` or `clear_delegation` methods (attribute check) |
| `test_executor_non_delegated_tool_runs_locally` | A tool not in `delegated_tool_names` runs locally even when a delegate is configured |

### Integration Tests: `test_concurrency.py`

| Test | Description |
|------|-------------|
| `test_two_threads_concurrent_lock_acquisition` | Two different `thread_id` locks can be acquired simultaneously (no blocking). Uses `asyncio.wait_for` with a short timeout to verify no deadlock. |
| `test_same_thread_serializes` | Two coroutines acquiring the same `thread_id` lock run sequentially (second waits for first to release) |
| `test_concurrent_turn_simulation` | Simulated two-thread scenario: create two threads, fire two `_execute_agent_turn` calls concurrently (mocked LLM), verify both complete without race conditions or shared state corruption |
| `test_existing_single_agent_regression` | Standard single-user, single-thread turn executes correctly with the new locking (same behavior as before) |

### Manual Verification

- Run the full existing test suite (846+ tests) and confirm zero regressions
- Verify that the desktop app's main conversation flow works without behavioral change
- Verify that streaming agent turns still work correctly

---

## Acceptance Criteria

1. **`get_user_lock` is no longer called in production code paths.** All lock acquisition uses `get_thread_lock(thread_id)`. The deprecated shim exists only for safety and emits a warning.

2. **`ToolExecutor` has no `set_delegation()` or `clear_delegation()` methods.** Delegation is constructor-only. The class has no mutable instance state after `__init__` completes (other than the internal `_tools` dict which is set once and never modified).

3. **Two asyncio tasks holding locks for different `thread_id` values run concurrently.** A test proves this by racing two lock acquisitions and verifying neither blocks.

4. **A single `thread_id` still serializes turns.** A test proves this by showing that a second lock acquisition on the same `thread_id` blocks until the first releases.

5. **No mutable state is shared between concurrent `ToolExecutor` instances.** A test creates two executors with different delegation configs and verifies they do not interfere.

6. **All existing tests pass (846+).** No behavioral regression in single-agent operation.

7. **`service.py` constructs a per-turn `ToolExecutor` when delegation is needed** and passes it to `runtime.invoke()` rather than mutating a shared instance.

8. **`runtime.py` accepts an optional `tool_executor` parameter** on `invoke()` and `resume_after_approval()`, falling back to the base executor when none is provided.

---

## Out of Scope

- **SpawnManager and spawn lifecycle** -- That is P8. This phase only removes the concurrency blockers.
- **LLM semaphore** -- Gating concurrent LLM calls is a P8 concern. After P7, nothing actually triggers concurrent LLM calls because the spawn infrastructure does not exist yet.
- **`AnimaCompanion` refactor** -- The companion remains per-user. P8 will address how spawns interact with it (read-only snapshots).
- **Database schema changes** -- No new tables, columns, or migrations. Thread IDs already exist in PostgreSQL (from P2).
- **Configuration changes** -- No new settings. The `_MAX_THREAD_LOCKS` constant is internal and does not need to be user-configurable.
- **`tool_context.py` changes** -- `ToolContext` already uses `contextvars.ContextVar`, which provides per-asyncio-task isolation. No changes needed.
- **Changing the `build_loop_runtime()` factory** -- The runtime singleton is preserved. Only the per-invocation executor varies.

---

## Architecture Decision Record

### ADR-P7-001: Per-Invocation ToolExecutor vs Stateless Singleton

**Status**: Accepted

**Context**: The current `ToolExecutor` is a singleton on the `AgentRuntime`. It has mutable fields (`_tool_delegate`, `_delegated_tool_names`) that are set per-turn via `set_delegation()` and cleared in a `finally` block. Under concurrent execution, two turns would race on these fields.

**Options considered**:

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: Per-invocation executor** | Create a new `ToolExecutor` for each turn. Delegation is a constructor arg. | No shared mutable state. Simple to reason about. | Allocates a new dict per turn (~15 entries). |
| **B: Pass delegation as call args** | Keep singleton. Pass `delegate` and `delegated_tool_names` through `execute()`. | No extra allocation. | Threads delegation through 4+ call layers. Harder to audit for accidental state leaks. Messier API. |

**Decision**: Option A. The allocation cost (one dict of ~15 tool references) is negligible -- a single LLM inference call costs 100-1000ms and allocates megabytes of response data. The simplicity of "no shared mutable state" is worth more than avoiding a microsecond dict construction.

**Consequences**:
- Easier: Reasoning about concurrency. No cleanup code (`clear_delegation` / `finally` blocks). Testing isolation.
- Harder: Nothing meaningful. The `ToolExecutor` constructor is called once per turn, which is already a low-frequency operation.

### ADR-P7-002: Thread Lock Cache Size

**Status**: Accepted

**Context**: The current user lock cache holds 256 entries. With per-thread locking, each spawn creates an additional thread, so the cache must accommodate `users * (1 + max_spawns)`.

**Decision**: Increase to 512. At the default `agent_max_concurrent_spawns = 10`, this supports ~46 concurrent users at maximum spawn load. For a single-user desktop app, this is vastly over-provisioned, which is the correct trade-off: memory cost per lock is ~200 bytes, so 512 locks cost ~100KB.

**Consequences**:
- Easier: No risk of premature eviction under spawn load.
- Harder: Nothing. The memory overhead is trivial.
