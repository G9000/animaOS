# ARH-003 - Optimistic locking for soul-block writes

- Status: in-review
- Priority: P0
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: none
- Owner: Claude (Fable 5)
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 12:53 MYT
- Started: 2026-07-07 01:25 MYT
- Completed: 2026-07-07 01:55 MYT

## Goal

Background reflections can no longer silently erase memory writes made during the same window; soul-block writes are version-checked.

## Problem

The deep monologue snapshots persona/intentions/working_memory (`services/agent/inner_monologue.py:633-795`), holds them across a long LLM call, then full-replaces the blocks (`set_soul_block` at `:819-827`, intentions rebuild at `:886-890`). `_write_soul_block` (`soul_blocks.py:26-57`) increments `version` but never checks it — no optimistic locking. Concrete loss: user says "call me Jay" mid-monologue → tool queues a pending op → soul writer appends it (`soul_writer.py:700-725`) → the monologue's stale full-replace lands seconds later and deletes it. Quick reflection (`inner_monologue.py:187-207`) has the same append-to-snapshot pattern for working_memory. `reflection.py` cancels only *pending* reflections on new activity, not in-flight ones. Separately, each deep-monologue run appends a fresh `## Learned Rules` section onto intentions text that already contains the previous one, growing without bound (`inner_monologue.py:886-890`, cap `MAX_PROCEDURAL_RULES=10` in `intentions.py:52` never applied here).

## Implementation Notes

1. Add `expected_version: int | None = None` to `_write_soul_block` (`soul_blocks.py:26`). When provided and the current row version differs, raise a `SoulBlockConflict` (new, small exception) instead of writing.
2. At every read-modify-write call site (deep monologue persona/working_memory, quick reflection working_memory, intentions rebuild), capture `block.version` at snapshot time and pass it on write. On `SoulBlockConflict`: re-read the block, re-apply the *transformation* (not the stale full text) once, and if it conflicts again, drop the update with a WARNING on `anima.runtime.degraded`. Re-apply means: for append-shaped updates, append to the fresh text; for the monologue's rewrite, either merge the LLM output onto the fresh text or discard (discarding a reflection is safe; discarding a user write is not — that asymmetry is the whole point).
3. The soul writer's pending-op path is the authoritative writer; it may keep writing without `expected_version` (its ops are deltas, not snapshots).
4. Replace the Learned Rules append with a replace-section operation using `intentions.py` helpers, applying `MAX_PROCEDURAL_RULES`.

## Deliverables

- Version-checked `_write_soul_block` with conflict exception.
- All snapshot-based writers (deep monologue, quick reflection, intentions rebuild) pass `expected_version` and handle conflict by re-read/re-apply-once.
- Learned Rules section replaced, not appended; rule cap enforced.
- Test: start a monologue-style write with a stale snapshot, land a pending-op append in between, assert the append survives and the block contains both changes (or the reflection is dropped, never the user write).

## Acceptance

- A pending-op write landing during an in-flight reflection is never lost.
- Intentions block no longer accumulates duplicate `## Learned Rules` sections.
- Conflict drops are visible at WARNING.
- Focused tests pass.

## Activity Log

- 2026-07-07 00:28 MYT - Ticket created.
- 2026-07-07 01:55 MYT - Implemented on branch `worktree-agent-runtime-hardening-p1`: `SoulBlockConflict` + `expected_version` on `_write_soul_block`/`set_soul_block`/`full_replace_soul_block`; same check on `set_working_context`/`set_active_intentions` (both legacy and modern tables — `version` columns already existed, no migration) and threaded through `set_self_model_block`; deep monologue and quick reflection snapshot versions at read and drop stale updates with a WARNING on `anima.runtime.degraded` (quick reflection re-applies its working-memory add/remove deltas onto fresh content once before dropping); Learned Rules section now replaced via `merge_learned_rules` (dedup + `MAX_PROCEDURAL_RULES` cap) instead of appended without bound. Soul-writer pending ops stay unversioned by design (delta ops against fresh reads — the authoritative writer).

## Validation

- Commands:
  - `uv run --directory apps/server pytest tests/test_soul_block_locking.py -q` → 14 passed
  - `uv run --directory apps/server pytest tests/test_p3_self_model_split.py tests/test_soul_writer.py tests/test_agent_reflection.py -q` → 58 passed
- Changed paths:
  - apps/server/src/anima_server/services/agent/soul_blocks.py
  - apps/server/src/anima_server/services/agent/self_model.py
  - apps/server/src/anima_server/services/agent/intentions.py
  - apps/server/src/anima_server/services/agent/inner_monologue.py
  - apps/server/tests/test_soul_block_locking.py
- Notes:
  - 14 new tests including the motivating race (concurrent append survives a stale full-replace) and an end-to-end quick-reflection test where a mid-LLM concurrent write survives and the reflection's deltas are re-applied.
