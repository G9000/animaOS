# ARH-004 - Background retry hygiene and persisted gates

- Status: backlog
- Priority: P1
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

Every background retry loop has a cap and backoff; every "last time X ran" gate survives a process restart.

## Problem

1. **Poison-pill pending ops.** Every soul-writer run resets `failed=False` on all failed ops and reprocesses them (`services/agent/soul_writer.py:238-255`). `PendingMemoryOp` (`models/pending_memory_op.py:14-49`) has no `retry_count` (unlike `MemoryCandidate`, capped at 3). A deterministically-failing op (unknown `op_type` at `:723`, corrupt content) retries on every turn forever and can saturate `get_pending_ops`' 50-row limit, starving valid ops.
2. **Uncapped archival retries.** `inactivity_sweep` (`eager_consolidation.py:180-224`, scheduled every 60s from `main.py:127-135`) re-selects every closed-but-unarchived thread; a deterministic `export_transcript` failure re-triggers the full pipeline once per minute forever. No retry counter, no backoff, no terminal state.
3. **In-process gates.** `_last_deep_monologue` (`sleep_tasks.py:437-462`) is a process dict, so every restart re-arms the most expensive reflection (this is a desktop app — restarts are frequent).
4. **Batch-corrupting IntegrityError fallback.** Soul-writer Phase 2 flush failure calls `rollback()` (reverting all in-memory status changes), mutates one candidate post-rollback, marks it `failed` without incrementing `retry_count`, and `break`s — one duplicate-hash collision aborts the batch inconsistently (`soul_writer.py:329-351`).

## Implementation Notes

1. Alembic migration (next free version after 021): add `retry_count INTEGER NOT NULL DEFAULT 0` to `pending_memory_ops`. On failure increment; skip ops with `retry_count >= 3` in `get_pending_ops` (and stop resetting their `failed` flag). Log skipped-dead-op counts at WARNING.
2. Same migration (or a sibling): archival retry state on `RuntimeThread` — `archive_retry_count`, `archive_next_retry_at`, and a terminal `archive_failed` marker (column or status value). `inactivity_sweep` filters `next_retry_at <= now`, backs off exponentially (1m → 2m → 4m … cap 1h), and after N failures (suggest 8) sets terminal state surfaced via health/logs.
3. Deep-monologue gate: derive from the newest completed `RuntimeBackgroundTaskRun` with `task_type='deep_monologue'` instead of the dict. Keep the dict as a cheap same-process fast path if desired, but the DB row is authoritative.
4. Rework the Phase 2 fallback to per-candidate `begin_nested()` savepoints, mirroring `candidate_ops.py:107-112`: a single collision marks that candidate failed (with `retry_count` increment) and the rest of the batch proceeds.

## Deliverables

- Migration adding `PendingMemoryOp.retry_count` and thread archival retry state.
- Capped, back-off archival sweep with terminal failure state.
- Restart-safe deep-monologue gate from `RuntimeBackgroundTaskRun`.
- Savepoint-per-candidate Phase 2 flush.
- Tests: a permanently-failing pending op is skipped after 3 attempts; archival backoff schedule respected; deep-monologue gate honored across a simulated restart (new session, same DB); one IntegrityError doesn't abort batch-mates.

## Acceptance

- No background loop in `soul_writer.py` / `eager_consolidation.py` can retry a deterministic failure unboundedly.
- Deep monologue does not re-run within 24h across restarts.
- Dead ops/threads are visible in logs, not silent.
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
