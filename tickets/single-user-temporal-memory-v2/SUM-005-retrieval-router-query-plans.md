# SUM-005 - Retrieval router and query plans

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/memory`, `apps/server/src/anima_server/services/agent`, `packages/anima-core`
- Parent: `SUM-000`
- Depends on: `SUM-003`, `SUM-004`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-03 16:41 MYT
- Started: 2026-07-03 16:28 MYT
- Completed: 2026-07-03 16:28 MYT

## Goal

Route memory retrieval by user intent instead of using one generic scoring strategy for every turn.

## Deliverables

- `retrieval_router.py` with deterministic route labels and query plan objects.
- Source-specific query-plan lane composition for profile, graph, memory items, episodes, transcripts, foresight, experiences, and skills.
- Trace output showing chosen route, lanes, route weights, evidence ids when present, and route reasons.
- Prompt/tool guidance updates for `search_long_memory`.
- Regression probes for route correctness.

## Acceptance

- Router fixture suite reaches agreed accuracy on representative user turns.
- Emotional support queries plan relationship and emotional-context lanes.
- Factual recall queries plan exact-claim, graph, memory-item, and episodic lanes.
- Project continuity queries plan active project/profile/episode, foresight, and experience lanes.
- Retrieval traces are serializable for UI/debug inspection.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-07-03 16:28 MYT - Claimed and completed in the detached PR #67 worktree with a deterministic memory retrieval router, `search_long_memory` auto-mode routing, router regression probes, and additive `anima-core` shared memory contract structs.
- 2026-07-03 16:37 MYT - Addressed code-quality review feedback for non-string `search_long_memory` modes, mixed-intent classifier precedence, truthful route trace metadata, tighter Rust salience enums, and ticket overclaim cleanup.
- 2026-07-03 16:41 MYT - Controller reran focused tests, Rust package tests, server lint/build, Alembic current, diff check, and a temporary-data health smoke before commit.

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_retrieval_router.py apps/server/tests/test_memory_package_boundary.py` - failed before implementation with `ModuleNotFoundError: No module named 'anima_server.services.memory.retrieval_router'`.
  - RED: `cargo test -p anima-core memory_contract` - failed before implementation with unresolved import `anima_core::memory_contract`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_retrieval_router.py apps/server/tests/test_memory_package_boundary.py` - 10 passed.
  - `cargo test -p anima-core memory_contract` - 3 passed; existing crate warnings remain.
  - `rustfmt --edition 2021 --check packages/anima-core/src/memory_contract.rs packages/anima-core/tests/memory_contract.rs` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build:server` - passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_retrieval_router.py apps/server/tests/test_memory_package_boundary.py` - review regressions failed before fixes on mixed-intent precedence, `RetrievalTraceItem(route_weight=...)`, and `mode=None`/`mode=123` handling.
  - RED: `cargo test -p anima-core memory_contract` - review regression failed before fixes because salience enums and fields were missing.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_retrieval_router.py apps/server/tests/test_memory_package_boundary.py` - review fix suite: 13 passed.
  - `cargo test -p anima-core memory_contract` - review fix suite: 4 passed; existing crate warnings remain.
  - `rustfmt --edition 2021 --check packages/anima-core/src/memory_contract.rs packages/anima-core/tests/memory_contract.rs` - controller rerun passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - controller rerun passed.
  - `git diff --check` - controller rerun passed with Git CRLF working-copy notices only.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build:server` - controller rerun passed.
  - `cargo test -p anima-core` - controller rerun passed: 192 unit tests, 4 memory contract integration tests, and 21 retrieval index integration tests passed; existing crate warnings remain.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run db:server:current` - controller rerun passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false` temporary-data FastAPI `GET /health` smoke - controller rerun returned 200 ok.
- Changed paths:
  - apps/server/src/anima_server/services/agent/tools.py
  - apps/server/src/anima_server/services/memory/__init__.py
  - apps/server/src/anima_server/services/memory/retrieval.py
  - apps/server/src/anima_server/services/memory/retrieval_router.py
  - apps/server/tests/test_memory_package_boundary.py
  - apps/server/tests/test_memory_retrieval_router.py
  - packages/anima-core/src/lib.rs
  - packages/anima-core/src/memory_contract.rs
  - packages/anima-core/tests/memory_contract.rs
  - tickets/single-user-temporal-memory-v2/SUM-005-retrieval-router-query-plans.md
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
- Notes:
  - Router is rule-first and deterministic; it does not add prompt plumbing or a mandatory external memory service.
  - This implements query-plan lane composition and auto-mode mapping into the existing wide evidence modes: aggregate, latest_update, temporal, and preference.
  - It does not execute independent per-lane retrievers yet; the existing wide evidence retrieval path remains the execution layer.
  - Shared Rust parity is an additive contract layer and does not rewrite existing `cards.rs` or `graph.rs`.
