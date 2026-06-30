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
- Updated: 2026-06-30 17:21 MYT
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
- 2026-06-30 12:29 MYT - Addressed PR #71 review feedback by deduplicating claim reconciliation evidence, invalidating companion memory after sleep-time profile reconciliation, and tightening deterministic test fixtures uncovered by the full backend gate.
- 2026-06-30 12:42 MYT - Addressed additional PR #71 review feedback by setting profile source FKs to `ON DELETE SET NULL` in the Alembic revision and allowing profile update re-extraction after promoted candidates.
- 2026-06-30 12:54 MYT - Addressed additional PR #71 review feedback by moving claim evidence provenance into a claim-specific FK column and retracting profile fields sourced from memories during user-initiated forget.
- 2026-06-30 13:21 MYT - Addressed additional PR #71 review feedback by invalidating the companion memory cache after the forget endpoint commits profile-field retractions.
- 2026-06-30 13:35 MYT - Addressed additional PR #71 review feedback by hard-deleting profile fields/evidence tied to forgotten memories and adding structured profile tables to eval reset cleanup.
- 2026-06-30 13:58 MYT - Addressed additional PR #71 review feedback by preserving profile fields that still have surviving evidence after one source memory is forgotten.
- 2026-06-30 14:41 MYT - Addressed additional PR #71 review feedback by deleting runtime-message-only profile fields when their source memory is forgotten and setting the profile self-FK to `ON DELETE SET NULL` in the migration.
- 2026-06-30 16:50 MYT - Addressed additional PR #71 review feedback by narrowing runtime-message profile forget cleanup so unrelated profile fields from the same turn are preserved.
- 2026-06-30 17:03 MYT - Addressed additional PR #71 review feedback by preserving user-corrected profile fields from conflicting automatic updates and setting profile `user_id` FKs to `ON DELETE CASCADE` in the migration.
- 2026-06-30 17:21 MYT - Addressed additional PR #71 review feedback by rejecting pending profile update candidates tied to forgotten source memories and parsing profile-only LLM extraction responses.

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_reconcile_profile_from_claims_is_idempotent_for_same_claim apps/server/tests/test_user_profile.py::test_sleep_tasks_invalidates_companion_memory_after_profile_reconciliation` - PR #71 review regressions failed before fix because repeated reconciliation counted the same claim again and sleep reconciliation did not invalidate companion memory.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_reconcile_profile_from_claims_is_idempotent_for_same_claim apps/server/tests/test_user_profile.py::test_sleep_tasks_invalidates_companion_memory_after_profile_reconciliation` - 2 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_agent_consolidation.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_memory_blocks.py` - 44 passed, 17 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_passes_timestamp_names_and_preserves_details -q` - failed before clock-freezing fixed the aged-out 24-hour fixture, then 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_episodes.py -q` - 13 passed, 11 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_dashboard_api.py -q` - failed before proactive-notice custom-instruction tests forced scaffold mode, then 19 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1711 passed, 1 skipped, 270 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete apps/server/tests/test_user_profile.py::test_profile_update_candidate_can_be_reextracted_after_promotion -q` - PR #71 review regressions failed before fix because migration-created source FKs had no `ON DELETE SET NULL` action and promoted profile candidates blocked re-extraction.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete apps/server/tests/test_user_profile.py::test_profile_update_candidate_can_be_reextracted_after_promotion -q` - 2 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_agent_consolidation.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete -q` - 46 passed, 17 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1713 passed, 1 skipped, 270 warnings.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete apps/server/tests/test_user_profile.py::test_reconcile_profile_from_claims_tracks_claim_evidence_separately apps/server/tests/test_user_profile.py::test_forget_memory_deletes_profile_fields_sourced_from_claim_chain -q` - PR #71 review regressions failed before fix because claim evidence used the memory-item evidence FK and forget left source-derived profile fields active.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete apps/server/tests/test_user_profile.py::test_reconcile_profile_from_claims_tracks_claim_evidence_separately apps/server/tests/test_user_profile.py::test_forget_memory_deletes_profile_fields_sourced_from_claim_chain -q` - 3 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_memory_api.py apps/server/tests/test_agent_consolidation.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete -q` - 68 passed, 19 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1715 passed, 1 skipped, 272 warnings.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_api.py::test_forget_endpoint_invalidates_companion_after_profile_forget_cleanup -q` - PR #71 review regression failed before fix because the forget endpoint left the companion memory cache valid after cleaning sourced profile fields.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_api.py::test_forget_endpoint_invalidates_companion_after_profile_forget_cleanup -q` - 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_api.py apps/server/tests/test_user_profile.py::test_forget_memory_deletes_profile_fields_sourced_from_claim_chain -q` - 22 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_deletes_profile_fields_sourced_from_claim_chain -q` - PR #71 review regression failed before fix because include-history profile reads still returned forgotten encrypted profile content.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_eval_harness.py::test_reset_eval_user_state_purges_soul_and_runtime_rows -q` - PR #71 review regression failed before fix because eval reset skipped `user_profile_fields`, `user_profile_field_evidence`, and `profile_update_candidates`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_deletes_profile_fields_sourced_from_claim_chain -q` - 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_eval_harness.py::test_reset_eval_user_state_purges_soul_and_runtime_rows -q` - 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_memory_api.py apps/server/tests/test_eval_harness.py::test_reset_eval_user_state_purges_soul_and_runtime_rows -q` - 36 passed, 11 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_memory_api.py::test_forget_endpoint_invalidates_companion_after_profile_forget_cleanup -q` - 1 passed.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed after import-order cleanup.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1716 passed, 1 skipped, 272 warnings.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_preserves_profile_field_with_surviving_evidence -q` - PR #71 review regression failed before fix because forgetting one source memory deleted a profile field still supported by a second evidence row.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_preserves_profile_field_with_surviving_evidence apps/server/tests/test_user_profile.py::test_forget_memory_deletes_profile_fields_sourced_from_claim_chain -q` - 2 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_memory_api.py apps/server/tests/test_eval_harness.py::test_reset_eval_user_state_purges_soul_and_runtime_rows -q` - 37 passed, 12 warnings.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_agent_consolidation.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_memory_api.py` - 45 passed, 15 warnings.
  - `bun install` - installed workspace dependencies in the isolated worktree after the first full lint attempt showed missing desktop packages.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false ANIMA_DATABASE_URL=sqlite:///<temp> uv run --project apps/server alembic -c apps/server/alembic_core.ini upgrade head && uv run --project apps/server alembic -c apps/server/alembic_core.ini current` - core migration reached `20260630_0001 (head)`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - runtime migration check stamped a temp SQLite DB at `017_document_tables`, upgraded to `018_profile_update_candidates (head)`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_legacy_kg_migration_downgrade_tolerates_missing_constraints` - 1 passed after updating the SUM-003 downgrade test to target `20260626_0002` explicitly.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1709 passed, 1 skipped, 268 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_deletes_profile_field_sourced_by_runtime_message -q` - PR #71 review regression failed before fix because a runtime-message-only profile field survived forgetting its source memory.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete -q` - PR #71 review regression failed before fix because `superseded_by_id` had no migration-level `ON DELETE SET NULL`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_deletes_profile_field_sourced_by_runtime_message -q` - 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete -q` - 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_memory_api.py apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete -q` - 38 passed, 13 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_preserves_unrelated_profile_field_from_same_turn -q` - PR #71 review regression failed before fix because runtime-message fallback deleted an unrelated profile field from the same turn.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_preserves_unrelated_profile_field_from_same_turn apps/server/tests/test_user_profile.py::test_forget_memory_deletes_profile_field_sourced_by_runtime_message -q` - 2 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_memory_api.py apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete -q` - 39 passed, 14 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_upsert_profile_field_preserves_user_correction_from_auto_update -q` - PR #71 review regression failed before fix because automatic profile updates could supersede a user correction.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete -q` - PR #71 review regression failed before fix because profile `user_id` FKs had no migration-level `ON DELETE CASCADE`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_upsert_profile_field_preserves_user_correction_from_auto_update -q` - 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete -q` - 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_memory_api.py apps/server/tests/test_runtime_db.py::test_user_profile_migration_sets_source_fks_null_on_delete -q` - 40 passed, 15 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_rejects_pending_profile_candidates_from_source_turn apps/server/tests/test_user_profile.py::test_llm_extraction_parses_profile_updates_without_memories -q` - PR #71 review regressions failed before fix because `forget_memory()` could not reject pending runtime profile candidates and profile-only extraction payloads were parsed as memories.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py::test_forget_memory_rejects_pending_profile_candidates_from_source_turn apps/server/tests/test_user_profile.py::test_llm_extraction_parses_profile_updates_without_memories -q` - 2 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_user_profile.py apps/server/tests/test_memory_api.py::test_forget_endpoint_invalidates_companion_after_profile_forget_cleanup apps/server/tests/test_agent_consolidation.py::test_run_background_extraction_stores_profile_update_candidates -q` - 22 passed, 16 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint -- --projects=server` - passed.
  - `git diff --check` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
- Changed paths:
  - apps/server/alembic_core/versions/20260630_0001_create_user_profile_fields.py
  - apps/server/alembic_runtime/versions/018_profile_update_candidates.py
  - apps/server/src/anima_server/api/routes/consciousness.py
  - apps/server/src/anima_server/api/routes/forgetting.py
  - apps/server/src/anima_server/models/__init__.py
  - apps/server/src/anima_server/models/agent_runtime.py
  - apps/server/src/anima_server/models/runtime_memory.py
  - apps/server/src/anima_server/services/agent/consolidation.py
  - apps/server/src/anima_server/services/agent/forgetting.py
  - apps/server/src/anima_server/services/agent/memory_blocks.py
  - apps/server/src/anima_server/services/agent/sleep_tasks.py
  - apps/server/src/anima_server/services/agent/soul_writer.py
  - apps/server/src/anima_server/services/agent/user_profile.py
  - apps/server/src/anima_server/services/data_crypto.py
  - apps/server/src/anima_server/services/agent/templates/prompts/memory_extraction.md.j2
  - apps/server/tests/test_agent_consolidation.py
  - apps/server/tests/test_agent_episodes.py
  - apps/server/tests/test_dashboard_api.py
  - apps/server/tests/test_runtime_db.py
  - apps/server/tests/test_user_profile.py
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-004-structured-user-profile.md
- Notes:
  - Full runtime SQLite migration from base is not a valid check in this repo because earlier runtime revision `005` executes PostgreSQL `CREATE EXTENSION vector`; SUM-004 runtime validation exercised only the new `018` revision from the existing runtime head.
