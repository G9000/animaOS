# ARH-002 - Cancellation-safe turn lifecycle

- Status: backlog
- Priority: P0
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 00:28 MYT
- Started:
- Completed:

## Goal

A client disconnect at any stage of a turn — not just during model invocation — leaves no run row stuck in `running`, no deadlocked stream worker, and no garbage-collected background task.

## Problem

Three related gaps in `services/agent/service.py`:

1. **Stranded runs.** `CancelledError` is handled only around `_invoke_turn_runtime` (`service.py:754-758`). The run + user message are committed early (`:1062`), but the other cleanup paths catch only `Exception` (`:725`, `:1079`, `:826`), which `CancelledError` bypasses. Disconnect during `_assemble_turn_context` (awaits at `:1165`, `:1186`) or the `run_started` emit (`:711`) leaves the run `running` forever and the user message replays as unanswered history next turn.
2. **Sentinel deadlock.** The stream worker's `finally: await queue.put(None)` (`:2760`, also `:573`) blocks forever if the bounded queue (256, `config.py:66`) filled after the consumer stopped reading; the generator's `finally: await worker_task` (`:2776`) then never returns, leaking the task and pinned DB sessions.
3. **Weak task refs.** `loop.create_task(on_thread_close(...))` at `api/routes/threads.py:105-111`, `:162-168` and `service.py:918`, `:2825` keeps no strong reference; the event loop holds only weak refs, so thread-close consolidation can be GC'd mid-flight.

## Implementation Notes

1. `_fail_turn_setup` (`service.py:1097`) already accepts `exc: BaseException`. Add `except asyncio.CancelledError` handlers (or widen to `BaseException` with re-raise after cleanup) at the three call sites mirroring the Stage-2 handler: mark the run failed/cancelled, commit, then re-raise the `CancelledError` so cancellation semantics are preserved.
2. Replace the sentinel `await queue.put(None)` with `queue.put_nowait(None)` wrapped in `contextlib.suppress(asyncio.QueueFull)` — the consumer already treats worker completion as end-of-stream, so a dropped sentinel is safe.
3. Route all four fire-and-forget sites through the existing `_track_background_task` (`service.py:156-160`), which retains strong refs and logs exceptions.
4. Related low-hanging fix while here: `companion.py:219-223` — `set_cancel` for already-finished runs inserts pre-set events that are never evicted; guard on run liveness.

## Deliverables

- `CancelledError`-aware cleanup at every turn stage in `service.py`.
- Deadlock-free stream-worker shutdown.
- All fire-and-forget consolidation tasks tracked via `_track_background_task`.
- Tests: cancel a turn task during context assembly → run row ends `failed`/`cancelled`, not `running`; fill the queue, cancel the consumer → worker task completes.

## Acceptance

- No code path can leave a run row `running` after the request task is cancelled.
- Stream worker shutdown never awaits a put on a full queue.
- `grep create_task` over `service.py` + `api/routes/threads.py` shows no untracked fire-and-forget task.
- Focused tests pass.

## Activity Log

- 2026-07-07 00:28 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
