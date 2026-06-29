# SUM-004 - Structured user profile

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`, `apps/server/src/anima_server/api`
- Parent: `SUM-000`
- Depends on: `SUM-002`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-30 05:47 MYT
- Started: 2026-06-30 05:34 MYT
- Completed: 2026-06-30 05:47 MYT

## Goal

Create an evidence-backed structured user profile that can be rendered compactly into the prompt and inspected or corrected through Open Mind surfaces.

## Deliverables

- Profile storage decision: new `UserProfileField` table or adapted `MemoryClaim` model.
- Typed categories for identity, relationships, work, preferences, goals, values, constraints, emotional patterns, and active projects.
- Consolidation extraction for profile updates.
- Sleep-time profile reconciliation.
- Compact profile prompt block.
- Inspection/correction API.

## Acceptance

- Profile fields are typed, confidence-scored, and evidence-linked.
- Profile prompt rendering is compact and deterministic.
- Correcting a profile field preserves audit history.
- Tests cover extraction parsing, reconciliation, rendering, and API behavior.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-06-30 05:34 MYT - Claimed by Codex on branch `codex/sum-004-structured-user-profile`, based on PR #70 head for `SUM-003`.
- 2026-06-30 05:47 MYT - Completed with encrypted structured profile tables, runtime profile update candidates, LLM extraction/promoter wiring, sleep-time claim reconciliation, compact prompt block, correction/retraction API, focused tests, migration validation, lint, build, full backend tests, and health smoke.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_agent_consolidation.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_memory_api.py` - 45 passed, 15 warnings.
  - `bun install` - installed workspace dependencies in the isolated worktree after the first full lint attempt showed missing desktop packages.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false ANIMA_DATABASE_URL=sqlite:///<temp> uv run --project apps/server alembic -c apps/server/alembic_core.ini upgrade head && uv run --project apps/server alembic -c apps/server/alembic_core.ini current` - core migration reached `20260630_0001 (head)`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - runtime migration check stamped a temp SQLite DB at `017_document_tables`, upgraded to `018_profile_update_candidates (head)`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_legacy_kg_migration_downgrade_tolerates_missing_constraints` - 1 passed after updating the SUM-003 downgrade test to target `20260626_0002` explicitly.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1709 passed, 1 skipped, 268 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - health smoke for `GET /health`: 200 ok.
- Changed paths:
  - apps/server/alembic_core/versions/20260630_0001_create_user_profile_fields.py
  - apps/server/alembic_runtime/versions/018_profile_update_candidates.py
  - apps/server/src/anima_server/api/routes/consciousness.py
  - apps/server/src/anima_server/models/__init__.py
  - apps/server/src/anima_server/models/agent_runtime.py
  - apps/server/src/anima_server/models/runtime_memory.py
  - apps/server/src/anima_server/services/agent/consolidation.py
  - apps/server/src/anima_server/services/agent/memory_blocks.py
  - apps/server/src/anima_server/services/agent/sleep_tasks.py
  - apps/server/src/anima_server/services/agent/soul_writer.py
  - apps/server/src/anima_server/services/agent/user_profile.py
  - apps/server/src/anima_server/services/data_crypto.py
  - apps/server/src/anima_server/services/agent/templates/prompts/memory_extraction.md.j2
  - apps/server/tests/test_agent_consolidation.py
  - apps/server/tests/test_runtime_db.py
  - apps/server/tests/test_user_profile.py
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-004-structured-user-profile.md
- Notes:
  - Full runtime SQLite migration from base is not a valid check in this repo because earlier runtime revision `005` executes PostgreSQL `CREATE EXTENSION vector`; SUM-004 runtime validation exercised only the new `018` revision from the existing runtime head.
