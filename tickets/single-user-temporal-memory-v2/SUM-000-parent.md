# SUM-000 - Single-User Temporal Memory v2 Parent Tracker

- Status: in_progress
- Priority: P1
- Scope: `apps/server`, `docs/prds/memory`, `docs/architecture/memory`, `tickets/single-user-temporal-memory-v2`
- Depends on: none
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-01 03:15 MYT
- Started: 2026-06-29 02:30 MYT
- Completed:

## Goal

Track the single-user temporal memory v2 initiative from baseline audit through evidence, temporal graph, profile, retrieval routing, salience, pattern synthesis, foresight, procedural learning, and optional adapter seams.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `SUM-001` | Baseline memory truth audit and eval probes | `done` | none |
| `SUM-002` | Evidence baseline and episode quality | `done` | `SUM-001` |
| `SUM-003` | Temporal knowledge graph v2 | `done` | `SUM-002` |
| `SUM-004` | Structured user profile | `done` | `SUM-002` |
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

- `SUM-001` - Baseline memory truth audit and eval probes (completed 2026-06-29 10:39 MYT)
- `SUM-002` - Evidence baseline and episode quality (completed 2026-06-29 11:03 MYT)
- `SUM-003` - Temporal knowledge graph v2 (completed 2026-06-29 22:53 MYT)
- `SUM-004` - Structured user profile (completed 2026-06-30 05:47 MYT)

## Activity Log

- 2026-06-27 12:40 MYT - Parent tracker created for single-user temporal memory v2 planning.
- 2026-06-29 02:30 MYT - `SUM-001` claimed by Codex on branch `codex/sum-001-memory-baseline`.
- 2026-06-29 03:12 MYT - `SUM-001` completed with baseline audit, deterministic recall probes, focused fixes, and validation.
- 2026-06-29 03:32 MYT - `SUM-001` updated after Codex review fixes and validation rerun.
- 2026-06-29 10:36 MYT - `SUM-002` claimed by Codex on branch `codex/sum-002-evidence-episode-quality`, based on PR #67 branch `codex/sum-001-memory-baseline`.
- 2026-06-29 10:39 MYT - `SUM-001` updated after Codex PR comment fix for KG sequence reset and validation rerun.
- 2026-06-29 11:03 MYT - `SUM-002` completed with evidence audit/backfill endpoints, episode timestamp/detail safeguards, focused validation, lint, build, and health smoke.
- 2026-06-29 11:13 MYT - `SUM-002` updated after review feedback to use multilingual-safe exact user detail excerpts.
- 2026-06-29 11:38 MYT - `SUM-002` expanded to production multilingual baseline across grounded episode details, lexical fallbacks, transcript search metadata, vector fallback scoring, and generic claim keys.
- 2026-06-29 12:32 MYT - `SUM-002` addressed PR #68 review threads and reran focused suite, lint, build, full backend tests, and health smoke.
- 2026-06-29 13:06 MYT - `SUM-002` addressed Codex rereview threads for per-turn relative date resolution and CJK unigram fallback search.
- 2026-06-29 17:01 MYT - `SUM-002` addressed Codex rereview thread for non-positive BM25 scores on ubiquitous CJK unigram hits.
- 2026-06-29 17:37 MYT - `SUM-002` addressed Codex rereview thread for one-character ASCII memory slot values in relation matching.
- 2026-06-29 18:08 MYT - `SUM-002` addressed Codex rereview threads for one-character ASCII fallback retrieval and long salient episode excerpt grounding.
- 2026-06-29 18:49 MYT - `SUM-002` addressed Codex rereview thread for one-character transcript query false positives.
- 2026-06-29 22:19 MYT - Resolved stacked PR merge conflict by preserving `SUM-001` base updates and `SUM-002` review validation history.
- 2026-06-29 22:27 MYT - `SUM-003` claimed by Codex on branch `codex/sum-003-temporal-kg-v2`, based on PR #68 branch `codex/sum-002-evidence-episode-quality`.
- 2026-06-29 22:53 MYT - `SUM-003` completed with temporal KG schema migration, evidence-backed relation lifecycle, alias/embedding entity deduplication, history/latest-belief retrieval helpers, vault portability, migration guard coverage, and full validation.
- 2026-06-29 23:55 MYT - `SUM-003` addressed PR #70 review feedback for exact-name entity type drift and superseded relation re-add intervals, then reran focused, KG/vault, lint, build, and full backend validation.
- 2026-06-30 00:12 MYT - `SUM-003` addressed PR #70 rereview feedback by filtering superseded relations out of current public graph API endpoints, then reran focused, KG/vault, lint, build, full backend validation, and health smoke.
- 2026-06-30 00:38 MYT - `SUM-003` addressed PR #70 rereview feedback by repairing current KG columns on legacy DBs with old KG tables and no Alembic version, then reran focused, KG/vault, runtime DB, lint, build, full backend validation, and health smoke.
- 2026-06-30 02:15 MYT - `SUM-003` addressed PR #70 rereview feedback by preserving relation confidence on duplicate upserts when omitted and resolving graph reads/search from entity aliases, then reran red/green regressions, related KG/API/runtime/vault suite, lint, build, full backend validation, and health smoke.
- 2026-06-30 02:33 MYT - `SUM-003` addressed PR #70 rereview feedback by resolving graph-context entity extraction from aliases when semantic fallback is disabled, then reran focused, related KG/API/runtime/vault suite, lint, build, full backend validation, and health smoke.
- 2026-06-30 02:54 MYT - `SUM-003` addressed PR #70 rereview feedback by resolving stale-pruning turn entities through aliases and relation endpoints, then reran focused, related KG/API/runtime/vault suite, lint, build, full backend validation, and health smoke.
- 2026-06-30 03:09 MYT - `SUM-003` addressed PR #70 rereview feedback by filtering stale-pruning candidates to active relations, then reran focused, related KG/API/runtime/vault suite, lint, build, full backend validation, and health smoke.
- 2026-06-30 03:32 MYT - `SUM-003` addressed PR #70 rereview feedback by guarding legacy downgrade FK drops when repaired KG tables lack constraints, then reran focused, related KG/API/runtime/vault suite, lint, build, full backend validation, and health smoke.
- 2026-06-30 03:49 MYT - `SUM-003` addressed PR #70 rereview feedback by preventing same-triple self-supersession from mutating the replacement row, then reran focused, related KG/API/runtime/vault suite, lint, build, full backend validation, and health smoke.
- 2026-06-30 05:34 MYT - `SUM-004` claimed by Codex on branch `codex/sum-004-structured-user-profile`, based on PR #70 head for `SUM-003`.
- 2026-06-30 05:47 MYT - `SUM-004` completed with structured profile storage, extraction and Soul Writer promotion, sleep-time claim reconciliation, profile prompt rendering, correction API, migrations, focused validation, lint, build, full backend tests, and health smoke.
- 2026-06-30 12:29 MYT - `SUM-004` addressed PR #71 review feedback for idempotent claim-profile reconciliation and companion memory invalidation after sleep-time profile updates, then reran focused, lint, build, and full backend validation.
- 2026-06-30 12:42 MYT - `SUM-004` addressed additional PR #71 feedback for profile source FK delete semantics and promoted-candidate re-extraction, then reran focused, lint, build, and full backend validation.
- 2026-06-30 12:54 MYT - `SUM-004` addressed additional PR #71 feedback for claim-specific profile provenance and profile retraction during user-initiated forget, then reran focused, lint, build, and full backend validation.
- 2026-06-30 13:21 MYT - `SUM-004` addressed additional PR #71 feedback for companion memory invalidation after user-initiated forget retracts sourced profile fields, then reran focused, lint, diff, and build validation.
- 2026-06-30 13:35 MYT - `SUM-004` addressed additional PR #71 feedback for hard-deleting forgotten profile evidence and clearing structured profile state during eval reset, then reran red/green regressions and related suites.
- 2026-06-30 13:58 MYT - `SUM-004` addressed additional PR #71 feedback for preserving profile fields with surviving evidence after a partial source forget, then reran red/green regressions and related suites.
- 2026-06-30 14:41 MYT - `SUM-004` addressed additional PR #71 feedback for runtime-message-linked profile forget cleanup and profile self-FK delete semantics, then reran red/green regressions, related suites, lint, diff, and build.
- 2026-06-30 16:50 MYT - `SUM-004` addressed additional PR #71 feedback for preserving unrelated same-turn profile fields during runtime-message forget cleanup, then reran red/green regressions, related suites, lint, diff, and build.
- 2026-06-30 17:03 MYT - `SUM-004` addressed additional PR #71 feedback for preserving manual profile corrections from automatic updates and cascading profile rows on user deletion, then reran red/green regressions, related suites, lint, diff, and build.
- 2026-06-30 17:21 MYT - `SUM-004` addressed additional PR #71 feedback for rejecting pending profile candidates after source-memory forget and accepting profile-only extraction payloads, then reran red/green regressions and related profile/API tests.
- 2026-06-30 17:35 MYT - `SUM-004` addressed additional PR #71 feedback for canonical profile keys and same-value user corrections, then reran red/green regressions and the user profile suite.
- 2026-06-30 18:00 MYT - `SUM-004` addressed additional PR #71 feedback for skipping unmapped fact claims and preserving forget behavior without a runtime DB, then reran red/green regressions and related profile/API tests.
- 2026-06-30 18:23 MYT - `SUM-004` addressed additional PR #71 feedback for profile observation bounds after partial forget and stricter same-turn runtime profile matching, then reran red/green regressions, related profile/API tests, lint, diff, and build.
- 2026-06-30 18:44 MYT - `SUM-004` addressed additional PR #71 feedback for vault round-tripping structured profile rows and sticky profile retractions, then reran red/green regressions, vault/profile suites, lint, diff, and build.
- 2026-07-01 00:54 MYT - `SUM-004` addressed additional PR #71 feedback for claim reconciliation counts and source-less claim dedupe, then reran red/green regressions, the user profile suite, lint, diff, and build.
- 2026-07-01 01:17 MYT - `SUM-004` addressed additional PR #71 feedback for vault restore ordering and missing claim-evidence FKs, then reran red/green regression, vault/profile suites, lint, diff, and build.
- 2026-07-01 01:35 MYT - `SUM-004` addressed additional PR #71 feedback for explicit profile-evidence deletion during forget cleanup, then reran red/green regression, related forget tests, user profile suite, and forget endpoint checks.
- 2026-07-01 01:53 MYT - `SUM-004` addressed additional PR #71 feedback for preserving newer non-claim profile updates during claim reconciliation, then reran red/green regression, reconciliation cluster, and the user profile suite.
- 2026-07-01 02:13 MYT - `SUM-004` addressed additional PR #71 feedback for returning 400 on blank profile correction payloads, then reran red/green regression, profile API checks, and the user profile suite.
- 2026-07-01 02:34 MYT - `SUM-004` addressed additional PR #71 feedback for preserving newer profile fields when retrying older failed profile candidates, then reran red/green regression, profile promotion checks, and the user profile suite.
- 2026-07-01 02:55 MYT - `SUM-004` addressed additional PR #71 feedback for single-token runtime-message profile forget cleanup and field-scoped sourceless claim reconciliation, then reran red/green regressions, related forget/reconciliation checks, the user profile suite, lint, diff, and build.
- 2026-07-01 03:15 MYT - `SUM-004` addressed additional PR #71 feedback for same-value profile observation timestamps and `user_profile` prompt-budget priority, then reran red/green regressions, profile/prompt-budget suites, lint, diff, and build.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_bm25_index.py::TestRustBackedKeywordSearch::test_bm25_search_uses_rust_memory_index_when_clean apps/server/tests/test_memory_scored_retrieval.py::test_scored_retrieval_pool_keeps_hot_older_items apps/server/tests/test_memory_scored_retrieval.py::test_scored_retrieval_pool_keeps_fresh_unscored_items apps/server/tests/test_sleep_agent.py::TestRestartCursor::test_consolidation_task_records_latest_runtime_message_cursor apps/server/tests/test_vault.py::test_export_and_import_vault_restores_knowledge_graph apps/server/tests/test_vault.py::test_capsule_sections_include_knowledge_graph_tables apps/server/tests/test_vault.py::test_reset_identity_sequences_includes_knowledge_graph_tables apps/server/tests/test_single_user_memory_baseline_probes.py` - 12 passed, 7 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test` - 1661 passed, 1 skipped, 242 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_api.py` - SUM-002 focused suite: 45 passed, 17 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_api.py apps/server/tests/test_phase3_storage.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py::TestTranscriptSearch` - expanded SUM-002 multilingual suite: 118 passed, 34 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_provenance_backfill.py::test_backfill_memory_item_evidence_prioritizes_active_missing_rows apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_does_not_append_ordinary_turns_without_salient_details apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_in_summary apps/server/tests/test_memory_multilingual_baseline.py::test_unicode_tokens_preserve_one_character_multilingual_and_digit_values apps/server/tests/test_memory_multilingual_baseline.py::test_bm25_fallback_handles_empty_tokenized_corpus` - PR review regressions: 5 passed, 3 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_provenance_backfill.py::test_backfill_memory_item_evidence_prioritizes_active_missing_rows apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_does_not_append_ordinary_turns_without_salient_details apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_in_summary apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_from_matching_turn apps/server/tests/test_memory_multilingual_baseline.py::test_unicode_tokens_preserve_one_character_multilingual_and_digit_values apps/server/tests/test_memory_multilingual_baseline.py::test_single_character_cjk_queries_match_longer_non_space_text apps/server/tests/test_memory_multilingual_baseline.py::test_bm25_fallback_handles_empty_tokenized_corpus` - PR rereview regressions: 7 passed, 4 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_provenance_backfill.py::test_backfill_memory_item_evidence_prioritizes_active_missing_rows apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_does_not_append_ordinary_turns_without_salient_details apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_in_summary apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_from_matching_turn apps/server/tests/test_memory_multilingual_baseline.py::test_unicode_tokens_preserve_one_character_multilingual_and_digit_values apps/server/tests/test_memory_multilingual_baseline.py::test_single_character_cjk_queries_match_longer_non_space_text apps/server/tests/test_memory_multilingual_baseline.py::test_bm25_fallback_handles_empty_tokenized_corpus apps/server/tests/test_memory_multilingual_baseline.py::test_bm25_fallback_ranks_cjk_unigram_by_overlap_when_bm25_scores_are_non_positive` - PR rereview regressions: 8 passed, 4 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py::test_memory_relation_preserves_one_character_ascii_slot_values` - PR rereview regression: failed before fix, then 1 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py::test_degraded_retrieval_preserves_one_character_ascii_identifiers apps/server/tests/test_agent_episodes.py::test_ground_salient_user_details_truncates_after_grounding_long_excerpt` - PR rereview regressions: failed before fix, then 2 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py::test_degraded_retrieval_preserves_one_character_ascii_identifiers` - PR rereview regression: failed before fix, then 1 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_api.py apps/server/tests/test_phase3_storage.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py::TestTranscriptSearch apps/server/tests/test_batch_segmenter.py` - expanded SUM-002 rereview suite: 148 passed, 41 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_api.py apps/server/tests/test_phase3_storage.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py::TestTranscriptSearch apps/server/tests/test_batch_segmenter.py` - expanded SUM-002 rereview suite: 149 passed, 41 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_memory_api.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py apps/server/tests/test_memory_retrieval_rebuild.py apps/server/tests/test_evidence_retrieval.py` - expanded SUM-002 rereview suite: 151 passed, 28 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_p5_transcript_archive.py` - focused SUM-002 rereview suite: 109 passed, 11 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py` - focused SUM-002 rereview suite: 96 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_memory_api.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py apps/server/tests/test_memory_retrieval_rebuild.py apps/server/tests/test_evidence_retrieval.py` - expanded SUM-002 rereview suite: 153 passed, 28 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` smoke for `GET /health` - 200 ok
  - `bun run lint` - passed
  - `bun run build` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-002 run: 1678 passed, 1 skipped, 250 warnings after isolated rerun of an order-dependent dashboard scaffold mismatch passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-002 run: 1679 passed, 1 skipped, 250 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-002 run: first attempt timed out at 5 minutes, rerun with longer timeout passed: 1680 passed, 1 skipped, 250 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-002 run: 1682 passed, 1 skipped, 250 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-002 run: 1682 passed, 1 skipped, 250 warnings
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::test_temporal_knowledge_graph_model_metadata apps/server/tests/test_knowledge_graph.py::TestUpsertEntity::test_upsert_entity_deduplicates_aliases_and_similar_embeddings apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_upsert_relation_records_temporal_evidence_fields apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_relation_evolution_preserves_history_and_resolves_latest_belief apps/server/tests/test_vault.py::test_export_and_import_vault_restores_knowledge_graph` - SUM-003 red test failed before implementation because `get_relation_history` was missing.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 KG/vault suite: 65 passed, 27 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py::test_stamped_soul_database_migration_repairs_missing_new_tables` - SUM-003 migration guard regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server alembic -c apps/server/alembic_core.ini downgrade -1`; `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run db:server:upgrade`; `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run db:server:current` - SUM-003 migration rollback/re-upgrade passed, current `dbbe99c1da3a (head)`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 full backend suite: 1687 passed, 1 skipped, 253 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - SUM-003 lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - SUM-003 build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertEntity::test_upsert_same_exact_name_tolerates_type_drift apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_readding_superseded_relation_creates_new_interval` - SUM-003 PR #70 review regressions failed before fix with entity unique constraint violation and superseded relation row reuse.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertEntity::test_upsert_same_exact_name_tolerates_type_drift apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_readding_superseded_relation_creates_new_interval` - SUM-003 PR #70 review regressions: 2 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 PR #70 review suite: 67 passed, 29 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_creation_flow.py::test_agent_can_generate_thinking_monologue_draft -q` - SUM-003 isolated rerun of an order-dependent full-suite failure: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - SUM-003 PR #70 review fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - SUM-003 PR #70 review fix build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 PR #70 review fix full backend suite: first run failed on order-dependent `test_agent_can_generate_thinking_monologue_draft`, isolated rerun passed, longer rerun passed: 1689 passed, 1 skipped, 255 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 PR #70 review fix health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_graph_api.py::test_graph_current_endpoints_filter_superseded_relations` - SUM-003 PR #70 rereview graph API regression failed before fix because `/api/graph/{user_id}/entities/{id}` returned the superseded `Acme` edge.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_graph_api.py::test_graph_current_endpoints_filter_superseded_relations` - SUM-003 PR #70 rereview graph API regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 PR #70 rereview graph/API suite: 68 passed, 29 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - SUM-003 PR #70 rereview fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - SUM-003 PR #70 rereview fix build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 PR #70 rereview fix full backend suite: 1690 passed, 1 skipped, 255 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 PR #70 rereview fix health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py::test_legacy_soul_database_migration_repairs_existing_kg_columns` - SUM-003 PR #70 legacy KG repair regression failed before fix because legacy KG tables were missing current mapped columns.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py::test_legacy_soul_database_migration_repairs_existing_kg_columns` - SUM-003 PR #70 legacy KG repair regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 PR #70 legacy KG repair suite: 93 passed, 29 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - SUM-003 PR #70 legacy KG repair lint: passed after import-order cleanup.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - SUM-003 PR #70 legacy KG repair build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 PR #70 legacy KG repair full backend suite: 1691 passed, 1 skipped, 255 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 PR #70 legacy KG repair health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_duplicate_relation_without_confidence_preserves_existing_confidence apps/server/tests/test_knowledge_graph.py::TestSearchGraphDepth1::test_resolves_start_entity_by_alias apps/server/tests/test_graph_api.py::test_graph_search_resolves_entity_aliases` - SUM-003 PR #70 alias/confidence regressions failed before fix because duplicate relation upsert overwrote stored confidence and alias graph search returned no results.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_duplicate_relation_without_confidence_preserves_existing_confidence apps/server/tests/test_knowledge_graph.py::TestSearchGraphDepth1::test_resolves_start_entity_by_alias apps/server/tests/test_graph_api.py::test_graph_search_resolves_entity_aliases` - SUM-003 PR #70 alias/confidence regressions: 3 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 PR #70 alias/confidence suite: 96 passed, 31 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - SUM-003 PR #70 alias/confidence lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - SUM-003 PR #70 alias/confidence build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_dashboard_api.py::test_proactive_notice_uses_saved_custom_instruction -q` - SUM-003 isolated rerun of an order-dependent full-suite failure: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 PR #70 alias/confidence full backend suite: first run failed on order-dependent `test_proactive_notice_uses_saved_custom_instruction`, isolated rerun passed, longer rerun passed: 1694 passed, 1 skipped, 257 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 PR #70 alias/confidence health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestGraphContextForQuery::test_resolves_alias_when_blocking_embeddings_disabled` - SUM-003 PR #70 alias-context regression failed before fix because alias-only graph context returned no lines when semantic fallback was disabled.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestGraphContextForQuery::test_resolves_alias_when_blocking_embeddings_disabled` - SUM-003 PR #70 alias-context regression: 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 PR #70 alias-context suite: 97 passed, 32 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - SUM-003 PR #70 alias-context lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - SUM-003 PR #70 alias-context build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 PR #70 alias-context full backend suite: 1695 passed, 1 skipped, 258 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 PR #70 alias-context health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestIngestConversationGraphRules::test_pruning_resolves_alias_subject_entities` - SUM-003 PR #70 alias-pruning regression failed before fix because alias-only turn subjects excluded canonical stale edges from pruning candidates.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestIngestConversationGraphRules::test_pruning_resolves_alias_subject_entities` - SUM-003 PR #70 alias-pruning regression: 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 PR #70 alias-pruning suite: 98 passed, 33 warnings.
  - `bun run lint` - SUM-003 PR #70 alias-pruning lint: passed.
  - `bun run build` - SUM-003 PR #70 alias-pruning build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 PR #70 alias-pruning full backend suite: 1696 passed, 1 skipped, 259 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 PR #70 alias-pruning health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestIngestConversationGraphRules::test_pruning_ignores_superseded_relation_candidates` - SUM-003 PR #70 active-pruning regression failed before fix because superseded relations were still sent to stale-pruning candidates.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestIngestConversationGraphRules::test_pruning_resolves_alias_subject_entities apps/server/tests/test_knowledge_graph.py::TestIngestConversationGraphRules::test_pruning_ignores_superseded_relation_candidates` - SUM-003 PR #70 active-pruning regressions: 2 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 PR #70 active-pruning suite: 99 passed, 34 warnings.
  - `bun run lint` - SUM-003 PR #70 active-pruning lint: passed.
  - `bun run build` - SUM-003 PR #70 active-pruning build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 PR #70 active-pruning full backend suite: 1697 passed, 1 skipped, 260 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 PR #70 active-pruning health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py::test_legacy_kg_migration_downgrade_tolerates_missing_constraints` - SUM-003 PR #70 legacy downgrade regression failed before fix with `ValueError: No such constraint` during Alembic downgrade.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py::test_legacy_kg_migration_downgrade_tolerates_missing_constraints` - SUM-003 PR #70 legacy downgrade regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 PR #70 legacy downgrade suite: 100 passed, 34 warnings.
  - `bun run lint` - SUM-003 PR #70 legacy downgrade lint: passed.
  - `bun run build` - SUM-003 PR #70 legacy downgrade build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 PR #70 legacy downgrade full backend suite: 1698 passed, 1 skipped, 260 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 PR #70 legacy downgrade health smoke for `GET /health`: 200 ok.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_same_triple_supersession_creates_replacement_interval` - SUM-003 PR #70 self-supersession regression failed before fix because same-triple supersession updated and superseded the existing row instead of inserting a replacement.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_knowledge_graph.py::TestUpsertRelation::test_same_triple_supersession_creates_replacement_interval` - SUM-003 PR #70 self-supersession regression: 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py apps/server/tests/test_graph_api.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_vault.py` - SUM-003 PR #70 self-supersession suite: 101 passed, 35 warnings.
  - `bun run lint` - SUM-003 PR #70 self-supersession lint: passed.
  - `bun run build` - SUM-003 PR #70 self-supersession build: passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - SUM-003 PR #70 self-supersession full backend suite: 1699 passed, 1 skipped, 261 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - SUM-003 PR #70 self-supersession health smoke for `GET /health`: 200 ok.
- Changed paths:
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-003-temporal-knowledge-graph-v2.md
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
- Notes:
  - Parent remains `in_progress` while later child tickets are still backlog.
  - SUM-002 did not require schema migration.
  - SUM-002 episode detail preservation now appends only grounded LLM-selected salient details, not ordinary turns.
  - SUM-002 relative date context now resolves from the matching turn timestamp when available.
  - SUM-002 degraded lexical fallback paths now use shared Unicode tokenization plus non-space script unigrams and overlap fallback for non-positive BM25 scores.
  - SUM-002 memory relation matching now preserves non-stop one-character ASCII slot values.
  - SUM-002 degraded BM25, vector, and transcript fallback retrieval now preserves one-character ASCII identifiers.
  - SUM-002 salient episode detail grounding now checks full cleaned text before truncating stored excerpts.
  - SUM-002 transcript fallback no longer treats one-character ASCII queries as raw substrings inside unrelated words.
  - SUM-003 keeps KG traversal active-relation compatible while preserving superseded relation history for latest-belief and relationship-history retrieval.
  - SUM-003 core migration skips safely for stamped legacy soul databases missing KG tables; metadata repair creates the current schema immediately afterward.
  - SUM-003 PR #70 review fix keeps exact normalized-name entity upserts on the existing row when extractor type labels drift.
  - SUM-003 PR #70 review fix only reuses active relation rows so re-observed superseded triples create new intervals instead of mutating historical facts.
  - SUM-003 PR #70 rereview fix treats `/api/graph/{user_id}/overview`, `/entities/{id}`, and `/relations` as current-graph endpoints by filtering to active relations.
  - SUM-003 PR #70 legacy repair fix adds current KG columns and indexes to old KG tables in the legacy stamp path before mapped KG operations run.
  - SUM-003 PR #70 alias/confidence fix preserves existing relation confidence when duplicate upserts omit confidence.
  - SUM-003 PR #70 alias/confidence fix resolves graph traversal and public graph search from entity aliases as well as canonical names.
  - SUM-003 PR #70 alias-context fix resolves graph-context query entity extraction from aliases when semantic fallback is disabled.
  - SUM-003 PR #70 alias-pruning fix resolves stale-pruning turn entities from aliases and relation endpoints so canonical stale edges are considered when a turn uses an alias-only subject.
  - SUM-003 PR #70 active-pruning fix filters stale-pruning candidate relations to active rows so already-superseded edges are not re-presented as current facts.
  - SUM-003 PR #70 legacy downgrade fix skips FK drops for repaired legacy KG tables that were stamped at head without named constraints.
  - SUM-003 PR #70 self-supersession fix excludes explicitly superseded/evolved relation IDs from duplicate-active lookup so same-triple corrections create a new active interval.
  - SUM-004 PR #71 vault restore fix inserts structured profile rows without unresolved profile/claim provenance FKs, then backfills only in-snapshot profile self-links after all profile rows exist.
  - SUM-004 PR #71 forget cleanup fix deletes profile evidence rows explicitly before deleting the profile field so SQLite soul DBs without FK cascade enforcement do not retain orphaned evidence.
  - SUM-004 PR #71 claim reconciliation fix compares active profile timestamps against claim provenance timestamps so older claims cannot supersede newer non-claim profile updates.
  - SUM-004 PR #71 profile API fix maps blank correction values to HTTP 400 while preserving 404 for missing active profile fields.
  - SUM-004 PR #71 profile candidate retry fix passes candidate creation time into promotion and preserves newer profile fields from older automatic updates.
  - SUM-004 PR #71 forget/reconciliation fix deletes single-token runtime-message profile facts only when profile metadata supports the relation and scopes sourceless source-memory reconciliation evidence to the target profile field.
  - SUM-004 PR #71 profile/budget fix keeps same-value upserts from moving `last_observed_at` backwards and gives `user_profile` an explicit tier-0 prompt-budget policy.
