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
- Updated: 2026-06-30 02:54 MYT
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
- 2026-06-29 23:55 MYT - Addressed PR #70 Codex review feedback for exact-name entity type drift and re-adding superseded relation triples without corrupting temporal history.
- 2026-06-30 00:12 MYT - Addressed PR #70 Codex rereview feedback by filtering superseded relations out of current public graph API endpoints.
- 2026-06-30 00:38 MYT - Addressed PR #70 Codex rereview feedback by repairing current KG columns on legacy DBs with old KG tables and no Alembic version.
- 2026-06-30 02:15 MYT - Addressed PR #70 Codex rereview feedback by preserving relation confidence on duplicate upserts when omitted and resolving graph reads/search from entity aliases.
- 2026-06-30 02:33 MYT - Addressed PR #70 Codex rereview feedback by resolving graph-context entity extraction from aliases when semantic fallback is disabled.
- 2026-06-30 02:54 MYT - Addressed PR #70 Codex rereview feedback by resolving stale-pruning turn entities through aliases and relation endpoints.

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
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertEntity::test_upsert_same_exact_name_tolerates_type_drift apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_readding_superseded_relation_creates_new_interval` - failed before fix with entity unique constraint violation and superseded relation row reuse.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertEntity::test_upsert_same_exact_name_tolerates_type_drift apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_readding_superseded_relation_creates_new_interval` - PR #70 review regressions: 2 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - PR #70 review suite: 67 passed, 29 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_creation_flow.py::test_agent_can_generate_thinking_monologue_draft -q` - isolated rerun of an order-dependent full-suite failure: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #70 review fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #70 review fix build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - PR #70 review fix full backend suite: first run failed on order-dependent `test_agent_can_generate_thinking_monologue_draft`, isolated rerun passed, longer rerun passed: 1689 passed, 1 skipped, 255 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - PR #70 review fix health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_graph_api.py::test_graph_current_endpoints_filter_superseded_relations` - failed before fix because `/api/graph/{user_id}/entities/{id}` returned the superseded `Acme` edge.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_graph_api.py::test_graph_current_endpoints_filter_superseded_relations` - PR #70 rereview graph API regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - PR #70 rereview graph/API suite: 68 passed, 29 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #70 rereview fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #70 rereview fix build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - PR #70 rereview fix full backend suite: 1690 passed, 1 skipped, 255 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - PR #70 rereview fix health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py::test_legacy_soul_database_migration_repairs_existing_kg_columns` - failed before fix because legacy KG tables were missing current mapped columns.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py::test_legacy_soul_database_migration_repairs_existing_kg_columns` - PR #70 legacy KG repair regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - PR #70 legacy KG repair suite: 93 passed, 29 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #70 legacy KG repair lint: passed after import-order cleanup.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #70 legacy KG repair build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - PR #70 legacy KG repair full backend suite: 1691 passed, 1 skipped, 255 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - PR #70 legacy KG repair health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_duplicate_relation_without_confidence_preserves_existing_confidence apps/server/tests/test_knowledge_graph.py::TestSearchGraphDepth1::test_resolves_start_entity_by_alias apps/server/tests/test_graph_api.py::test_graph_search_resolves_entity_aliases` - failed before fix because duplicate relation upsert overwrote stored confidence and alias graph search returned no results.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_duplicate_relation_without_confidence_preserves_existing_confidence apps/server/tests/test_knowledge_graph.py::TestSearchGraphDepth1::test_resolves_start_entity_by_alias apps/server/tests/test_graph_api.py::test_graph_search_resolves_entity_aliases` - PR #70 alias/confidence regressions: 3 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - PR #70 alias/confidence suite: 96 passed, 31 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #70 alias/confidence lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #70 alias/confidence build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_dashboard_api.py::test_proactive_notice_uses_saved_custom_instruction -q` - isolated rerun of an order-dependent full-suite failure: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - PR #70 alias/confidence full backend suite: first run failed on order-dependent `test_proactive_notice_uses_saved_custom_instruction`, isolated rerun passed, longer rerun passed: 1694 passed, 1 skipped, 257 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - PR #70 alias/confidence health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestGraphContextForQuery::test_resolves_alias_when_blocking_embeddings_disabled` - failed before fix because alias-only graph context returned no lines when semantic fallback was disabled.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestGraphContextForQuery::test_resolves_alias_when_blocking_embeddings_disabled` - PR #70 alias-context regression: 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - PR #70 alias-context suite: 97 passed, 32 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #70 alias-context lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #70 alias-context build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - PR #70 alias-context full backend suite: 1695 passed, 1 skipped, 258 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - PR #70 alias-context health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestIngestConversationGraphRules::test_pruning_resolves_alias_subject_entities` - failed before fix because alias-only turn subjects excluded canonical stale edges from pruning candidates.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestIngestConversationGraphRules::test_pruning_resolves_alias_subject_entities` - PR #70 alias-pruning regression: 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - PR #70 alias-pruning suite: 98 passed, 33 warnings.
  - `bun run lint` - PR #70 alias-pruning lint: passed.
  - `bun run build` - PR #70 alias-pruning build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - PR #70 alias-pruning full backend suite: 1696 passed, 1 skipped, 259 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - PR #70 alias-pruning health smoke for `GET /health`: 200 ok.
- Changed paths:
  - apps/server/alembic_core/versions/dbbe99c1da3a_temporal_knowledge_graph_v2.py
  - apps/server/src/anima_server/api/routes/graph.py
  - apps/server/src/anima_server/db/session.py
  - apps/server/src/anima_server/models/agent_runtime.py
  - apps/server/src/anima_server/services/agent/knowledge_graph.py
  - apps/server/src/anima_server/services/vault.py
  - apps/server/tests/test_graph_api.py
  - apps/server/tests/test_knowledge_graph.py
  - apps/server/tests/test_runtime_db.py
  - apps/server/tests/test_vault.py
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-003-temporal-knowledge-graph-v2.md
- Notes:
  - Temporal KG relations now preserve observed/valid time, confidence, lifecycle status, evidence references, and evolution links.
  - Existing graph traversal remains compatible by returning active relations by default; relationship history and latest-belief helpers expose superseded history.
  - The core migration guards stamped legacy soul databases that are missing KG tables; `Base.metadata.create_all()` still repairs those databases after Alembic completes.
  - PR #70 review fix keeps exact normalized-name entity upserts on the existing row when extractor type labels drift.
  - PR #70 review fix only reuses active relation rows so re-observed superseded triples create new intervals instead of mutating historical facts.
  - PR #70 rereview fix treats `/api/graph/{user_id}/overview`, `/entities/{id}`, and `/relations` as current-graph endpoints by filtering to active relations.
  - PR #70 legacy repair fix adds current KG columns and indexes to old KG tables in the legacy stamp path before mapped KG operations run.
  - PR #70 alias/confidence fix preserves existing relation confidence when duplicate upserts omit confidence.
  - PR #70 alias/confidence fix resolves graph traversal and public graph search from entity aliases as well as canonical names.
  - PR #70 alias-context fix resolves graph-context query entity extraction from aliases when semantic fallback is disabled.
  - PR #70 alias-pruning fix resolves stale-pruning turn entities from aliases and relation endpoints so canonical stale edges are considered when a turn uses an alias-only subject.
