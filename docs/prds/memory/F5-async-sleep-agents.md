---
title: "PRD: F5 — Async Sleep-Time Agents"
description: Structured background tasks triggered by turn frequency and user inactivity
category: prd
version: "1.0"
---

# PRD: F5 — Async Sleep-Time Agents

**Version**: 1.0
**Date**: 2026-03-18
**Status**: Draft
**Roadmap Phase**: 10.6
**Priority**: P2
**Depends on**: F2 (Heat Scoring), F3 (Predict-Calibrate), F4 (Knowledge Graph)
**Blocks**: F6 benefits from this for orchestration

---

## 1. Overview

Refactor the background memory processing pipeline from two suboptimal trigger modes (every-turn consolidation + 5-minute inactivity reflection) into a unified, frequency-gated, heat-threshold-aware async orchestrator. F5 adopts the useful orchestration patterns visible in Letta's `SleeptimeMultiAgentV4` such as turn counting, configurable frequency, tracked task runs, and last-processed restart cursors, but it does **not** adopt Letta's open-ended background-agent model.

AnimaOS intentionally uses structured background tasks instead of general-purpose, open-ended workers with their own evolving memory. This is a deliberate reliability tradeoff: the system gives up some flexibility in exchange for more predictable and auditable background behavior. The user's foreground response is never blocked, and last-processed tracking remains a required part of restart safety rather than an optional optimization.

---

## 2. Problem Statement

### Current Implementation

| Component | File | Trigger | Behavior |
|-----------|------|---------|----------|
| Memory consolidation | `consolidation.py` | Every turn via `asyncio.create_task()` | Fires on **every single turn** — no gating. Runs regex + LLM extraction + dedup. |
| Reflection | `reflection.py` | 5-minute inactivity timer | `schedule_reflection()` sets a delayed task. On fire: expire working memory, quick monologue, then full `run_sleep_tasks()`. |
| Sleep tasks | `sleep_tasks.py` | Called by reflection | Sequential: contradiction scan → profile synthesis → episode generation → deep monologue → embedding backfill. All run, every time. |

### The Gaps

| Gap | Impact |
|-----|--------|
| **No frequency gating** | Consolidation runs on every turn. For a 20-message conversation, that's 20 LLM extraction calls. Most are wasteful — the conversation hasn't changed enough turn-to-turn to justify re-extraction. |
| **No heat-based gating** | Expensive operations (contradiction scan, profile synthesis, deep monologue) run on every reflection regardless of whether memory activity justifies them. A quiet day with 3 low-importance messages triggers the same work as an intense day with 50 messages. |
| **No parallelism** | All sleep tasks run sequentially. Independent tasks (contradiction scan, profile synthesis, embedding backfill) could run in parallel. |
| **No turn counting** | No record of how many turns have occurred since the last background run. No way to configure "run every Nth turn." |
| **No task tracking** | No record of what background tasks ran, when, or whether they succeeded. Debugging background processing issues is blind. |
| **No restart safety** | If the server restarts, there's no record of which messages were already processed. Consolidation may reprocess old messages. |

### Evidence

| Source | Pattern |
|--------|---------|
| Letta `sleeptime_multi_agent_v4.py` | `SleeptimeMultiAgentV4.step()` calls `asyncio.create_task(run_sleeptime_agents())` after each foreground step |
| Letta | `_sleeptime_agent_frequency` counter — only fires if `turn_count % frequency == 0` |
| Letta | `get_last_processed_message_id_and_update_async()` — restart safety |
| Letta | `finally` block ensures state is saved even if tasks fail |

### Design Boundary

The competitor audit supports borrowing Letta's orchestration mechanics while rejecting its background-agent autonomy. AnimaOS should keep background maintenance as named, structured tasks with explicit inputs and outputs, not as free-form LLM workers that decide their own memory edits. That narrower model is less flexible than Letta's full sleeptime agents, but it is more predictable to operate, easier to inspect, and better aligned with a local-first personal system.

---

## 3. Goals and Non-Goals

### Goals

1. Turn counter with configurable frequency: run structured background tasks every N turns (default 3)
2. Heat-threshold gating: expensive operations only fire when accumulated memory heat exceeds a threshold
3. Parallel execution of independent background tasks (consolidation, graph ingestion, heat decay, episode check), with `graph_ingestion` kept current-turn / explicit-delta scoped
4. Sequential execution of expensive tasks (contradiction scan, profile synthesis, deep monologue) gated by heat
5. `BackgroundTaskRun` table tracking all background work for debugging and monitoring
6. `force=True` mode for the existing 5-minute inactivity timer to bypass all gating
7. Last-processed-message tracking to prevent reprocessing on restart

### Non-Goals

- Changing what the background tasks do — this feature changes **when and how** they fire, not their logic
- Multi-process or multi-worker parallelism — all parallelism is within a single `asyncio` event loop
- External task queue (Celery, Redis, etc.) — in-process only
- UI for task monitoring — backend tracking table only
- Changing the 5-minute inactivity timer value

---

## 4. Detailed Design

### 4.1 Architecture

```
User sends message
       |
       v
  [Foreground response returned immediately]
       |
       v
  bump_turn_counter(user_id)
       |
       v
  should_run_sleeptime(user_id)?
       |
  No --+--> return (skip background work this turn)
       |
  Yes -+--> asyncio.create_task(run_sleeptime_agents(...))
                    |
           +--------+--------+--------+
           |        |        |        |
        [parallel tasks]              |
      consolidate  graph   heat    episode
      (F3)         ingest  decay   check
                   (F4)    (F2)
           |        |        |        |
           +--------+--------+--------+
                    |
           [sequential, heat-gated tasks]
                    |
           contradiction_scan (if heat > threshold)
                    |
           profile_synthesis  (if heat > threshold)
                    |
           deep_monologue     (if > 24h since last)
```

### 4.2 Data Model

**New table: `background_task_runs`**

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, autoincrement | |
| `user_id` | Integer | FK → users.id, CASCADE | |
| `task_type` | String(50) | NOT NULL | consolidation, graph_ingestion, heat_decay, episode_gen, contradiction_scan, profile_synthesis, deep_monologue |
| `status` | String(20) | NOT NULL, default "pending" | pending, running, completed, failed |
| `result_json` | JSON | nullable | Task-specific result data. For `task_type="consolidation"`, completed runs **must** persist the restart cursor payload `{ "thread_id": int | null, "last_processed_message_id": int, "messages_processed": int }`. |
| `error_message` | Text | nullable | Error details on failure |
| `started_at` | DateTime(tz) | nullable | When the task started executing |
| `completed_at` | DateTime(tz) | nullable | When the task finished |
| `created_at` | DateTime(tz) | NOT NULL, server_default now() | When the task was enqueued |

**Index**: `ix_bg_task_runs_user_status` on `(user_id, status)`

### 4.3 In-Memory State

```python
# Turn counter — no persistence needed (worst case: extra run after restart)
_turn_counters: dict[int, int] = {}  # user_id -> turn_count

# Restart cursor — persisted by completed consolidation BackgroundTaskRun rows
# Scope is (user_id, thread_id) when thread_id exists, otherwise per-user
# Avoids reprocessing on restart
```

### 4.4 New File

```
apps/server/src/anima_server/services/agent/sleep_agent.py
```

### 4.5 Core Functions

```python
# Configuration
SLEEPTIME_FREQUENCY: int = 3          # Run every N turns
HEAT_THRESHOLD_CONSOLIDATION: float = 5.0  # Min heat for expensive ops

def bump_turn_counter(user_id: int) -> int:
    """Increment and return the turn counter for a user.
    Thread-safe via dict default + atomic increment.
    """
    ...

def should_run_sleeptime(user_id: int) -> bool:
    """True if turn_count % SLEEPTIME_FREQUENCY == 0."""
    ...

async def run_sleeptime_agents(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
    force: bool = False,
) -> list[str]:
    """Orchestrate all background tasks.

    This orchestrator issues structured background tasks, not autonomous,
    open-ended workers. Task selection and ordering remain explicit in code so
    runs are predictable and auditable.

    Parallel group (always run):
    1. Memory consolidation (predict-calibrate from F3)
    2. Knowledge graph ingestion (F4; separate task, current-turn / explicit-delta scoped)
    3. Heat decay (F2)
    4. Episode generation check

    Sequential group (heat-gated, skipped if heat < threshold):
    5. Contradiction scan
    6. Profile synthesis

    Time-gated:
    7. Deep monologue (only once per 24 hours)

    When force=True (inactivity timer): bypass frequency and heat gates.

    Context scope guidance by task:
    - `profile_synthesis` and `deep_monologue` may use transcript-wide context
      when current-turn inputs or explicit deltas are insufficient.
    - `graph_ingestion`, `consolidation`, `contradiction_scan`,
      `heat_decay`, `episode_gen`, and restart-cursor bookkeeping should
      remain current-turn or explicit-delta scoped when possible so behavior
      stays structured and predictable.
    - For `graph_ingestion`, that bounded scope includes stale-relation
      pruning candidates only for relations touching entities mentioned in the
      current turn, consistent with F4.

    Returns list of task run IDs for tracking.
    """
    ...

async def _issue_background_task(
    *,
    user_id: int,
    task_type: str,
    task_fn: Callable[..., Any],
    db_factory: Callable[..., object] | None = None,
    **kwargs: Any,
) -> str:
    """Fire a tracked background task.
    1. Create BackgroundTaskRun with status='pending'
    2. Update to 'running' with started_at
    3. Execute task_fn
    4. Update to 'completed' or 'failed' with result/error
    Uses finally-block to ensure state is always saved.
    """
    ...

def get_last_processed_message_id(
    user_id: int,
    thread_id: int | None = None,
) -> int | None:
    """Get the last processed message ID for the active cursor scope.

    Reads from the most recent completed `BackgroundTaskRun` where
    `task_type="consolidation"` and `result_json.thread_id` matches the
    requested thread scope. The cursor scope is `(user_id, thread_id)` when a
    thread exists, otherwise the per-user conversation stream.
    """
    ...

def update_last_processed_message_id(
    user_id: int,
    thread_id: int | None,
    message_id: int,
    messages_processed: int,
) -> None:
    """Persist the consolidation restart cursor payload in `result_json`.

    Required payload shape for completed consolidation runs:
    {
        "thread_id": thread_id,
        "last_processed_message_id": message_id,
        "messages_processed": messages_processed,
    }
    """
    ...
```

### 4.6 Modified Files

| File | Function | Change |
|------|----------|--------|
| `consolidation.py` | `schedule_background_memory_consolidation()` | Replace direct `asyncio.create_task()` with `bump_turn_counter()` + `should_run_sleeptime()` + `run_sleeptime_agents()`. Consolidation is responsible for persisting the restart cursor payload for its `(user_id, thread_id)` scope. |
| `reflection.py` | `schedule_reflection()` | Keep 5-minute timer. When it fires, call `run_sleeptime_agents(force=True)` |
| `reflection.py` | `run_reflection()` | Delegate to `run_sleeptime_agents(force=True)`. **Must preserve**: (1) `expire_working_memory_items()` call (line 86-98), (2) quick inner monologue call (line 101-118), both of which run BEFORE sleep tasks in the current flow. These are not sleep tasks — they are pre-sleep housekeeping. |
| `sleep_tasks.py` | `run_sleep_tasks()` | Keep as-is but add heat-threshold gating for contradiction scan and profile synthesis. Make callable from `run_sleeptime_agents()`. |
| `models/agent_runtime.py` | (module level) | Add `BackgroundTaskRun` model |

### 4.7 Task Parallelism

Independent tasks run via `asyncio.gather()`:

```python
await asyncio.gather(
    _issue_background_task(task_type="consolidation", task_fn=consolidate_fn, ...),
    _issue_background_task(task_type="graph_ingestion", task_fn=graph_fn, ...),
    _issue_background_task(task_type="heat_decay", task_fn=decay_fn, ...),
    _issue_background_task(task_type="episode_gen", task_fn=episode_fn, ...),
    return_exceptions=True,  # Don't let one failure cancel others
)
```

`graph_ingestion` remains a first-class task in this parallel group, but it does
not widen into a transcript-wide graph scan. Its input contract stays limited
to current-turn messages plus the explicit relation-delta candidate set F4
defines for bounded pruning.

Each task opens its own DB session via `db_factory()` — no shared session state. SQLite WAL mode handles concurrent reads.

### 4.8 Restart Cursor Contract

Restart safety is enforced through completed `BackgroundTaskRun` rows for
`task_type="consolidation"`.

- Cursor location: `background_task_runs.result_json` on the completed
  consolidation run.
- Cursor scope: `(user_id, thread_id)` when `thread_id` exists; otherwise the
  per-user conversation stream.
- Required payload on every completed consolidation run:

```json
{
  "thread_id": 123,
  "last_processed_message_id": 456,
  "messages_processed": 7
}
```

- `thread_id` may be `null` when no thread exists.
- `last_processed_message_id` is the highest conversation message ID durably
  processed for that cursor scope.
- `messages_processed` records how many messages were consumed in that
  consolidation pass so crash recovery and auditing can distinguish a no-op run
  from a replayed range.

### 4.9 Frequency Gating Logic

```python
def should_run_sleeptime(user_id: int) -> bool:
    count = _turn_counters.get(user_id, 0)
    return count > 0 and count % SLEEPTIME_FREQUENCY == 0
```

With `SLEEPTIME_FREQUENCY=3`:
- Turn 1: skip
- Turn 2: skip
- Turn 3: run
- Turn 4: skip
- Turn 5: skip
- Turn 6: run

### 4.10 Heat Gating Logic

```python
async def _should_run_expensive(db: Session, user_id: int) -> bool:
    hottest = get_hottest_items(db, user_id=user_id, limit=1)
    if not hottest:
        return False
    return hottest[0].heat >= HEAT_THRESHOLD_CONSOLIDATION
```

---

## 5. Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F5.1 | In-memory per-user turn counter with `bump_turn_counter()` | Must |
| F5.2 | `should_run_sleeptime()` checking `turn_count % frequency == 0` | Must |
| F5.3 | `SLEEPTIME_FREQUENCY` config (default: 3) | Must |
| F5.4 | `run_sleeptime_agents()` orchestrating parallel + sequential structured background tasks with explicit task ordering and tracking; `graph_ingestion` remains a separate current-turn / explicit-delta-scoped task | Must |
| F5.5 | Parallel execution of independent tasks via `asyncio.gather()` | Must |
| F5.6 | Heat-threshold gating for expensive tasks (contradiction scan, profile synthesis) | Must |
| F5.7 | `HEAT_THRESHOLD_CONSOLIDATION` config (default: 5.0) | Must |
| F5.8 | Time gating for deep monologue (once per 24h) | Must |
| F5.9 | `BackgroundTaskRun` model tracking all task executions | Must |
| F5.10 | `_issue_background_task()` with finally-block cleanup | Must |
| F5.11 | `force=True` mode bypassing frequency + heat gates (for inactivity timer) | Must |
| F5.12 | `schedule_background_memory_consolidation()` routes through the frequency-gated orchestrator | Must |
| F5.13 | `schedule_reflection()` calls `run_sleeptime_agents(force=True)` | Must |
| F5.14 | Restart safety must use a consolidation-owned cursor stored in completed `BackgroundTaskRun.result_json` payloads | Must |
| F5.15 | `return_exceptions=True` in `asyncio.gather()` so one failure doesn't cancel others | Must |
| F5.16 | Each task opens its own DB session via `db_factory()` | Must |
| F5.17 | Vault export/import support for `background_task_runs` | Could |
| F5.18 | Preserve `settings.agent_background_memory_enabled` guard from current `schedule_background_memory_consolidation()` (line 649) | Must |
| F5.19 | Preserve `companion.invalidate_memory()` call after background processing completes (currently at `run_background_memory_consolidation()` line 607-610 and `run_reflection()` line 140-143) | Must |
| F5.20 | Preserve working memory expiry (`expire_working_memory_items()`) and quick inner monologue in the `force=True` (inactivity timer) path — these run BEFORE sleep tasks, not as part of them | Must |
| F5.21 | Preserve `_background_tasks` set tracking and `drain_background_memory_tasks()` lifecycle management, or provide equivalent | Must |
| F5.22 | `get_last_processed_message_id()` / `update_last_processed_message_id()` must scope the cursor by `(user_id, thread_id)` when `thread_id` exists | Must |
| F5.23 | Completed consolidation runs must write `thread_id`, `last_processed_message_id`, and `messages_processed` in `result_json` | Must |

---

## 6. Data Model Changes

**Migration**: `20260321_0001_create_background_task_runs.py`

- New tables: **1** (`background_task_runs`)
- New indices: **1** (`ix_bg_task_runs_user_status`)

---

## 7. Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| AC1 | After 3 messages, background consolidation fires; after messages 1 and 2, it does not | Integration test: count `BackgroundTaskRun` records |
| AC2 | With `SLEEPTIME_FREQUENCY=3`, messages 3, 6, 9 trigger background work | Unit test for `should_run_sleeptime()` |
| AC3 | With heat below threshold, contradiction scan and profile synthesis are skipped | Integration test: verify no task runs of those types |
| AC4 | With heat above threshold, contradiction scan and profile synthesis fire | Integration test |
| AC5 | Independent tasks run in parallel (consolidation, graph, heat decay, episodes), with `graph_ingestion` issued as its own task rather than nested inside consolidation | Integration test: verify all 4 task types have runs for the same trigger |
| AC6 | One task failure does not cancel others | Unit test: mock one task to raise, verify others complete |
| AC7 | All background task runs recorded in `background_task_runs` with correct status | Integration test: verify status transitions pending→running→completed/failed |
| AC8 | `force=True` bypasses frequency and heat gates | Unit test |
| AC9 | 5-minute inactivity timer still triggers full suite | Integration test |
| AC10 | Foreground response is not blocked by background processing | Integration test: measure response time with and without background tasks |
| AC11 | After a crash or restart, consolidation resumes from the most recent completed cursor for the same `(user_id, thread_id)` scope and does not reprocess older messages | Integration test with persisted `BackgroundTaskRun.result_json` |
| AC12 | A cursor written for one thread does not resume processing for another thread owned by the same user | Integration test |
| AC13 | All 602 existing tests pass | CI |

---

## 8. Test Plan

| # | Type | Test | Details |
|---|------|------|---------|
| T1 | Unit | `bump_turn_counter()` | Verify counter increments: 1, 2, 3, 4... |
| T2 | Unit | `should_run_sleeptime()` | Frequency=3: true at 3, 6, 9; false at 1, 2, 4, 5 |
| T3 | Unit | Frequency gating end-to-end | Fire 5 turns, verify sleeptime agents ran only on turns 3 (with freq=3) |
| T4 | Unit | Heat gating | Mock low heat → expensive tasks skipped; mock high heat → they fire |
| T5 | Unit | `force=True` | Verify all tasks fire regardless of counter or heat |
| T6 | Unit | Task failure isolation | Mock one task to raise, verify others complete and all have correct status |
| T7 | Integration | `_issue_background_task()` | Verify task run recorded with correct status, timestamps, result |
| T8 | Integration | End-to-end 3-message flow | Send 3 messages, verify the orchestrator issues separate consolidation and `graph_ingestion` runs after the 3rd turn |
| T9 | Integration | Graph-ingestion scope contract | Trigger `graph_ingestion`, verify it receives current-turn inputs and only bounded relation-delta pruning candidates, not transcript-wide scan inputs |
| T10 | Integration | Inactivity reflection | Wait 5 minutes (mock timer), verify full suite triggers with force |
| T11 | Integration | Crash/restart resume | Seed a completed consolidation `BackgroundTaskRun` with cursor payload, restart the orchestrator, verify only newer messages are processed for that `(user_id, thread_id)` scope |
| T12 | Integration | Thread-scoped cursor isolation | Seed different cursor payloads for two threads under one user, verify each thread resumes from its own cursor |
| T13 | Regression | Full suite | All 602 tests pass |

---

## 9. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Lost work on crash** | Low | Completed consolidation runs must persist a restart cursor payload in `BackgroundTaskRun.result_json`, scoped by `(user_id, thread_id)` when present. That bounds replay after restart, and all operations are designed to be idempotent so limited re-running is safe. |
| **Race conditions** | Medium | Each task opens own DB session via `db_factory()`. SQLite WAL mode handles concurrent reads. No shared mutable state between tasks. |
| **Turn counter lost on restart** | Low | Worst case: one extra background run or one skipped run. The persisted consolidation cursor prevents reprocessing of older messages within the same cursor scope. |
| **Frequency tuning** | Low | `SLEEPTIME_FREQUENCY` is a module-level constant. Start with 3, adjust based on observation. |
| **Heat threshold tuning** | Low | `HEAT_THRESHOLD_CONSOLIDATION` defaults to 5.0. If too high, expensive tasks never fire; if too low, they fire too often. Mitigated by `force=True` on inactivity timer guaranteeing they run at least once per idle period. |

---

## 10. Rollout

1. Create `BackgroundTaskRun` model in `models/agent_runtime.py`
2. Create Alembic migration for `background_task_runs` table
3. Create `sleep_agent.py` with all functions
4. Write unit tests for turn counting, frequency gating, heat gating
5. Modify `consolidation.py` to route through frequency-gated orchestrator
6. Modify `reflection.py` to call `run_sleeptime_agents(force=True)`
7. Add heat-threshold gating to `sleep_tasks.py` for expensive operations
8. Write integration tests for end-to-end flows
9. Run full test suite (602+ tests)
10. Ship as single PR

---

## 11. References

- Letta `sleeptime_multi_agent_v4.py` — `SleeptimeMultiAgentV4.step()`, `run_sleeptime_agents()`, frequency gating
- Letta `letta_agent_v3.py` — context token estimation, conversation scoping
- Letta sleep-time compute paper (arXiv 2504.13171) — empirical validation of async background processing
- [Implementation Plan Phase 5](../memory-implementation-plan.md) — detailed function signatures and modified file list
