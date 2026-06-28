# SUM-001 - Baseline memory truth audit and eval probes

- Status: done
- Priority: P1
- Scope: `apps/server`, `docs/architecture/memory`, `docs/prds/memory`
- Parent: `SUM-000`
- Depends on: none
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-29 03:32 MYT
- Started: 2026-06-29 02:30 MYT
- Completed: 2026-06-29 03:32 MYT

## Goal

Establish the true live state of the memory system and add baseline recall probes before changing architecture.

## Deliverables

- Code/doc drift audit for predict-calibrate, retrieval routing, evidence, KG export/import, heat scoring, and sleep tasks.
- Focused memory eval probes for factual, emotional, profile, temporal, and pattern recall.
- Small fixes for high-impact known gaps when still present.
- Updated architecture notes where docs are stale.

## Acceptance

- Audit lists live code paths with file references.
- Baseline tests or eval probes can run deterministically without real provider calls.
- Any tiny fixes include focused tests.
- Follow-up tickets are updated if audit changes the plan.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-06-29 02:30 MYT - Claimed by Codex on branch `codex/sum-001-memory-baseline`; starting baseline audit and deterministic eval probes.
- 2026-06-29 03:12 MYT - Added live baseline audit, deterministic recall probes, focused fixes for retrieval heat pool ordering, sleep restart cursor metadata, KG vault round-trip, and completed validation.
- 2026-06-29 03:32 MYT - Addressed Codex review feedback by preserving fresh unscored memories in the retrieval candidate pool and adding KG capsule coverage; validation rerun.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_bm25_index.py::TestRustBackedKeywordSearch::test_bm25_search_uses_rust_memory_index_when_clean apps/server/tests/test_memory_scored_retrieval.py::test_scored_retrieval_pool_keeps_hot_older_items apps/server/tests/test_memory_scored_retrieval.py::test_scored_retrieval_pool_keeps_fresh_unscored_items apps/server/tests/test_sleep_agent.py::TestRestartCursor::test_consolidation_task_records_latest_runtime_message_cursor apps/server/tests/test_vault.py::test_export_and_import_vault_restores_knowledge_graph apps/server/tests/test_vault.py::test_capsule_sections_include_knowledge_graph_tables apps/server/tests/test_single_user_memory_baseline_probes.py` - 11 passed, 7 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test` - 1660 passed, 1 skipped, 242 warnings
- Changed paths:
  - apps/server/src/anima_server/services/agent/memory_store.py
  - apps/server/src/anima_server/services/agent/sleep_agent.py
  - apps/server/src/anima_server/services/vault.py
  - apps/server/tests/test_bm25_index.py
  - apps/server/tests/test_memory_scored_retrieval.py
  - apps/server/tests/test_single_user_memory_baseline_probes.py
  - apps/server/tests/test_sleep_agent.py
  - apps/server/tests/test_vault.py
  - docs/architecture/README.md
  - docs/architecture/memory/memory-system.md
  - docs/architecture/memory/single-user-temporal-memory-v2-baseline-audit.md
  - docs/prds/memory/README.md
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-001-baseline-memory-truth-audit.md
- Notes:
  - Full-suite warnings are existing SQLite test teardown warnings and one pytest assert-rewrite warning.
  - Codex review requested the fresh-unscored retrieval edge case and KG capsule test coverage; both are covered.
