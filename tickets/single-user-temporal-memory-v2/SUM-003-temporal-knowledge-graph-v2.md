# SUM-003 - Temporal knowledge graph v2

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-002`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-29 22:53 MYT
- Started: 2026-06-29 22:27 MYT
- Completed: 2026-06-29 22:53 MYT

## Goal

Upgrade the existing knowledge graph into a temporal, evidence-backed graph suitable for evolving relationships and preferences.

## Deliverables

- Temporal relation fields and migration.
- Evidence linkage for graph relations.
- Alias and embedding-based entity deduplication.
- Evolution chain semantics for soft changes.
- Graph retrieval helpers for relationship history and latest belief resolution.
- Export/import coverage for KG state.

## Acceptance

- Relation lifecycle tests cover observed time, valid time, supersession, and evolution.
- Graph retrieval can return both current belief and supporting history.
- KG export/import preserves entities, relations, temporal fields, and evidence references.
- Existing graph behavior remains compatible.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-06-29 22:27 MYT - Claimed by Codex on branch `codex/sum-003-temporal-kg-v2`, based on PR #68 branch `codex/sum-002-evidence-episode-quality`.
- 2026-06-29 22:53 MYT - Completed temporal KG v2 schema, relation lifecycle helpers, alias/embedding entity deduplication, KG vault portability, migration guard, validation, and health smoke.

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::test_temporal_knowledge_graph_model_metadata apps/server/tests/test_knowledge_graph.py::TestUpsertEntity::test_upsert_entity_deduplicates_aliases_and_similar_embeddings apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_upsert_relation_records_temporal_evidence_fields apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_relation_evolution_preserves_history_and_resolves_latest_belief apps/server/tests/test_vault.py::test_export_and_import_vault_restores_knowledge_graph` - failed before implementation because `get_relation_history` was missing.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - 65 passed, 27 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py::test_stamped_soul_database_migration_repairs_missing_new_tables` - 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server alembic -c apps/server/alembic_core.ini downgrade -1`; `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run db:server:upgrade`; `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run db:server:current` - downgrade/re-upgrade passed, current `dbbe99c1da3a (head)`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1687 passed, 1 skipped, 253 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` health smoke for `GET /health` - 200 ok.
- Changed paths:
  - apps/server/alembic_core/versions/dbbe99c1da3a_temporal_knowledge_graph_v2.py
  - apps/server/src/anima_server/models/agent_runtime.py
  - apps/server/src/anima_server/services/agent/knowledge_graph.py
  - apps/server/src/anima_server/services/vault.py
  - apps/server/tests/test_knowledge_graph.py
  - apps/server/tests/test_vault.py
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-003-temporal-knowledge-graph-v2.md
- Notes:
  - Temporal KG relations now preserve observed/valid time, confidence, lifecycle status, evidence references, and evolution links.
  - Existing graph traversal remains compatible by returning active relations by default; relationship history and latest-belief helpers expose superseded history.
  - The core migration guards stamped legacy soul databases that are missing KG tables; `Base.metadata.create_all()` still repairs those databases after Alembic completes.
