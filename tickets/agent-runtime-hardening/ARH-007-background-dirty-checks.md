# ARH-007 - Dirty-checks for background cognition

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: `ARH-004`
- Owner: Claude (Fable 5)
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 18:18 MYT
- Started: 2026-07-07 13:25 MYT
- Completed: 2026-07-07 18:18 MYT

## Goal

Background cognition stops re-buying LLM calls for unchanged inputs — persisted contradiction verdicts, input-freshness checks before each sleep task, and gated emotional-pattern promotion.

## Problem

1. **Contradiction scan re-buys verdicts.** `scan_contradictions` (`services/agent/sleep_tasks.py:267-294`) does O(n²) pairwise similarity over up to 100 items/category, then sends up to 10 pairs × 4 categories = 40 LLM calls per run. "COMPATIBLE" verdicts are never persisted, so identical stable pairs are re-sent every run — and `run_reflection` calls the orchestrator with `force=True` (`reflection.py:149-157`), bypassing the heat gate, so the full scan fires after every 5-minute idle lull. Dominant recurring LLM cost at mature memory size.
2. **No dirty check anywhere.** Every idle lull re-runs pattern synthesis (~24 episode summaries re-rendered into an LLM call, `pattern_synthesis.py:181-195`) and profile synthesis (50 facts, `sleep_tasks.py:396-401`) even with zero new episodes/candidates since the last run.
3. **Per-turn emotional-pattern promotion.** Soul-writer Phase 4 (`soul_writer.py:426-449`) says "(if due)" but has no gate: every run scans 50 emotion signals and writes SQLCipher rows — on practically every turn (`consolidation.py:563-570`), contradicting the keep-SQLCipher-off-the-hot-path design (`consolidation.py:601-603`). It's also duplicated from the deep monologue (`inner_monologue.py:980-994`), and the same conversation's emotion is recorded twice (turn extraction at `consolidation.py:514-529` + quick reflection at `inner_monologue.py:243-300`), letting `MIN_SIGNALS_FOR_PATTERN=3` fire off one conversation.

## Implementation Notes

1. **Verdict cache.** New small table (Alembic, after ARH-004's revision): `contradiction_checks(user_id, item_a_hash, item_b_hash, verdict, checked_at)` with a unique key on the hash pair (order-normalized). Hash = content hash of each item at check time, so an edited item naturally invalidates. Before sending a pair to the LLM, skip if a row exists for the current hashes. Persist all verdicts, including COMPATIBLE. Prune rows whose hashes no longer match any live item in the existing prune sweep.
2. **Drop `force=True` for the contradiction scan** specifically (keep force semantics for cheap tasks if needed): idle-lull reflection should run the scan only when its heat/dirty gate passes.
3. **Input-freshness gate.** In the orchestrator (`sleep_agent.py:178-343`), before each task compare `max(created_at/updated_at)` of that task's inputs (episodes for pattern synthesis, memory items/candidates for profile synthesis, emotion signals for pattern promotion) against the newest completed `RuntimeBackgroundTaskRun` of that `task_type` (gate infra from ARH-004). Skip and record a `skipped_unchanged` run result when nothing is newer.
4. **Phase 4 gating.** Gate emotional-pattern promotion on new-signal count (e.g. ≥3 new signals) or elapsed time (≥1h); make the deep monologue the single call site or the soul-writer path the single site — not both. Tag reflection-derived emotion signals with their source conversation/turn so duplicates of turn-derived signals are skipped or not double-counted toward `MIN_SIGNALS_FOR_PATTERN`.

## Deliverables

- `contradiction_checks` migration + skip logic; forced idle runs no longer bypass the scan's gate.
- Per-task input-freshness skip in the sleep orchestrator with `skipped_unchanged` visibility.
- Gated, single-call-site emotional-pattern promotion; deduped emotion signals.
- Tests: second scan over unchanged items makes zero LLM calls; editing one item re-checks only its pairs; orchestrator skips tasks with no new inputs and runs them when inputs appear; one conversation cannot satisfy `MIN_SIGNALS_FOR_PATTERN` alone.

## Acceptance

- An idle user who triggers repeated reflection lulls with no new memories incurs zero contradiction/pattern/profile LLM calls after the first cycle.
- Verdict cache invalidates on content change.
- Focused tests pass; migration up/down clean.

## Activity Log

- 2026-07-07 00:28 MYT - Ticket created.
- 2026-07-07 18:18 MYT - Implemented on branch `worktree-agent-runtime-hardening-p3`: migration `023_contradiction_checks` + `ContradictionCheck` model persist every scan verdict keyed on an order-normalized content-hash pair (editing an item naturally re-checks only its pairs); `scan_contradictions` skips cached pairs and decrypts each item once instead of per-comparison; the orchestrator computes the heat gate even under `force` — the contradiction scan honors it on idle-lull runs while other expensive tasks keep force semantics; per-task input-freshness gates skip `contradiction_scan`/`profile_synthesis`/`pattern_synthesis` when nothing they read changed since their last completed run (logged as `skipped_unchanged`); soul-writer Phase 4 emotional-pattern promotion is gated on new-signal count or a 1h interval derived from `CoreEmotionalPattern.last_observed` (restart-safe) and the duplicate deep-monologue call site was removed; quick reflection dedupes its emotion signal against the per-turn extraction's within a 15-minute window so one conversation cannot double-count toward `MIN_SIGNALS_FOR_PATTERN`.

## Validation

- Commands:
  - `uv run --directory apps/server pytest tests/test_background_dirty_checks.py -q` → 11 passed
  - `uv run --directory apps/server pytest tests/test_sleep_agent.py tests/test_background_dirty_checks.py -q` → 33 passed (one pre-existing force-mode test updated to the new contract)
  - Broader sweep (consciousness, soul writer, reflection, creation flow, profile, baseline probes, block locking, runtime DB) → 227 passed, 2 known pre-existing pgvector-environment failures
  - Migration chain validated: single head `023_contradiction_checks`
- Changed paths:
  - apps/server/alembic_runtime/versions/023_contradiction_checks.py
  - apps/server/src/anima_server/models/runtime_memory.py
  - apps/server/src/anima_server/services/agent/sleep_tasks.py
  - apps/server/src/anima_server/services/agent/sleep_agent.py
  - apps/server/src/anima_server/services/agent/soul_writer.py
  - apps/server/src/anima_server/services/agent/emotional_patterns.py
  - apps/server/src/anima_server/services/agent/emotional_intelligence.py
  - apps/server/src/anima_server/services/agent/inner_monologue.py
  - apps/server/tests/test_background_dirty_checks.py
  - apps/server/tests/test_sleep_agent.py
- Notes:
  - 11 new tests: verdict cache (no re-buy, content-edit invalidation), freshness gate truth table, force no longer bypasses the contradiction heat gate, unchanged inputs skip synthesis, promotion gate thresholds, reflection-signal dedupe window.
