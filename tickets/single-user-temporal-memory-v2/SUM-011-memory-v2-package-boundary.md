# SUM-011 - Memory v2 package boundary

- Status: done
- Priority: P2
- Scope: `apps/server/src/anima_server/services/memory_v2`, `apps/server/tests`, `docs/architecture/memory`
- Parent: `SUM-000`
- Depends on: `SUM-001`, `SUM-003`, `SUM-006`, `SUM-010`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-07-03 15:50 MYT
- Updated: 2026-07-03 15:56 MYT
- Started: 2026-07-03 15:50 MYT
- Completed: 2026-07-03 15:56 MYT

## Goal

Create a minimal `services.memory_v2` package boundary for stable memory v2 contracts and compatibility-safe facades without moving the large existing implementations out of `services.agent`.

## Deliverables

- `memory_v2` package with stable domain/status/string contracts.
- Salience facade over the existing agent salience implementation.
- Retrieval facade over the existing agent retrieval backend implementation.
- Temporal helper module for normalized lifecycle status checks.
- Focused package-boundary test coverage.

## Acceptance

- Existing `services.agent` import paths continue to work unchanged.
- New `services.memory_v2` imports expose the intended stable boundary.
- Facade objects delegate to existing implementations rather than duplicating behavior.
- No heavy implementation extraction or broad production import churn is included.

## Activity Log

- 2026-07-03 15:50 MYT - Claimed by Codex in detached PR #67 worktree `sum-011-memory-v2-boundary-pr67`.
- 2026-07-03 15:50 MYT - Added memory v2 package boundary, compatibility facades, focused boundary test, and minimal architecture doc reference.
- 2026-07-03 15:56 MYT - Controller review aligned domain exports with memory terminology (`TemporalRecordStatus`, `MemoryEndpointKind`) and added `RecallScoreBreakdown`.

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_v2_package_boundary.py` - 4 failed with `ModuleNotFoundError: No module named 'anima_server.services.memory_v2'`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_v2_package_boundary.py` - 4 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - failed on unsorted `domain.__all__`.
  - `uv run --project apps/server ruff check apps/server/src/anima_server/services/memory_v2/domain.py --select RUF022 --fix` - fixed sorted `__all__`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_v2_package_boundary.py` - 4 passed after lint fix.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - passed.
  - `git diff --check` - passed with existing CRLF normalization warnings for edited markdown files.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_v2_package_boundary.py` - controller rerun after domain contract review: 4 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - controller rerun: passed.
  - `git diff --check` - controller rerun: passed with existing CRLF normalization warnings for edited markdown files.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build:server` - controller server build: passed.
- Changed paths:
  - apps/server/src/anima_server/services/memory_v2/__init__.py
  - apps/server/src/anima_server/services/memory_v2/domain.py
  - apps/server/src/anima_server/services/memory_v2/retrieval.py
  - apps/server/src/anima_server/services/memory_v2/salience.py
  - apps/server/src/anima_server/services/memory_v2/temporal.py
  - apps/server/tests/test_memory_v2_package_boundary.py
  - docs/architecture/memory/memory-system.md
  - tickets/single-user-temporal-memory-v2/SUM-011-memory-v2-package-boundary.md
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
- Notes:
  - This ticket intentionally does not move implementation files out of `services.agent`.
  - Existing `services.agent` production imports remain unchanged; new memory work can import contracts and facades from `services.memory_v2`.
