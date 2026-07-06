# ARH-003 - Optimistic locking for soul-block writes

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
