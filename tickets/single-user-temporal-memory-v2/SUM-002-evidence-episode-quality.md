# SUM-002 - Evidence baseline and episode quality

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`, `apps/server/tests`
- Parent: `SUM-000`
- Depends on: `SUM-001`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-29 18:49 MYT
- Started: 2026-06-29 10:36 MYT
- Completed: 2026-06-29 11:03 MYT

## Goal

Make source-grounded memory and high-quality episode summaries the baseline for later profile, graph, foresight, and skill work.

## Deliverables

- Operational F15 evidence backfill path for existing vaults.
- Evidence audit helper for active durable memories.
- F9 episode prompt upgrades and validation.
- Tests proving concrete details, timestamps, and participants survive episode generation.
- Production multilingual baseline for SUM-002 memory paths: grounded episode details, lexical fallbacks, transcript search metadata, and generic claim keys.

## Acceptance

- Active `MemoryItem` evidence coverage can be measured.
- Episode generation receives conversation timestamp and participant names.
- Episode summaries preserve important concrete details, including multilingual original-language details.
- LLM-selected salient episode details are grounded against the user transcript before being stored.
- Degraded lexical memory and transcript fallbacks do not fail closed on non-English or non-space-delimited text.
- Provenance tests and episode tests pass.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-06-29 10:36 MYT - Claimed by Codex on branch `codex/sum-002-evidence-episode-quality`, based on PR #67 branch `codex/sum-001-memory-baseline`.
- 2026-06-29 11:03 MYT - Completed evidence audit/backfill endpoints and timestamp/detail-preserving episode generation validation.
- 2026-06-29 11:13 MYT - Replaced language-biased concrete-detail token extraction with bounded exact user excerpts and added multilingual episode regression coverage.
- 2026-06-29 11:38 MYT - Expanded SUM-002 to production multilingual baseline: LLM-backed grounded salient episode details, Unicode lexical fallbacks, transcript metadata/search coverage, vector fallback coverage, and non-English claim key safeguards.
- 2026-06-29 12:32 MYT - Addressed PR #68 review threads for active-first evidence backfill, dual-time relative date context, durable-only episode detail fallback, one-character multilingual/digit tokens, and empty-token BM25 corpora.
- 2026-06-29 13:06 MYT - Addressed Codex rereview threads for midnight-spanning relative date resolution and single-character CJK fallback search inside longer non-space text.
- 2026-06-29 17:01 MYT - Addressed Codex rereview thread for non-positive BM25 scores on ubiquitous CJK unigram hits by falling back to overlap scoring.
- 2026-06-29 17:37 MYT - Addressed Codex rereview thread for one-character ASCII memory slot values by preserving non-stop single-character tokens in memory relation matching.
- 2026-06-29 18:08 MYT - Addressed Codex rereview threads for one-character ASCII fallback retrieval and long salient episode excerpts by preserving identifier tokens in degraded retrieval callers and grounding full details before truncation.
- 2026-06-29 18:49 MYT - Addressed Codex rereview thread for one-character transcript query false positives by routing single-character transcript queries through token overlap instead of substring matching.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_preserves_multilingual_user_details` - failed before fix because Japanese and lowercase non-English details were dropped by the old extractor
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_uses_grounded_llm_salient_details apps/server/tests/test_memory_multilingual_baseline.py` - failed before fix because the episode path copied whole user turns instead of LLM-selected grounded details, lexical fallbacks returned zero for Japanese text, transcript sidecars omitted Japanese keywords, and generic non-English claim slugs collapsed to empty strings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_provenance_backfill.py::test_backfill_memory_item_evidence_prioritizes_active_missing_rows apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_does_not_append_ordinary_turns_without_salient_details apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_in_summary apps/server/tests/test_memory_multilingual_baseline.py::test_unicode_tokens_preserve_one_character_multilingual_and_digit_values apps/server/tests/test_memory_multilingual_baseline.py::test_bm25_fallback_handles_empty_tokenized_corpus` - failed before review fixes for the five unresolved PR threads, then 5 passed, 3 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_from_matching_turn apps/server/tests/test_memory_multilingual_baseline.py::test_single_character_cjk_queries_match_longer_non_space_text` - failed before rereview fixes, then 2 passed, 1 warning
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_provenance_backfill.py::test_backfill_memory_item_evidence_prioritizes_active_missing_rows apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_does_not_append_ordinary_turns_without_salient_details apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_in_summary apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_from_matching_turn apps/server/tests/test_memory_multilingual_baseline.py::test_unicode_tokens_preserve_one_character_multilingual_and_digit_values apps/server/tests/test_memory_multilingual_baseline.py::test_single_character_cjk_queries_match_longer_non_space_text apps/server/tests/test_memory_multilingual_baseline.py::test_bm25_fallback_handles_empty_tokenized_corpus` - 7 passed, 4 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py::test_bm25_fallback_ranks_cjk_unigram_by_overlap_when_bm25_scores_are_non_positive` - failed before fix, then 1 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py` - 30 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py::test_memory_relation_preserves_one_character_ascii_slot_values` - failed before fix, then 1 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py` - 31 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_provenance_backfill.py::test_backfill_memory_item_evidence_prioritizes_active_missing_rows apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_does_not_append_ordinary_turns_without_salient_details apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_in_summary apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_resolves_relative_dates_from_matching_turn apps/server/tests/test_memory_multilingual_baseline.py::test_unicode_tokens_preserve_one_character_multilingual_and_digit_values apps/server/tests/test_memory_multilingual_baseline.py::test_single_character_cjk_queries_match_longer_non_space_text apps/server/tests/test_memory_multilingual_baseline.py::test_bm25_fallback_handles_empty_tokenized_corpus apps/server/tests/test_memory_multilingual_baseline.py::test_bm25_fallback_ranks_cjk_unigram_by_overlap_when_bm25_scores_are_non_positive` - 8 passed, 4 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_preserves_multilingual_user_details apps/server/tests/test_agent_episodes.py::test_maybe_generate_episode_passes_timestamp_names_and_preserves_details` - 2 passed, 2 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_api.py apps/server/tests/test_phase3_storage.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py::TestTranscriptSearch` - 118 passed, 34 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_api.py apps/server/tests/test_phase3_storage.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py::TestTranscriptSearch apps/server/tests/test_batch_segmenter.py` - 148 passed, 41 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_api.py apps/server/tests/test_phase3_storage.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py::TestTranscriptSearch apps/server/tests/test_batch_segmenter.py` - 149 passed, 41 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_memory_api.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py apps/server/tests/test_memory_retrieval_rebuild.py apps/server/tests/test_evidence_retrieval.py` - 151 passed, 28 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py::test_degraded_retrieval_preserves_one_character_ascii_identifiers apps/server/tests/test_agent_episodes.py::test_ground_salient_user_details_truncates_after_grounding_long_excerpt` - failed before fix, then 2 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py::test_degraded_retrieval_preserves_one_character_ascii_identifiers` - failed before fix for transcript substring false positives, then 1 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_p5_transcript_archive.py` - 109 passed, 11 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py` - 96 passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_memory_item_evidence.py apps/server/tests/test_memory_api.py apps/server/tests/test_memory_provenance_backfill.py apps/server/tests/test_agent_episodes.py apps/server/tests/test_memory_multilingual_baseline.py apps/server/tests/test_bm25_index.py apps/server/tests/test_p5_transcript_archive.py apps/server/tests/test_memory_retrieval_rebuild.py apps/server/tests/test_evidence_retrieval.py` - 153 passed, 28 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` smoke for `GET /health` - 200 `{'status': 'ok', 'service': 'server', 'environment': 'development', 'provisioned': False}`
  - `bun run lint` - passed
  - `bun run build` - passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - first run stopped on order-dependent `test_proactive_notice_endpoint_accepts_custom_instruction` scaffold text mismatch; isolated rerun passed
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - rerun: 1678 passed, 1 skipped, 250 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1679 passed, 1 skipped, 250 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - first run hit the 5-minute tool timeout before producing a result; rerun with a longer timeout: 1680 passed, 1 skipped, 250 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1682 passed, 1 skipped, 250 warnings
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - 1682 passed, 1 skipped, 250 warnings
- Changed paths:
  - apps/server/src/anima_server/api/routes/memory.py
  - apps/server/src/anima_server/schemas/memory.py
  - apps/server/src/anima_server/services/agent/batch_segmenter.py
  - apps/server/src/anima_server/services/agent/bm25_index.py
  - apps/server/src/anima_server/services/agent/claims.py
  - apps/server/src/anima_server/services/agent/episodes.py
  - apps/server/src/anima_server/services/agent/memory_store.py
  - apps/server/src/anima_server/services/agent/prompt_loader.py
  - apps/server/src/anima_server/services/agent/provenance.py
  - apps/server/src/anima_server/services/agent/templates/prompts/episode_generation.md.j2
  - apps/server/src/anima_server/services/agent/text_processing.py
  - apps/server/src/anima_server/services/agent/transcript_archive.py
  - apps/server/src/anima_server/services/agent/transcript_search.py
  - apps/server/src/anima_server/services/agent/vector_store.py
  - apps/server/tests/test_agent_episodes.py
  - apps/server/tests/test_memory_api.py
  - apps/server/tests/test_memory_multilingual_baseline.py
  - apps/server/tests/test_memory_provenance_backfill.py
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-002-evidence-episode-quality.md
- Notes:
  - No schema migration was needed.
  - Episode detail preservation now appends only LLM-selected salient details after verifying each detail against user messages; ordinary turns are not appended as durable details.
  - Relative date context now resolves from the turn that contained the relative phrase when per-turn timestamps are available, and skips deterministic resolution for ambiguous multi-date segments.
  - Degraded lexical memory paths now use shared Unicode lexical tokenization with unigrams for non-space script fallback search, and BM25 falls back to overlap scoring when BM25 returns no positive score.
  - Memory relation matching now preserves non-stop one-character ASCII slot values such as `username: x` and `username: y`.
  - Degraded BM25, vector, and transcript fallback retrieval now preserve one-character ASCII identifiers such as `x`.
  - Salient episode details are grounded against the full cleaned detail before the stored excerpt is truncated.
  - Transcript fallback no longer treats one-character ASCII queries as raw substrings inside unrelated words.
