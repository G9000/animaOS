# SUM-010 - Optional external adapter seams

- Status: done
- Priority: P3
- Scope: `apps/server/src/anima_server/services/agent`, `docs/architecture/memory`
- Parent: `SUM-000`
- Depends on: `SUM-003`, `SUM-005`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-03 15:08 MYT
- Started: 2026-07-03 15:04 MYT
- Completed: 2026-07-03 15:08 MYT

## Goal

Create optional adapter seams for external retrieval or graph engines without making any external system canonical or mandatory.

## Deliverables

- Retrieval backend interface with native implementation as the reference.
- Rebuild contract from SQLCipher canonical memory into derived indexes.
- Documentation for optional Weaviate/Qdrant/LanceDB-style vector adapters.
- Deferred graph backend interface only after native temporal KG semantics are stable.

## Acceptance

- Native backend remains the default.
- External indexes can be dropped and rebuilt from canonical storage.
- No external service is required for normal local use.
- Adapter contract tests pass against the native backend.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-07-03 15:04 MYT - Claimed by Codex on branch `codex/sum-010-optional-external-adapter-seams`, based on PR #67 head `codex/sum-001-memory-baseline`.
- 2026-07-03 15:08 MYT - Completed optional retrieval adapter seam with native backend contract, canonical rebuild path, adapter docs, and focused validation.

## Validation

- Commands:
  - Baseline: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_retrieval_rebuild.py` - 6 passed, 6 warnings before changes.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_retrieval_rebuild.py::test_memory_rebuild_contract_uses_only_active_canonical_rows` - failed during collection with `ModuleNotFoundError: No module named 'anima_server.services.agent.retrieval_backends'`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_retrieval_rebuild.py` - 8 passed, 7 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_bm25_index.py apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_uses_rust_memory_index_for_keyword_leg apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_semantic_search_uses_rust_memory_index_when_available apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_semantic_search_falls_back_when_rust_vector_search_has_no_hits apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_rebuilds_missing_rust_memory_index_for_keyword_leg apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_semantic_search_rebuilds_missing_rust_memory_index_from_canonical` - 25 passed, 5 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - passed.
  - `git diff --check` - passed.
  - `bun install --frozen-lockfile` - installed fresh worktree Node dependencies without lockfile changes after the first build attempt showed missing desktop packages.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - FastAPI `GET /health` smoke returned `200 ok`.
- Changed paths:
  - apps/server/src/anima_server/services/agent/retrieval_backends.py
  - apps/server/src/anima_server/services/agent/memory_store.py
  - apps/server/src/anima_server/services/agent/bm25_index.py
  - apps/server/src/anima_server/services/agent/embeddings.py
  - apps/server/tests/test_memory_retrieval_rebuild.py
  - docs/architecture/memory/optional-external-adapters.md
  - docs/architecture/memory/memory-system.md
  - tickets/single-user-temporal-memory-v2/SUM-010-optional-external-adapter-seams.md
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
- Notes:
  - Native retrieval remains the default backend and still degrades to existing Python/pgvector fallback paths when unavailable.
  - External vector adapters are now documented as optional rebuildable indexes over `MemoryRetrievalDocument`; they cannot become canonical storage.
  - Graph backend seams remain deferred until native temporal KG semantics are stable enough for a separate contract.
