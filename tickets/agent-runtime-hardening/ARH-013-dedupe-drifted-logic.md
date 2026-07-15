# ARH-013 - Deduplicate drifted turn and sleep logic

- Status: done
- Priority: P2
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: `ARH-002`
- Owner: Claude (Fable 5)
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-08 03:40 MYT
- Started: 2026-07-08 01:00 MYT
- Completed: 2026-07-08 03:40 MYT

## Goal

Exactly one copy of each critical loop: one step-tool-call pipeline shared by invoke and approval-resume, one sleep orchestrator, one stream pump — plus the mid-turn memory refresh no longer dropping prompt inputs.

## Problem

1. **Approval resume drifted.** `resume_after_approval` (`services/agent/runtime.py:756-1133`) re-implements ~380 lines of `invoke`'s validation/approval/terminal/execution loop (`:434-675`) with behavioral differences already present: no deferred-call handling, no consecutive-failure tool exclusion, no memory refresh, no sandwich continuation. Every main-loop fix must be hand-ported or resumes silently diverge.
2. **Memory refresh drops inputs.** When a tool sets `memory_modified`, the system prompt is rebuilt (`runtime.py:632-645`) without re-applying `_append_action_tool_prompt` (applied only at `:227`) and without `conversation_turn_count` — after a `save_to_memory` call, the model loses connected-client action tool descriptions and relationship-stage instructions mid-turn.
3. **Two sleep orchestrators.** `run_sleep_tasks` (`sleep_tasks.py:77-252`) shadows `run_sleeptime_agents` (`sleep_agent.py:178-343`): reachable only from the manual `/sleep` endpoint (`api/routes/chat.py:571-576`), bypasses heat gating and `RuntimeBackgroundTaskRun` tracking, and has drifted (its step 0.5 computes a `refs_regenerated` count that does nothing).
4. **Two stream pumps.** `service.py:540-593` vs `:2715-2780` are near-identical queue pumps.
5. **Dead params + fragile cursor.** `initial_sequence_id` on `_persist_turn_result` (`service.py:2361`) and `_persist_approval_checkpoint` (`:2280`) is ignored (fresh sequences reserved inside) — an invitation for a double-use bug. The consolidation cursor loads **all** completed task-run rows and Python-filters `result_json` (`sleep_agent.py:736-756`, `:774-798`) — the table grows ~6-10 rows every 3rd turn, nothing prunes it, and the cursor mutates `result_json` in place.

## Implementation Notes

1. Extract `_process_step_tool_calls(...)` covering validation → approval checkpoint → terminal handling → execution → deferred calls → failure exclusion → memory refresh → message append. `invoke` and `resume_after_approval` both call it; the resume path passes its restored state. Behavior differences that are *intentional* (if any surface during extraction) get explicit parameters, not divergent copies. This is the riskiest refactor in the epic — do it with characterization tests first (record current invoke behavior on a multi-tool fixture, assert identical after).
2. In the extracted refresh step, thread `extra_tool_schemas` and `conversation_turn_count` through `build_system_prompt_with_budget` so the rebuild matches the original assembly (coordinate with ARH-006: rebuild must only touch volatile suffix blocks).
3. Point `/sleep` at `run_sleeptime_agents(force=True)`; delete `run_sleep_tasks` and its dead step 0.5. Manual runs then get task-run tracking and cursor updates for free.
4. Extract one `_stream_via_queue()` helper (keep ARH-002's sentinel fix inside it).
5. Delete the ignored `initial_sequence_id` params. Move the consolidation cursor to a dedicated `(user_id, thread_id) → last_message_id` table (Alembic) written on task completion; add `RuntimeBackgroundTaskRun` retention to the existing `_periodic_prune_sweep` (`main.py:137`), keeping e.g. the last 30 days or last N per task_type.

## Deliverables

- Shared `_process_step_tool_calls` used by both entry points; approval resume regains deferred-call handling, failure exclusion, and (fixed) memory refresh.
- Single sleep orchestrator; `/sleep` endpoint delegates with `force=True`.
- Single stream pump helper.
- Dead params removed; cursor table migration + task-run retention sweep.
- Tests: characterization suite for the multi-tool step loop (pre/post identical); approval-resume path exercises deferred calls and memory refresh; `/sleep` produces task-run rows; cursor survives task-run pruning.

## Acceptance

- `runtime.py` contains one tool-call processing loop; a fix applied there affects both invoke and resume paths by construction.
- Mid-turn memory refresh preserves action tools and relationship instructions.
- `run_sleep_tasks` is gone; manual sleep runs are tracked.
- Cursor lookups no longer scan the full task-run table.
- Focused tests pass; migration up/down clean.

## Activity Log

- 2026-07-07 00:28 MYT - Ticket created.
- 2026-07-08 02:20 MYT - Partial implementation on branch `worktree-agent-runtime-hardening-p6` (4 of 5 sub-items landed with tests; the two highest-risk items deferred with rationale below).
  - **#4 Single stream pump (done).** `stream_agent` and `stream_approve_or_deny` had near-identical bounded-queue SSE pumps; extracted `_stream_via_queue(run_turn, *, failure_log)`. Each caller now supplies only the worker coroutine and its failure-log line; the ARH-002 non-blocking-sentinel safety lives in the one helper.
  - **#5a Dead `initial_sequence_id` params (done).** `_persist_turn_result` and `_persist_approval_checkpoint` ignored the param (both reserve fresh sequences internally). Removed from both signatures, their call sites, and the `_prepare_turn_context` return tuple (the returned value was only ever fed to these ignoring callers). Updated the 4 approval-reentry tests that passed it.
  - **#2 Mid-turn memory refresh dropped inputs (done).** When a tool set `memory_modified`, `invoke` rebuilt the system prompt without threading `conversation_turn_count` and without re-appending `_append_action_tool_prompt(extra_tool_schemas)` — so a `save_to_memory` mid-turn stripped connected-client action tools and reset relationship-stage instructions to `first_contact`. The rebuild now mirrors the initial assembly. New regression test asserts both signals survive.
  - **#5b Consolidation cursor table + task-run retention (done).** The cursor lived in each consolidation task-run's `result_json`; reads scanned every completed row and Python-filtered by `thread_id`, writes mutated the row in place, and nothing pruned the ever-growing table. Moved it to a dedicated `runtime_consolidation_cursors` table (migration 025, up/down verified) keyed on `(user_id, thread_id)`; accessors do an indexed select-then-upsert (NULL-aware for the global scope), and `_task_consolidation` now persists the advance explicitly. Added `prune_old_background_task_runs` to the 6-hourly sweep (default 30-day retention). Tests: round-trip / scope isolation / single-row upsert / cursor-survives-pruning.
- 2026-07-08 03:05 MYT - **#1 Extract `_process_step_tool_calls` shared by invoke + resume — DONE** (characterization-test-first, as the ticket mandates):
  1. Wrote characterization tests for the three previously-uncovered `invoke` behaviors (deferred blocked-tool post-turn execution, consecutive-failure exclusion, flush-on-approval) — locking current behavior before touching code.
  2. Extracted two methods: `_execute_validated_calls` (non-terminal parallel + terminal sequential + reply policy + solver/tools_used/failure tracking) and `_process_step_tool_calls` (validate → defer → flush-before-stop → execute), plus small `_FailureTracker`/`_StepToolCallResult` dataclasses. `invoke`'s per-step loop now calls the helper; all characterization + existing `test_agent_runtime` + `test_runtime_enhancements` stayed green (pure refactor).
  3. Rewired `resume_after_approval`'s follow-up step to the same helper (`user_message=None`, no action tools, `deferred_tool_calls=None`, no failure tracker — deferral/exclusion are loop concepts that don't apply to the single follow-up step). All 56 `test_approval_reentry` tests stayed green; added a resume characterization test proving the follow-up now runs the shared multi-tool pipeline.
  - Two intentional consistency improvements to `invoke` fell out of unifying the flush semantics (both validated by the characterization suite): its pre-approval flush now splits terminal/non-terminal and applies the reply policy (was `execute_parallel(all)`), and a rule violation now flushes already-validated safe calls before reporting the violation (was: silently dropped). `resume` behavior is otherwise preserved (it already flushed before both stops).
- 2026-07-08 03:40 MYT - **#3 One sleep orchestrator — DONE.**
  - Folded structured profile-field reconciliation (`reconcile_profile_from_claims`) into `_task_profile_synthesis`, so the automatic sleep path now reconciles too (it previously never did — reconciliation was only reachable via the manual `run_sleep_tasks`).
  - Deleted `run_sleep_tasks`, its dead step 0.5 (a `refs_regenerated` count that cleared nothing), and the now-orphaned `SleepTaskResult`.
  - Repointed the `/sleep` endpoint at `run_sleeptime_agents(force=True)`: manual runs now get `RuntimeBackgroundTaskRun` tracking + consolidation-cursor updates and stay in sync with the per-turn path. The count-shaped response the Consciousness UI consumes (`contradictionsResolved`/`itemsMerged`/`episodesGenerated`) is rebuilt from the issued task runs' stored `result_json` — no frontend change.
  - **Decision on freshness (answering the open question):** `force` bypasses the *heat* gate only, NOT the ARH-007 input-freshness dirty-check. `test_background_dirty_checks` documents that even a forced run skips synthesis whose inputs are unchanged; bypassing freshness would contradict that tested design and re-run work needlessly. So the correct approach kept freshness intact — no ARH-007 semantics change.
  - Tests: profile reconciliation now tested directly on `_task_profile_synthesis`; a relocated `test_run_sleeptime_agents_invalidates_companion_cache` (companion invalidation is now an unconditional post-run step, no longer coupled to reconciliation); a `/sleep` endpoint test asserting the response mapping; the obsolete `run_sleep_tasks` ordering test removed (ordering is structural + covered by `test_background_dirty_checks`).

## Validation

- Commands:
  - #1 extraction: `pytest tests/test_step_loop_characterization.py tests/test_agent_runtime.py tests/test_runtime_enhancements.py tests/test_approval_reentry.py` → 86 + 5 passed (characterization tests identical pre/post extraction; `invoke` pure refactor; `resume` rewired). `test_chat` 17 passed; `test_agent_service` 35 tests with only the known recalled-image-pill pre-existing failure.
  - #2/#4/#5a/#5b: `pytest tests/test_agent_runtime.py tests/test_runtime_enhancements.py tests/test_approval_reentry.py tests/test_sleep_agent.py tests/test_ws.py` → 117 passed
  - #3 sleep merge: `pytest tests/test_sleep_agent.py tests/test_background_dirty_checks.py tests/test_agent_consolidation.py tests/test_user_profile.py` → 80 passed; `tests/test_chat.py::test_sleep_endpoint_maps_task_run_results_to_counts` → 1 passed. `run_sleep_tasks`/`SleepTaskResult` deleted; `/sleep` delegates to `run_sleeptime_agents(force=True)`.
  - Migration 025 up/down/re-up verified on a stamped-at-024 sqlite DB (table + both indexes created, dropped on downgrade, idempotent re-upgrade)
- Changed paths:
  - apps/server/src/anima_server/services/agent/service.py
  - apps/server/src/anima_server/services/agent/runtime.py
  - apps/server/src/anima_server/models/runtime.py
  - apps/server/alembic_runtime/versions/025_consolidation_cursor.py
  - apps/server/src/anima_server/services/agent/sleep_agent.py
  - apps/server/src/anima_server/services/agent/eager_consolidation.py
  - apps/server/src/anima_server/main.py
  - apps/server/src/anima_server/config.py
  - apps/server/tests/test_approval_reentry.py
  - apps/server/tests/test_runtime_enhancements.py
  - apps/server/tests/test_sleep_agent.py
  - apps/server/tests/test_step_loop_characterization.py
- Notes:
  - All six sub-items landed (#1 shared step pipeline, #2 mid-turn refresh fix, #3 single sleep orchestrator, #4 single stream pump, #5a dead-param removal, #5b cursor table + retention). Behavior-preserving except: the intended mid-turn-refresh correctness fix (#2); two documented `invoke` consistency improvements from #1 (pre-approval flush splits/applies policy; violation flushes already-validated calls before reporting); and #3's intended additions (the automatic sleep path now reconciles profile fields; manual `/sleep` runs are now tracked and heat-gate-bypassed but still freshness-gated).
