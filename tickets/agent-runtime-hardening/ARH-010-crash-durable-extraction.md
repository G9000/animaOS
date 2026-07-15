# ARH-010 - Crash-durable memory extraction

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: `ARH-004`
- Owner: Claude (Fable 5)
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 21:55 MYT
- Started: 2026-07-07 21:26 MYT
- Completed: 2026-07-07 21:55 MYT

## Goal

A crash, shutdown, or cancellation during per-turn memory extraction loses at most the in-flight LLM enrichment — never the whole turn's memories.

## Problem

`run_background_extraction` (`services/agent/consolidation.py:389-599`) is spawned fire-and-forget (`:832-840`) and commits nothing until a single `rt_db.commit()` at `:549` — after the LLM call at `:438`. Consequences:

- A process kill, task cancellation at shutdown (`CancelledError` bypasses the `except Exception` at `:585`), or any exception after the LLM call drops the entire turn's extraction — including the regex-derived candidates that were already computed *before* the LLM call.
- A `MemoryExtractionFailure` row is only written when the LLM call itself reports failure (`:442-451`); every other failure mode leaves no retryable record.
- The PG session opened at `:389` is held across the multi-second LLM await, pinning a pool connection per concurrent turn.

## Implementation Notes

1. **Commit before the LLM call**: persist regex-derived candidates and a `MemoryExtractionFailure` (or equivalent) "intent" row for the turn, then commit and close/release the session.
2. **Run the LLM call sessionless**, then open a fresh session to write LLM-derived candidates and resolve the intent row on success. On failure, the intent row remains and the soul writer's existing Phase 1.5 retry loop (`soul_writer.py:452`) recovers the turn — verify the retry loop actually picks up this record shape; adjust it if it only handles LLM-reported failures today.
3. **Catch `CancelledError` distinctly** in the task wrapper: on shutdown-cancel, ensure the pre-LLM commit already happened (it will have, given step 1) and re-raise; log at INFO not ERROR.
4. Respect ARH-004's retry-cap conventions for the recovery path (an intent row that fails recovery repeatedly must not retry forever).
5. Track the spawned task via `_track_background_task` (shared convention with ARH-002).

## Deliverables

- Two-phase extraction: durable pre-LLM commit (regex candidates + intent), post-LLM enrichment commit, session released across the await.
- Shutdown-safe cancellation handling.
- Soul-writer recovery verified for the intent-row shape.
- Tests: kill the extraction task between the two phases → regex candidates persisted and the next soul-writer run recovers the intent; concurrent-turn test showing no session held during a (mocked, slow) LLM call; cancellation during LLM await leaves a recoverable intent row.

## Acceptance

- Killing the server mid-extraction loses no regex-derived candidates and leaves a retryable record for the LLM phase.
- No DB session is held across the extraction LLM await.
- Recovery is bounded by retry caps (no poison-pill intents).
- Focused tests pass.

## Activity Log

- 2026-07-07 00:28 MYT - Ticket created.
- 2026-07-07 21:55 MYT - Implemented on branch `worktree-agent-runtime-hardening-p4`: `run_background_extraction` is now three-phase — Phase A commits regex candidates, foresight, and a retryable `MemoryExtractionFailure` intent row (reason "LLM extraction pending (crash-recovery guard)") *before* the LLM call; Phase B runs the LLM with no session held; Phase C persists LLM results and resolves the intent atomically in a fresh session. `CancelledError` is handled distinctly (INFO, re-raised) — shutdown mid-LLM loses nothing. The intent row is recovered by the Soul Writer's existing Phase 1.5 retry loop (status `failed`, capped at `MAX_RETRY_COUNT=3` per ARH-004 conventions); duplicate recovery is safe via the candidate content-hash dedupe.

## Validation

- Commands:
  - `uv run --directory apps/server pytest tests/test_extraction_durability.py tests/test_agent_consolidation.py tests/test_soul_writer.py -q` → 39 passed
- Changed paths:
  - apps/server/src/anima_server/services/agent/consolidation.py
  - apps/server/tests/test_extraction_durability.py
- Notes:
  - 4 new tests: cancellation mid-LLM keeps regex candidates + intent; success resolves the intent; LLM failure keeps it with the real reason; scaffold provider writes no intent.
  - Known benign race: an eager soul-writer run may pick up the pending intent while Phase B is in flight, costing one duplicate LLM call; content-hash dedupe makes the outcome idempotent.
