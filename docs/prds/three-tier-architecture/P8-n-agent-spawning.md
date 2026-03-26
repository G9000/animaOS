---
title: "Phase 8: N-Agent Spawning"
description: Enable one AI identity to run multiple concurrent cognitive processes sharing a single soul, with SpawnManager orchestration, LLM semaphore gating, and spawn-specific tooling
category: prd
version: "1.0"
---

# Phase 8: N-Agent Spawning

**Status**: Draft
**Depends on**: P4 (Write Boundary), P7 (Concurrency Refactor)
**Estimated scope**: 1 PR

---

## Overview

Phase 8 is the capstone of the three-tier architecture migration. With PostgreSQL handling all runtime state (P2), the write boundary enforced so runtime never touches the soul directly (P4), and per-thread locking replacing per-user locking (P7), the system can now support N concurrent agent processes sharing one identity.

The core claim: a single AI identity can run multiple cognitive processes in parallel without fragmenting its sense of self. This is one mind doing multiple things at once -- not a team of agents, not multi-persona, not a swarm. Every spawn shares the same soul (read-only snapshot), the same knowledge base, and reports its findings back to the main agent.

A spawned agent is a short-lived background worker. It cannot talk to the user, cannot modify core memory, and cannot spawn other spawns. It runs a constrained step loop, writes only to its own PostgreSQL thread, and terminates by calling `report_result`. The main agent sees the result on its next turn or by polling with `check_spawns`.

---

## Scope

### In scope

- `SpawnManager` class: lifecycle management for spawn creation, cancellation, status polling, and cleanup
- `SpawnRun` and `SpawnStep` PostgreSQL models for tracking spawn state
- Four new tools: `spawn_task`, `check_spawns`, `cancel_spawn` (main agent), `report_result` (spawn-only)
- Spawn-specific system prompt that communicates the constrained role
- `safe_create_task` utility for GC-protected asyncio fire-and-forget tasks
- LLM semaphore gating to bound concurrent inference requests
- Spawn timeout enforcement with graceful cancellation
- Spawn result injection into the main agent's context on the next turn
- Integration with the existing `ToolExecutor` (per-spawn instances, no shared mutable state)
- Integration with `prompt_budget.py` for spawn result memory blocks
- Configuration settings for concurrency limits, timeouts, and step caps

### Out of scope

- Spawn recursion (spawns spawning spawns) -- `agent_spawn_recursive` defaults to `False`
- Frontend UI for spawn progress -- deferred to a separate spec, will use WebSocket events
- Spawn-to-spawn communication -- spawns are independent workers
- Real-time streaming of spawn output to the user -- spawns run silently
- Spawn access to action tools (delegated client tools like `bash`, `read_file`)
- Spawn persistence across server restarts -- active spawns are cancelled on shutdown

---

## Implementation Details

### Architectural Invariants

1. **One identity, N processes.** Every spawn reads the same soul. No spawn has a different personality, name, or value system.
2. **Snapshot isolation.** Spawns receive a frozen copy of soul memory blocks at spawn time. If consolidation updates the soul mid-spawn, the spawn continues with stale data. Spawns are short-lived (seconds to minutes); staleness is acceptable.
3. **Write boundary holds.** Spawns write only to PostgreSQL (their own thread, `SpawnRun` status, `save_to_memory` items). The consolidation gateway is the sole path to the soul. No exceptions.
4. **No user-facing output.** Spawns never call `send_message`. Their results surface through the main agent, preserving a coherent conversational voice.
5. **No cross-lock dependencies.** Main thread lock and spawn thread locks are independent. No spawn ever acquires the main thread's lock or vice versa. This prevents deadlocks by construction.

### Trade-offs

| Decision | What we gain | What we give up |
|----------|-------------|-----------------|
| Snapshot isolation (not live reads) | No race conditions between spawns and consolidation; simple implementation | Spawn may operate on slightly stale soul data |
| No spawn recursion | Bounded resource usage; simpler mental model | Cannot decompose spawn tasks further |
| No `send_message` for spawns | Single conversational voice; user never confused about who is talking | Spawn cannot ask user for clarification mid-task |
| Step cap of 4 (default) | Prevents runaway spawns from burning tokens | Complex tasks may need more steps; tunable via config |
| LLM semaphore (not per-model queuing) | Simple concurrency control; works with any provider | Cannot differentiate between fast and slow models; all spawns compete equally |

---

## Files to Create

### `apps/server/src/anima_server/models/spawn.py`

New SQLAlchemy models for spawn tracking. Both models use the runtime PostgreSQL base class (introduced in P1/P2).

### `apps/server/src/anima_server/services/agent/spawn_manager.py`

The `SpawnManager` class. Singleton per process, holds the active task set and LLM semaphore. Responsible for spawn creation, cancellation, status queries, and cleanup.

### `apps/server/src/anima_server/services/agent/spawn_tools.py`

Tool definitions: `spawn_task`, `check_spawns`, `cancel_spawn`, `report_result`. Each decorated with `@tool` from the existing tools module.

### `apps/server/src/anima_server/services/agent/spawn_runner.py`

The spawn agent step loop. Builds a constrained runtime (subset of tools, spawn system prompt, own ToolExecutor), runs up to `agent_spawn_max_steps` steps, updates `SpawnRun` on completion or failure.

## Files to Modify

### `apps/server/src/anima_server/services/agent/tools.py`

- Add `spawn_task`, `check_spawns`, `cancel_spawn` to `get_extension_tools()` for the main agent
- Add a new `get_spawn_tools()` function that returns the spawn-safe subset plus `report_result`
- `report_result` must NOT appear in the main agent's tool set

### `apps/server/src/anima_server/services/agent/service.py`

- Import and initialize `SpawnManager` (lazy singleton, same pattern as `get_or_build_runner()`)
- Add spawn result injection to `_prepare_turn_context()`: query `SpawnRun` rows where `parent_thread_id` matches and `status` is `completed`, build a `MemoryBlock` with label `spawn_results`, mark as consumed after injection
- Provide `SpawnManager` instance to spawn tools via `ToolContext` or a dedicated contextvar

### `apps/server/src/anima_server/services/agent/llm.py`

- Add `_llm_semaphore` module-level `asyncio.Semaphore` initialized from `settings.agent_max_concurrent_spawns`
- Add `gated_llm_call(adapter, request)` async function that acquires the semaphore before invoking the adapter
- The semaphore gates both main agent and spawn LLM calls uniformly

### `apps/server/src/anima_server/services/agent/prompt_budget.py`

- Add `spawn_results` to `_BLOCK_POLICIES` at tier 1, order 5, max_chars 2000. Spawn results are contextually important (the main agent asked for this work) but should not crowd out identity or conversation state.
- Add `spawn_goal` to `_BLOCK_POLICIES` at tier 0, order 4, max_chars 500 (for spawn system prompt construction).

### `apps/server/src/anima_server/config.py`

- Add four new settings (see Configuration section below)

### `apps/server/src/anima_server/models/__init__.py`

- Import and re-export `SpawnRun` and `SpawnStep` from `models/spawn.py`

---

## Models / Schemas

### SpawnRun (PostgreSQL runtime)

```python
class SpawnRun(RuntimeBase):
    __tablename__ = "spawn_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(nullable=False, index=True)
    parent_thread_id: Mapped[int] = mapped_column(
        ForeignKey("agent_threads.id"), nullable=False, index=True
    )
    spawn_thread_id: Mapped[int] = mapped_column(
        ForeignKey("agent_threads.id"), nullable=False, unique=True
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )  # pending | running | completed | failed | cancelled
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    steps_completed: Mapped[int] = mapped_column(nullable=False, default=0)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    consumed: Mapped[bool] = mapped_column(
        nullable=False, default=False
    )  # True after main agent has seen the result
```

**Index strategy:**
- `(user_id)` -- list all spawns for a user
- `(parent_thread_id, status)` -- list active spawns for a conversation thread
- `(spawn_thread_id)` unique -- one spawn per thread

**Status transitions:**
```
pending --> running --> completed
                   \-> failed
pending --> cancelled
running --> cancelled
```

### SpawnStep (PostgreSQL runtime)

```python
class SpawnStep(RuntimeBase):
    __tablename__ = "spawn_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    spawn_run_id: Mapped[int] = mapped_column(
        ForeignKey("spawn_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_index: Mapped[int] = mapped_column(nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_args_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

### SpawnStatus (Pydantic response schema)

```python
class SpawnStatus(BaseModel):
    spawn_run_id: int
    goal: str
    status: str
    result: str | None = None
    error: str | None = None
    steps_completed: int = 0
    created_at: datetime
    completed_at: datetime | None = None
```

---

## SpawnManager

### Class Design

```python
class SpawnManager:
    """Manages the lifecycle of spawned background agents.

    Singleton per process. Holds the active asyncio.Task set for GC
    protection and the LLM semaphore for concurrency control.
    """

    def __init__(self, max_concurrent: int = 10) -> None:
        self._active_tasks: set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._cancel_events: dict[int, asyncio.Event] = {}  # spawn_run_id -> cancel event

    async def spawn(
        self,
        *,
        goal: str,
        context: str,
        parent_thread_id: int,
        user_id: int,
        db_factory: Callable[[], Session],
        soul_snapshot: tuple[MemoryBlock, ...],
    ) -> int:
        """Create a SpawnRun, fire an asyncio task, return spawn_run_id."""

    async def check(self, spawn_run_id: int, db: Session) -> SpawnStatus:
        """Return current status of a spawn."""

    async def cancel(self, spawn_run_id: int, db: Session) -> bool:
        """Request cancellation of a running spawn. Returns True if cancellation was signalled."""

    async def list_active(self, parent_thread_id: int, db: Session) -> list[SpawnStatus]:
        """List all non-consumed spawns for a parent thread."""

    async def cleanup_completed(self, parent_thread_id: int, db: Session) -> int:
        """Mark completed spawn results as consumed. Returns count consumed."""

    def shutdown(self) -> None:
        """Cancel all active tasks. Called during server shutdown."""
```

### Singleton Access

```python
_spawn_manager: SpawnManager | None = None
_spawn_manager_lock = threading.Lock()

def get_spawn_manager() -> SpawnManager:
    global _spawn_manager
    if _spawn_manager is not None:
        return _spawn_manager
    with _spawn_manager_lock:
        if _spawn_manager is None:
            _spawn_manager = SpawnManager(
                max_concurrent=settings.agent_max_concurrent_spawns
            )
        return _spawn_manager
```

The `SpawnManager` is registered with FastAPI's lifespan for graceful shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    get_spawn_manager().shutdown()
```

### safe_create_task

Module-level utility for fire-and-forget asyncio tasks with GC protection:

```python
_background_tasks: set[asyncio.Task] = set()

def safe_create_task(coro: Coroutine, *, label: str = "background") -> asyncio.Task:
    """Create an asyncio task that won't be garbage collected.

    Wraps the coroutine in exception handling so unhandled errors are
    logged rather than silently swallowed. The task is added to a
    module-level set and removed via done callback.
    """
    async def wrapper():
        try:
            await coro
        except asyncio.CancelledError:
            logger.info("Task %s cancelled", label)
        except Exception:
            logger.exception("Task %s failed", label)

    task = asyncio.create_task(wrapper(), name=f"spawn:{label}")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
```

This is the same pattern used by Letta for background agent tasks. The `set` reference prevents Python's garbage collector from collecting tasks that have no other references.

---

## Spawn Lifecycle

### 1. Main agent calls `spawn_task(goal, context)`

The tool function:
1. Gets the current `ToolContext` (user_id, thread_id, db)
2. Snapshots the current soul memory blocks (frozen copy)
3. Calls `SpawnManager.spawn()` which:
   a. Creates a new `AgentThread` in PostgreSQL for the spawn
   b. Creates a `SpawnRun` row with status `pending`
   c. Builds a `db_factory` from the current session's bind
   d. Fires `safe_create_task(spawn_runner.run_spawn(...))` with the spawn_run_id as label
   e. Returns the `spawn_run_id`
4. Returns a confirmation string to the main agent: `"Spawned task #{id}: {goal}"`

### 2. Spawn runner executes

`spawn_runner.run_spawn()` is an async function that:

1. Opens a fresh DB session via `db_factory()`
2. Updates `SpawnRun.status` to `running`
3. Builds the spawn's tool set via `get_spawn_tools()` (see Spawn Tools below)
4. Creates a dedicated `ToolExecutor` with the spawn tool set (no delegation, no shared state)
5. Builds the spawn system prompt (see Spawn System Prompt below)
6. Enters the step loop:

```
for step_index in range(settings.agent_spawn_max_steps):
    if cancel_event.is_set():
        update status -> cancelled; break
    if timeout exceeded:
        update status -> failed, error="timeout"; break

    acquire LLM semaphore
    invoke LLM with spawn system prompt + spawn thread messages
    release LLM semaphore

    process tool calls:
        if report_result called:
            update SpawnRun.result, status -> completed; break
        execute other tools (recall_memory, recall_conversation, save_to_memory)
        record SpawnStep

    if no tool calls (model responded with text only):
        treat as implicit report_result with the text as result
        update SpawnRun.result, status -> completed; break

if loop exhausted without report_result:
    update status -> failed, error="max steps reached without result"
```

7. Updates `SpawnRun.completed_at` and `SpawnRun.token_usage`
8. Closes the DB session

### 3. Main agent sees results

On the main agent's next turn, `_prepare_turn_context()` in `service.py` queries:

```sql
SELECT * FROM spawn_runs
WHERE parent_thread_id = :thread_id
  AND status IN ('completed', 'failed')
  AND consumed = false
```

For each unconsumed result, a `MemoryBlock` is built:

```python
MemoryBlock(
    label="spawn_results",
    description="Results from background tasks you spawned",
    value=format_spawn_results(unconsumed_spawns),
    read_only=True,
)
```

The format:

```
Background task results:
- Task #42 (completed): "Found 3 relevant papers on CLS theory..."
- Task #43 (failed): "Timed out after 300s"
```

After the turn completes, `SpawnRun.consumed` is set to `True` so results are not re-injected.

The main agent can also actively poll using `check_spawns()` mid-turn to see results before its turn ends.

### 4. Error handling

| Failure mode | Behavior |
|-------------|----------|
| LLM error during spawn | `SpawnRun.status = "failed"`, `SpawnRun.error` captures the message |
| Tool execution error | Treated as a tool error message in the spawn's context; spawn continues |
| Timeout | `SpawnRun.status = "failed"`, `SpawnRun.error = "Spawn timed out after {N}s"` |
| Cancellation | `SpawnRun.status = "cancelled"`, asyncio task is cancelled |
| Server shutdown | All active spawns cancelled via `SpawnManager.shutdown()` |
| DB session error | Logged, spawn marked failed if possible |

---

## Spawn Tools

### Main Agent Tools (added to `get_extension_tools()`)

#### spawn_task

```python
@tool
def spawn_task(goal: str, context: str = "") -> str:
    """Spawn a background agent to work on a task in parallel while you
    continue the conversation. The spawn has access to memory search and
    conversation history but cannot send messages to the user or modify
    core memory. Use this for research, analysis, or any task that can
    run independently.

    Args:
        goal: Clear description of what the spawn should accomplish.
        context: Relevant context from the current conversation to pass
                 to the spawn. Include key details it will need.

    Returns a spawn ID that you can check later with check_spawns().
    """
```

#### check_spawns

```python
@tool
def check_spawns() -> str:
    """Check the status of all spawned background tasks. Returns a summary
    of each spawn's current status, including any completed results.
    Use this to poll for results from tasks you previously spawned.
    """
```

#### cancel_spawn

```python
@tool
def cancel_spawn(spawn_id: str) -> str:
    """Cancel a running spawned task by its ID. Use this if a spawn is no
    longer needed (e.g., you already found the answer another way).
    """
```

### Spawn-Only Tools (returned by `get_spawn_tools()`)

#### report_result

```python
@tool
def report_result(result: str) -> str:
    """Report your findings back to the main agent. This ends your task.
    Provide a clear, concise summary of what you found or accomplished.
    The main agent will see this result and can share it with the user.
    """
```

This tool is terminal for spawns (equivalent to `send_message` for the main agent). When called:
1. Updates `SpawnRun.result` with the result text
2. Updates `SpawnRun.status` to `completed`
3. Updates `SpawnRun.completed_at`
4. Returns a sentinel value that the spawn runner recognizes as "stop"

### Spawn Tool Set Summary

The `get_spawn_tools()` function returns:

| Tool | Available | Notes |
|------|-----------|-------|
| `send_message` | No | Spawns do not talk to the user |
| `recall_memory` | Yes | Reads from soul snapshot (frozen at spawn time) |
| `recall_conversation` | Yes | Reads from the spawn's own PG thread |
| `recall_transcript` | Yes | Read-only access to encrypted JSONL archive |
| `core_memory_append` | No | Main agent only (write boundary) |
| `core_memory_replace` | No | Main agent only (write boundary) |
| `save_to_memory` | Yes | Writes to PG (spawn's thread context) |
| `note_to_self` | Yes | Scratch-pad for the spawn's own reasoning |
| `dismiss_note` | Yes | Clean up spawn session notes |
| `current_datetime` | Yes | Utility |
| `spawn_task` | No | No recursion |
| `check_spawns` | No | Spawns do not manage other spawns |
| `cancel_spawn` | No | Spawns do not manage other spawns |
| `update_human_memory` | No | Main agent only |
| `set_intention` | No | Main agent only |
| `complete_goal` | No | Main agent only |
| `create_task` | No | Main agent only |
| `list_tasks` | No | Main agent only |
| `complete_task` | No | Main agent only |
| `report_result` | Yes | Spawn-only terminal tool |

The `thinking` and `request_heartbeat` parameters are injected into spawn tools using the same `inject_inner_thoughts_into_tools()` and `inject_heartbeat_into_tools()` functions. `report_result` is treated as terminal (no heartbeat injected), matching the `send_message` pattern.

---

## Spawn System Prompt

The spawn receives a minimal system prompt. It does not receive the full persona/soul prompt because (a) it is not speaking to the user and does not need conversational personality, and (b) keeping the prompt small leaves more context window for the actual work.

```
You are a background worker for ANIMA. You have been spawned to complete a specific task.

Your goal: {goal}

Context from the main agent: {context}

Rules:
- You have access to memory search (recall_memory, recall_conversation) and can save findings (save_to_memory, note_to_self).
- You CANNOT send messages to the user or modify core memory.
- You CANNOT spawn other tasks.
- Work efficiently. You have a maximum of {max_steps} reasoning steps.
- When you have completed your task, use report_result to send your findings back to the main agent.
- If you cannot complete the task, use report_result to explain what you found and what blocked you.

Available tools:
{tool_summaries}
```

The `{tool_summaries}` section uses the existing `get_tool_summaries()` function applied to the spawn's tool set.

The spawn's soul snapshot is included as memory blocks (read-only) in the conversation context, not in the system prompt. This keeps the system prompt stable and small while still giving the spawn access to identity and knowledge.

---

## Concurrency Controls

### LLM Semaphore

All LLM calls -- both main agent and spawns -- pass through a single semaphore:

```python
# In llm.py
_llm_semaphore: asyncio.Semaphore | None = None

def _get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(settings.agent_max_concurrent_spawns)
    return _llm_semaphore

async def gated_llm_call(client: ChatClient, messages: Sequence[Any]) -> Any:
    """Invoke the LLM with semaphore-gated concurrency control."""
    async with _get_llm_semaphore():
        return await client.ainvoke(messages)
```

The semaphore value (`agent_max_concurrent_spawns`) controls total concurrent LLM requests, not just spawns. If the main agent is running and 9 spawns are active, only `max_concurrent_spawns - 1` spawns will be actively calling the LLM at any given time (one slot is effectively occupied by the main agent).

The main agent is NOT required to acquire the semaphore for its own LLM calls. The semaphore gates spawn LLM calls. The main agent's turn is already serialized by the per-thread lock and should not be blocked behind spawn work. If desired, the main agent can optionally participate in semaphore gating for true global concurrency control -- this is a configuration decision.

Design choice: the semaphore lives in `llm.py` alongside the `create_llm()` singleton because it gates the same shared resource (the LLM endpoint). An alternative placement would be inside `SpawnManager`, but that would require the main agent's runtime to know about `SpawnManager` to share the semaphore.

### Per-Thread Locking (from P7)

P7 replaces `get_user_lock(user_id)` with `get_thread_lock(thread_id)`. This is the prerequisite that makes N-agent spawning safe:

- Main agent acquires `lock(main_thread_id)`
- Spawn 1 acquires `lock(spawn_thread_1_id)`
- Spawn 2 acquires `lock(spawn_thread_2_id)`
- No contention between any of them

### ToolExecutor Isolation (from P7)

P7 removes the shared mutable `_tool_delegate` / `_delegated_tool_names` state from `ToolExecutor`. Each spawn creates its own `ToolExecutor` instance with the spawn tool set. There is no delegation for spawns (they do not connect to frontend clients).

### ToolContext Isolation

Each spawn runs in its own asyncio task. The `ToolContext` is set via `contextvars.ContextVar`, which is automatically scoped to the task. No special handling is needed -- `set_tool_context()` in the spawn runner sets the spawn's context, and tools within that task see the spawn's `db`, `user_id`, and `thread_id`.

### DB Session Isolation

Each spawn opens its own DB session via `db_factory()`. Sessions are not shared between the main agent and spawns. PostgreSQL handles concurrent writes from multiple sessions natively.

### Spawn Timeout

Each spawn has a wall-clock timeout (`agent_spawn_timeout`, default 300s). The spawn runner checks elapsed time at the start of each step:

```python
deadline = start_time + settings.agent_spawn_timeout
# In the step loop:
if time.monotonic() > deadline:
    # Update SpawnRun.status = "failed", error = "timeout"
    break
```

The `asyncio.Task` is also cancelled after the timeout as a safety net, using `asyncio.wait_for` or a dedicated timer task.

---

## Configuration

Four new settings added to `Settings` in `config.py`:

```python
# Maximum concurrent LLM calls across all spawns (semaphore size).
# Also serves as the effective cap on concurrent spawns since each
# spawn step requires an LLM call.
agent_max_concurrent_spawns: int = 10

# Wall-clock timeout for a single spawn (seconds). Spawn is cancelled
# and marked failed if it exceeds this duration.
agent_spawn_timeout: float = 300.0

# Maximum number of LLM reasoning steps a spawn can take. After this
# many steps without calling report_result, the spawn fails with
# "max steps reached". Keep low to prevent runaway token usage.
agent_spawn_max_steps: int = 4

# Whether spawns can call spawn_task themselves. Disabled by default
# to prevent runaway resource usage. If enabled, a depth limit should
# be enforced (future work).
agent_spawn_recursive: bool = False
```

Environment variable names (following the `ANIMA_` prefix convention):

| Setting | Env var |
|---------|---------|
| `agent_max_concurrent_spawns` | `ANIMA_AGENT_MAX_CONCURRENT_SPAWNS` |
| `agent_spawn_timeout` | `ANIMA_AGENT_SPAWN_TIMEOUT` |
| `agent_spawn_max_steps` | `ANIMA_AGENT_SPAWN_MAX_STEPS` |
| `agent_spawn_recursive` | `ANIMA_AGENT_SPAWN_RECURSIVE` |

---

## Test Plan

Tests live in `apps/server/tests/test_spawn.py` (new file). All tests use the existing test infrastructure (conftest fixtures, in-memory PostgreSQL, mock LLM adapter).

### Unit Tests

1. **SpawnRun model CRUD.** Create a `SpawnRun`, verify all fields persist and read back correctly. Test status transitions: pending to running, running to completed, running to failed, pending to cancelled.

2. **SpawnStep model CRUD.** Create `SpawnStep` rows linked to a `SpawnRun`, verify cascade delete works.

3. **SpawnManager.spawn() creates records.** Call `spawn()`, verify a `SpawnRun` row exists with status `pending`, a new `AgentThread` is created for the spawn, and an asyncio task is in `_active_tasks`.

4. **Spawn reads soul snapshot, not live soul.** Provide a soul snapshot at spawn time. Modify the soul after spawning. Verify the spawn's `recall_memory` reads the snapshot, not the modified soul.

5. **Spawn writes to own PG thread (isolated).** Spawn calls `save_to_memory` and `note_to_self`. Verify writes are associated with the spawn's thread, not the parent thread.

6. **Spawn cannot call send_message.** Verify `send_message` is not in the spawn's tool set. If the LLM somehow emits a `send_message` call, the executor returns an "Unknown tool" error.

7. **Spawn cannot call core_memory_append or core_memory_replace.** Verify these tools are not in the spawn's tool set.

8. **Spawn cannot call spawn_task.** Verify `spawn_task` is not in the spawn's tool set (no recursion).

9. **report_result updates SpawnRun and ends the spawn.** Call `report_result` with a result string. Verify `SpawnRun.result` is set, `status` is `completed`, `completed_at` is populated, and the step loop terminates.

10. **check_spawns returns correct status.** Create multiple spawns with different statuses. Call `check_spawns`. Verify the returned summary accurately reflects each spawn's state and result.

11. **cancel_spawn cancels a running spawn.** Start a spawn, call `cancel_spawn`. Verify the asyncio task is cancelled, `SpawnRun.status` is `cancelled`, and any in-flight LLM call is interrupted.

12. **LLM semaphore limits concurrent inference.** Set `agent_max_concurrent_spawns = 2`. Start 5 spawns. Verify that at most 2 LLM calls are in-flight at any time (use a mock adapter that tracks concurrent invocations).

13. **Spawn timeout triggers cancellation.** Set `agent_spawn_timeout = 0.5`. Start a spawn with a mock LLM that sleeps for 2 seconds. Verify the spawn is cancelled and `SpawnRun.status` is `failed` with a timeout error message.

14. **Main agent sees spawn result on next turn.** Complete a spawn. On the main agent's next `_prepare_turn_context()`, verify a `spawn_results` `MemoryBlock` is present containing the spawn's result text.

15. **Consumed spawns are not re-injected.** After the main agent sees a spawn result, verify `SpawnRun.consumed` is `True` and the result does not appear in subsequent turns.

16. **Multiple spawns run concurrently without interference.** Start 3 spawns with different goals. Verify all 3 complete independently, each writing to its own thread, with correct results.

17. **Spawn step cap enforcement.** Set `agent_spawn_max_steps = 2`. Start a spawn whose mock LLM never calls `report_result`. Verify the spawn fails with "max steps reached" after 2 steps.

18. **safe_create_task GC protection.** Create a task via `safe_create_task`, drop all local references. Verify the task still runs to completion (not garbage collected). Verify the task is removed from `_background_tasks` after completion.

19. **SpawnManager.shutdown() cancels all active tasks.** Start 3 spawns. Call `shutdown()`. Verify all asyncio tasks are cancelled and `SpawnRun` statuses are updated.

20. **Spawn token usage is tracked.** Complete a spawn. Verify `SpawnRun.token_usage` contains prompt and completion token counts aggregated across all steps.

### Integration Tests

21. **End-to-end spawn flow.** Send a user message that triggers the main agent to call `spawn_task`. Verify the spawn runs to completion, the result is injected into the next turn's context, and the main agent can reference it.

22. **Spawn with real tool execution.** Spawn a task that calls `recall_memory` and `recall_conversation` before reporting. Verify both tools execute correctly against the snapshot and spawn thread respectively.

---

## Acceptance Criteria

1. The main agent can spawn a background task by calling `spawn_task(goal, context)`, receiving a spawn ID in response.

2. Spawned agents run concurrently with the main agent conversation without blocking or interfering.

3. Spawned agents receive a frozen snapshot of soul memory blocks and cannot modify core memory.

4. Spawned agents can search memory (`recall_memory`), search their own conversation thread (`recall_conversation`), save observations (`save_to_memory`, `note_to_self`), and report results (`report_result`).

5. Spawned agents cannot call `send_message`, `core_memory_append`, `core_memory_replace`, `spawn_task`, or any user-facing tool.

6. `check_spawns()` returns accurate status for all spawns associated with the current conversation thread.

7. `cancel_spawn(id)` cancels a running spawn, setting its status to `cancelled`.

8. Completed spawn results appear automatically in the main agent's context on the next turn as a `spawn_results` memory block, and are marked consumed after injection.

9. The LLM semaphore bounds total concurrent inference requests to `agent_max_concurrent_spawns`.

10. Spawns that exceed `agent_spawn_timeout` seconds are cancelled and marked as failed.

11. Spawns that exceed `agent_spawn_max_steps` steps without calling `report_result` are marked as failed.

12. Server shutdown cancels all active spawns gracefully.

13. All 846+ existing tests continue to pass. New tests cover all spawn lifecycle paths.

14. No regressions in main agent turn latency when zero spawns are active (spawn infrastructure has zero cost when not used).

---

## Out of Scope

- **Spawn recursion.** Spawns cannot spawn other spawns. The `agent_spawn_recursive` setting exists as a forward-looking flag but defaults to `False` and is not implemented in this phase. If enabled in the future, a depth limit and total descendant cap must be enforced.

- **Spawn UI / frontend.** How the desktop app displays spawn progress is deferred. The backend will emit structured `SpawnRun` data via the existing REST API, but WebSocket events for real-time spawn lifecycle updates are a separate spec.

- **Spawn-to-user streaming.** Spawns do not stream output to the user in real time. Their results are delivered as a block when the main agent's next turn begins.

- **Action tool delegation for spawns.** Spawns do not have access to client-delegated action tools (bash, file I/O, etc.). Only server-side cognitive tools are available.

- **Spawn persistence across restarts.** If the server restarts, all in-flight spawns are lost. `SpawnRun` rows with status `running` or `pending` at server start can be marked `failed` with error `"server restarted"` as a cleanup step, but this is not required in the initial implementation.

- **Cross-spawn communication.** Spawns are independent. They cannot read each other's threads or coordinate. If coordination is needed in the future, it would go through the main agent.

- **Spawn result merging strategies.** The current design injects results as a flat `MemoryBlock`. More sophisticated merging (e.g., LLM-powered summarization of multiple spawn results) is deferred.

- **Per-model or per-provider semaphore.** The LLM semaphore is global. If the system uses multiple LLM providers or models simultaneously (e.g., different models for spawns vs main agent), per-provider gating would be more efficient but adds complexity that is not justified until multi-model usage is common.
