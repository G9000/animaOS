# SUM-000 - Single-User Temporal Memory v2 Parent Tracker

- Status: in_progress
- Priority: P1
- Scope: `apps/server`, `docs/prds/memory`, `docs/architecture/memory`, `tickets/single-user-temporal-memory-v2`
- Depends on: none
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-29 18:49 MYT
- Started: 2026-06-29 02:30 MYT
- Completed:

## Goal

Track the single-user temporal memory v2 initiative from baseline audit through evidence, temporal graph, profile, retrieval routing, salience, pattern synthesis, foresight, procedural learning, and optional adapter seams.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `SUM-001` | Baseline memory truth audit and eval probes | `done` | none |
| `SUM-002` | Evidence baseline and episode quality | `done` | `SUM-001` |
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
- `SUM-002` - Evidence baseline and episode quality (completed 2026-06-29 11:03 MYT)

## Activity Log

- 2026-06-27 12:40 MYT - Parent tracker created for single-user temporal memory v2 planning.
- 2026-06-29 02:30 MYT - `SUM-001` claimed by Codex on branch `codex/sum-001-memory-baseline`.
- 2026-06-29 03:12 MYT - `SUM-001` completed with baseline audit, deterministic recall probes, focused fixes, and validation.
- 2026-06-29 03:32 MYT - `SUM-001` updated after Codex review fixes and validation rerun.
- 2026-06-29 10:36 MYT - `SUM-002` claimed by Codex on branch `codex/sum-002-evidence-episode-quality`, based on PR #67 branch `codex/sum-001-memory-baseline`.
- 2026-06-29 11:03 MYT - `SUM-002` completed with evidence audit/backfill endpoints, episode timestamp/detail safeguards, focused validation, lint, build, and health smoke.
- 2026-06-29 11:13 MYT - `SUM-002` updated after review feedback to use multilingual-safe exact user detail excerpts.
- 2026-06-29 11:38 MYT - `SUM-002` expanded to production multilingual baseline across grounded episode details, lexical fallbacks, transcript search metadata, vector fallback scoring, and generic claim keys.
- 2026-06-29 12:32 MYT - `SUM-002` addressed PR #68 review threads and reran focused suite, lint, build, full backend tests, and health smoke.
- 2026-06-29 13:06 MYT - `SUM-002` addressed Codex rereview threads for per-turn relative date resolution and CJK unigram fallback search.
- 2026-06-29 17:01 MYT - `SUM-002` addressed Codex rereview thread for non-positive BM25 scores on ubiquitous CJK unigram hits.
- 2026-06-29 17:37 MYT - `SUM-002` addressed Codex rereview thread for one-character ASCII memory slot values in relation matching.
- 2026-06-29 18:08 MYT - `SUM-002` addressed Codex rereview threads for one-character ASCII fallback retrieval and long salient episode excerpt grounding.
- 2026-06-29 18:49 MYT - `SUM-002` addressed Codex rereview thread for one-character transcript query false positives.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_bm25_index.py::TestRustBackedKeywordSearch::test_bm25_search_uses_rust_memory_index_when_clean apps/server/tests/test_memory_scored_retrieval.py::test_scored_retrieval_pool_keeps_hot_older_items apps/server/tests/test_memory_scored_retrieval.py::test_scored_retrieval_pool_keeps_fresh_unscored_items apps/server/tests/test_sleep_agent.py::TestRestartCursor::test_consolidation_task_records_latest_runtime_message_cursor apps/server/tests/test_vault.py::test_export_and_import_vault_restores_knowledge_graph apps/server/tests/test_vault.py::test_capsule_sections_include_knowledge_graph_tables apps/server/tests/test_single_user_memory_baseline_probes.py` - 11 passed, 7 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test` - 1660 passed, 1 skipped, 242 warnings
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
- Changed paths:
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
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
