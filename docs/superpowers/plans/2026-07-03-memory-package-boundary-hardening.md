# Memory Package Boundary Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the minimal `anima_server.services.memory` facade into the durable ownership boundary for memory contracts, orchestration, retrieval planning, and background memory services.

**Architecture:** Keep SQLCipher soul storage canonical and keep existing `services.agent` production imports working during the migration. Move ownership behind `services.memory` in thin, testable slices: contracts first, facades next, then import cutover guarded by compatibility shims. Do not introduce a parallel versioned memory package name.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, SQLCipher SQLite, Rust `packages/anima-core`, pytest, ruff, Bun/Nx scripts.

---

## Scope

This plan follows SUM-011 and SUM-005. SUM-011 created the package boundary; SUM-005 added the retrieval router and Rust memory contract layer. This initiative hardens that boundary so future memory work has one stable import surface and less cognitive memory logic lives directly under `services.agent`.

Included:

- Move shared memory domain contracts and fixture tests into a clear `services.memory` ownership model.
- Add facade modules for temporal claims, temporal graph, retrieval planning, salience, pattern synthesis, foresight, and procedural memory.
- Keep existing `services.agent` paths as compatibility shims until imports are migrated safely.
- Add cross-language contract fixtures for Python and Rust.
- Add import-lint or test guards so new memory code uses `services.memory`.
- Update memory architecture docs with ownership rules.

Not included:

- Rewriting persistence models.
- Replacing SQLCipher.
- Introducing mandatory Graphiti, Weaviate, Neo4j, Redis, Qdrant, LanceDB, or hosted services.
- Moving all existing large implementation files in one PR.
- Changing runtime behavior without focused regression coverage.

## Dependencies

- PR #67 branch contains `apps/server/src/anima_server/services/memory/`.
- SUM-005 retrieval router and `packages/anima-core/src/memory_contract.rs` exist.
- Existing backend tests can run with `ANIMA_CORE_REQUIRE_ENCRYPTION=false`.

## File Map

| Area | Files |
| --- | --- |
| Python memory boundary | `apps/server/src/anima_server/services/memory/*.py` |
| Existing agent implementations | `apps/server/src/anima_server/services/agent/*.py` |
| Rust contract | `packages/anima-core/src/memory_contract.rs`, `packages/anima-core/tests/memory_contract.rs` |
| Tests | `apps/server/tests/test_memory_*boundary*.py`, new `apps/server/tests/test_memory_package_*.py` |
| Docs | `docs/architecture/memory/memory-system.md`, this plan |
| Tickets | `tickets/memory-package-boundary/` |

## Boundary Rules

- `services.memory` owns stable memory contracts and orchestration-facing APIs.
- `services.agent` may keep heavy legacy implementations while migration is active.
- Compatibility shims may re-export from `services.memory` back into `services.agent`, but new memory code should import from `services.memory`.
- Rust contracts are additive and serialized with snake_case values.
- Query-plan traces may expose route weights and evidence IDs, but must not call routing weights retrieval scores.
- No new module path may use a parallel versioned memory package name.

## Execution Order

### Task 1: Boundary Inventory And Import Rules

**Files:**
- Create: `apps/server/tests/test_memory_package_import_rules.py`
- Modify: `docs/architecture/memory/memory-system.md`
- Modify: `tickets/memory-package-boundary/MPB-001-boundary-inventory-import-rules.md`

- [ ] Write a failing test that scans memory-adjacent modules and identifies imports that should move behind `services.memory`.
- [ ] Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_package_import_rules.py -q`
- [ ] Implement an allowlisted inventory helper in the test only; do not change production imports yet.
- [ ] Document the ownership rules in `docs/architecture/memory/memory-system.md`.
- [ ] Record changed paths and validation in MPB-001.
- [ ] Commit: `memory: document package boundary import rules`

### Task 2: Shared Contract Fixture Parity

**Files:**
- Create: `apps/server/tests/fixtures/memory_contract/*.json`
- Modify: `apps/server/tests/test_memory_package_boundary.py`
- Modify: `packages/anima-core/tests/memory_contract.rs`
- Modify: `packages/anima-core/src/memory_contract.rs`
- Modify: `tickets/memory-package-boundary/MPB-002-contract-fixture-parity.md`

- [ ] Add JSON fixtures for temporal fact, temporal relationship, salience, and recall trace payloads.
- [ ] Add Python tests that parse fixtures through `services.memory.domain` and `services.memory.retrieval_router`.
- [ ] Add Rust tests that parse the same fixtures through `anima_core::memory_contract`.
- [ ] Tighten Rust/Python field names only where fixture failures prove drift.
- [ ] Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_package_boundary.py`
- [ ] Run: `cargo test -p anima-core memory_contract`
- [ ] Record validation in MPB-002.
- [ ] Commit: `memory: add package contract parity fixtures`

### Task 3: Temporal Claims And Graph Facades

**Files:**
- Create: `apps/server/src/anima_server/services/memory/temporal_claims.py`
- Create: `apps/server/src/anima_server/services/memory/temporal_graph.py`
- Create: `apps/server/tests/test_memory_temporal_boundary.py`
- Modify: `apps/server/src/anima_server/services/memory/__init__.py`
- Modify: `tickets/memory-package-boundary/MPB-003-temporal-claims-graph-facades.md`

- [ ] Write tests for `current_fact`, `fact_history`, `valid_at`, `current_relationship`, and `relationship_history` facade functions using existing model/service helpers.
- [ ] Run the tests and confirm missing facade failures.
- [ ] Implement facades as thin wrappers over existing claims/KG logic.
- [ ] Keep existing `services.agent.claims` and `services.agent.knowledge_graph` imports working.
- [ ] Run related claim/KG tests plus the new boundary test.
- [ ] Record validation in MPB-003.
- [ ] Commit: `memory: add temporal claim and graph facades`

### Task 4: Retrieval Plan Execution Boundary

**Files:**
- Modify: `apps/server/src/anima_server/services/memory/retrieval_router.py`
- Modify: `apps/server/src/anima_server/services/memory/retrieval.py`
- Create: `apps/server/tests/test_memory_retrieval_plan_boundary.py`
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Modify: `tickets/memory-package-boundary/MPB-004-retrieval-plan-execution-boundary.md`

- [ ] Add tests that convert a query plan into execution lanes without changing result behavior.
- [ ] Add a typed execution adapter that records lane reasons, mode mapping, and evidence IDs returned from the existing wide-evidence path.
- [ ] Keep `search_long_memory` backward compatible with explicit mode values.
- [ ] Ensure traces distinguish route weights from retrieval scores.
- [ ] Run router/tool tests and focused search-long-memory tests.
- [ ] Record validation in MPB-004.
- [ ] Commit: `memory: add retrieval plan execution boundary`

### Task 5: Background Memory Service Facades

**Files:**
- Create: `apps/server/src/anima_server/services/memory/patterns.py`
- Create: `apps/server/src/anima_server/services/memory/foresight.py`
- Create: `apps/server/src/anima_server/services/memory/procedural.py`
- Create: `apps/server/tests/test_memory_background_facades.py`
- Modify: `apps/server/src/anima_server/services/memory/__init__.py`
- Modify: `tickets/memory-package-boundary/MPB-005-background-memory-facades.md`

- [ ] Add tests proving pattern synthesis, foresight, and procedural service facades delegate to current implementations.
- [ ] Implement thin facade modules that expose stable input/output contracts.
- [ ] Keep sleep-time orchestration imports unchanged until the compatibility ticket.
- [ ] Run focused pattern/foresight/procedural tests.
- [ ] Record validation in MPB-005.
- [ ] Commit: `memory: expose background memory facades`

### Task 6: Compatibility Shims And Import Migration

**Files:**
- Modify: selected `apps/server/src/anima_server/services/agent/*.py`
- Modify: selected `apps/server/src/anima_server/services/memory/*.py`
- Modify: `apps/server/tests/test_memory_package_import_rules.py`
- Modify: `tickets/memory-package-boundary/MPB-006-compatibility-shims-import-migration.md`

- [ ] Move safe imports from `services.agent` to `services.memory` one subsystem at a time.
- [ ] Keep compatibility shims for existing public imports.
- [ ] Add import-rule tests that fail on new direct imports into implementation modules where a memory facade exists.
- [ ] Run the focused suites for every migrated subsystem.
- [ ] Run `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server`.
- [ ] Record validation in MPB-006.
- [ ] Commit: `memory: migrate imports to package boundary`

### Task 7: Docs, Eval, And Cutover Checklist

**Files:**
- Modify: `docs/architecture/memory/memory-system.md`
- Create: `docs/architecture/memory/memory-package-boundary.md`
- Create: `apps/server/tests/test_memory_package_cutover.py`
- Modify: `tickets/memory-package-boundary/MPB-007-docs-eval-cutover.md`
- Modify: `tickets/memory-package-boundary/MPB-000-parent.md`

- [ ] Document the final boundary: owned modules, compatibility shims, and what still intentionally lives under `services.agent`.
- [ ] Add a cutover smoke test that imports the public memory package surface.
- [ ] Run all package-boundary tests.
- [ ] Run `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_package_boundary.py apps/server/tests/test_memory_package_import_rules.py apps/server/tests/test_memory_package_cutover.py`.
- [ ] Run `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server`.
- [ ] Run `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build:server`.
- [ ] Mark MPB-000 done only if all child tickets are done.
- [ ] Commit: `docs: finalize memory package boundary`

## Verification Commands

Run these before marking the initiative complete:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server apps/server/tests/test_memory_package_boundary.py apps/server/tests/test_memory_retrieval_router.py
cargo test -p anima-core memory_contract
cargo test -p anima-core
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run lint:server
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run build:server
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run db:server:current
git diff --check
```

## Risks

| Risk | Mitigation |
| --- | --- |
| Broad import churn causes regressions | Use compatibility shims and move one subsystem per ticket |
| Boundary becomes a second implementation | Facades delegate first; implementation movement requires tests |
| Route traces overclaim precision | Use route weights for planner output and retrieval scores only for actual results |
| Rust/Python contract drift | Shared fixtures parse on both sides |
| PR grows too large | Commit per ticket and keep compatibility intact |

## Completion Definition

The initiative is complete when `services.memory` is the documented memory API surface for new code, existing `services.agent` imports still work, shared contract fixtures pass in Python and Rust, and docs/tickets clearly state what remains intentionally legacy.
