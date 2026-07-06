# ARH-013 - Deduplicate drifted turn and sleep logic

- Status: backlog
- Priority: P2
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: `ARH-002`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 00:28 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
