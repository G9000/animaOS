# SUM-000 - Single-User Temporal Memory v2 Parent Tracker

- Status: in_progress
- Priority: P1
- Scope: `apps/server`, `docs/prds/memory`, `docs/architecture/memory`, `tickets/single-user-temporal-memory-v2`
- Depends on: none
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-29 03:32 MYT
- Started: 2026-06-29 02:30 MYT
- Completed:

## Goal

Track the single-user temporal memory v2 initiative from baseline audit through evidence, temporal graph, profile, retrieval routing, salience, pattern synthesis, foresight, procedural learning, and optional adapter seams.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `SUM-001` | Baseline memory truth audit and eval probes | `done` | none |
| `SUM-002` | Evidence baseline and episode quality | `backlog` | `SUM-001` |
| `SUM-003` | Temporal knowledge graph v2 | `backlog` | `SUM-002` |
| `SUM-004` | Structured user profile | `backlog` | `SUM-002` |
| `SUM-005` | Retrieval router and query plans | `backlog` | `SUM-003`, `SUM-004` |
| `SUM-006` | Salience-aware decay and soft evolution | `backlog` | `SUM-003`, `SUM-004` |
| `SUM-007` | Cross-episode pattern synthesis | `backlog` | `SUM-005`, `SUM-006` |
| `SUM-008` | Foresight signals | `backlog` | `SUM-002` |
| `SUM-009` | Procedural experience and skill memory | `backlog` | `SUM-005` |
| `SUM-010` | Optional external adapter seams | `backlog` | `SUM-003`, `SUM-005` |

## Deliverables

- A truth baseline for the live memory system.
- Evidence-backed durable memory semantics.
- Temporal knowledge graph relation lifecycle.
- Structured evidence-backed user profile.
- Intent-specific retrieval query plans.
- Salience-aware decay and evolution handling.
- Cross-episode pattern synthesis.
- Foresight signal extraction and lifecycle.
- Procedural experience extraction, clustering, and skill distillation.
- Optional external adapter seams that preserve SQLCipher as canonical storage.

## Acceptance

- Every child ticket references this parent.
- Parent status table reflects child progress.
- Each child ticket records validation and changed paths.
- Multi-user/group memory remains out of scope unless explicitly reauthorized.
- No external memory engine becomes mandatory.

## Completed Tickets

- `SUM-001` - Baseline memory truth audit and eval probes (completed 2026-06-29 03:32 MYT)

## Activity Log

- 2026-06-27 12:40 MYT - Parent tracker created for single-user temporal memory v2 planning.
- 2026-06-29 02:30 MYT - `SUM-001` claimed by Codex on branch `codex/sum-001-memory-baseline`.
- 2026-06-29 03:12 MYT - `SUM-001` completed with baseline audit, deterministic recall probes, focused fixes, and validation.
- 2026-06-29 03:32 MYT - `SUM-001` updated after Codex review fixes and validation rerun.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_bm25_index.py::TestRustBackedKeywordSearch::test_bm25_search_uses_rust_memory_index_when_clean apps/server/tests/test_memory_scored_retrieval.py::test_scored_retrieval_pool_keeps_hot_older_items apps/server/tests/test_memory_scored_retrieval.py::test_scored_retrieval_pool_keeps_fresh_unscored_items apps/server/tests/test_sleep_agent.py::TestRestartCursor::test_consolidation_task_records_latest_runtime_message_cursor apps/server/tests/test_vault.py::test_export_and_import_vault_restores_knowledge_graph apps/server/tests/test_vault.py::test_capsule_sections_include_knowledge_graph_tables apps/server/tests/test_single_user_memory_baseline_probes.py` - 11 passed, 7 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test` - 1660 passed, 1 skipped, 242 warnings
- Changed paths:
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
- Notes:
  - Parent remains `in_progress` while later child tickets are still backlog.
