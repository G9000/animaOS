# SUM-007 - Cross-episode pattern synthesis

- Status: done
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-005`, `SUM-006`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-03 02:26 MYT
- Started: 2026-07-02 22:59 MYT
- Completed: 2026-07-02 22:59 MYT

## Goal

Add a sleep-time synthesis pass that discovers recurring patterns across episodes and turns repeated observations into compact, evidence-backed memory.

## Deliverables

- `pattern_synthesis.py` sleep-time task.
- Episode sampling by time window, topic, and salience.
- Pattern extraction prompt and strict JSON parser.
- Storage strategy for patterns as profile fields, graph relations, or memory items.
- Prompt block rendering for high-confidence active patterns only.

## Acceptance

- Single mentions do not create durable patterns.
- Repeated evidence across episodes creates a pattern.
- Patterns cite source episode/evidence IDs.
- Prompt rendering stays compact.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-07-02 22:59 MYT - Implemented cross-episode pattern synthesis with time/topic/salience episode sampling, strict repeated-evidence parsing, evidence-backed pattern memory storage, compact prompt rendering, and sleep-time orchestration hooks.
- 2026-07-02 23:14 MYT - Recorded final lint/build validation and noted that the full backend suite was stopped at user request before completion.
- 2026-07-03 00:53 MYT - Reran focused, adjacent, and full backend suites before review; full suite failed on an inherited SUM-006 migration repair regression.
- 2026-07-03 01:05 MYT - Addressed Codex review feedback by making duplicate pattern synthesis idempotent for already-seen source episodes.
- 2026-07-03 01:17 MYT - Addressed Codex review feedback by decrypting episode emotional arcs before rendering pattern synthesis prompts.
- 2026-07-03 01:30 MYT - Addressed Codex review feedback by running manual sleep episode generation before pattern synthesis and skipping near-duplicate pattern memories.
- 2026-07-03 01:50 MYT - Addressed Codex review feedback by excluding stale episodes from pattern sampling, cleaning pattern memories during forget/suppression, and honoring the heat visibility floor in pattern prompt blocks.
- 2026-07-03 02:01 MYT - Addressed Codex review feedback by scheduling retrieval/vector index cleanup when forget/suppression deletes derived pattern memories.
- 2026-07-03 02:26 MYT - Addressed Codex review feedback by decrypting pattern evidence before matching forgotten text during derived pattern cleanup.

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py::TestForceMode::test_force_bypasses_heat_gate` - failed before implementation with missing `pattern_synthesis` module, block renderer, and sleep task hook.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_synthesis_creates_evidence_backed_pattern_memory` - failed before source evidence IDs were persisted in pattern metadata.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py::TestForceMode::test_force_bypasses_heat_gate` - 5 passed, 3 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_prompt_budget.py apps/server/tests/test_single_user_memory_baseline_probes.py` - 47 passed, 14 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test` - 1 failed, 1758 passed, 1 skipped, 297 warnings. Failure: `apps/server/tests/test_runtime_db.py::test_stamped_soul_database_migration_repairs_missing_new_tables`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_runtime_db.py::test_stamped_soul_database_migration_repairs_missing_new_tables` - failed deterministically with `sqlalchemy.exc.NoSuchTableError: memory_items` in inherited migration `20260701_0003_add_memory_salience.py`.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_synthesis_skips_duplicate_pattern_evidence_for_same_episodes` - failed before the review fix because duplicate pattern synthesis reported an update and appended duplicate evidence.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_synthesis_skips_duplicate_pattern_evidence_for_same_episodes` - 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py::TestForceMode::test_force_bypasses_heat_gate` - review fix focused suite: 6 passed, 4 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_prompt_budget.py apps/server/tests/test_single_user_memory_baseline_probes.py` - review fix adjacent suite: 48 passed, 15 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - review fix lint: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_prompt_episode_rendering_decrypts_emotional_arc` - failed before the review fix because encrypted `emotional_arc` rendered as `enc2:` ciphertext.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_prompt_episode_rendering_decrypts_emotional_arc` - 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py::TestForceMode::test_force_bypasses_heat_gate` - emotional-arc review focused suite: 7 passed, 5 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_prompt_budget.py apps/server/tests/test_single_user_memory_baseline_probes.py` - emotional-arc review adjacent suite: 49 passed, 16 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - emotional-arc review fix lint: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_sleep_agent.py::test_manual_sleep_generates_episode_before_pattern_synthesis` - failed before the review fix because manual sleep ran pattern synthesis before episode generation.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_synthesis_skips_similar_pattern_memory` - failed before the review fix because near-duplicate pattern text created a second pattern memory.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_sleep_agent.py::test_manual_sleep_generates_episode_before_pattern_synthesis` - manual sleep order review regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_synthesis_skips_similar_pattern_memory` - similar-pattern review regression: 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py::test_manual_sleep_generates_episode_before_pattern_synthesis apps/server/tests/test_sleep_agent.py::TestForceMode::test_force_bypasses_heat_gate apps/server/tests/test_user_profile.py::test_sleep_tasks_reconciles_claims_to_profile_fields apps/server/tests/test_user_profile.py::test_sleep_tasks_invalidates_companion_memory_after_profile_reconciliation` - latest review focused suite: 11 passed, 8 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - latest review fix lint: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_episode_sampling_skips_stale_episodes_marked_for_regeneration` - failed before the P1 fix because stale episodes marked `needs_regeneration` were sampled.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_forgetting.py::TestForgetMemory::test_forget_removes_pattern_memory_citing_stale_episode` - failed before the P1 fix because pattern memories citing stale source episodes survived forgetting.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_pattern_prompt_block_honors_heat_visibility_floor` - failed before the P2 fix because below-floor pattern memories rendered into the prompt block.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_episode_sampling_skips_stale_episodes_marked_for_regeneration` - latest P1 regression: 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_forgetting.py::TestForgetMemory::test_forget_removes_pattern_memory_citing_stale_episode` - latest P1 regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_pattern_prompt_block_honors_heat_visibility_floor` - latest P2 regression: 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_forgetting.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_prompt_budget.py` - latest review focused suite: 59 passed, 14 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - latest review fix lint: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_forgetting.py::TestForgetMemory::test_forget_removes_pattern_memory_citing_stale_episode` - failed before the retrieval-index cleanup fix because the source memory id was deleted from the index but the derived pattern id was not.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_forgetting.py::TestForgetMemory::test_forget_removes_pattern_memory_citing_stale_episode` - retrieval-index cleanup regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_forgetting.py` - retrieval-index cleanup focused suite: 32 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - retrieval-index cleanup lint: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_forgetting.py::TestForgetMemory::test_forget_matches_encrypted_pattern_evidence_text` - failed before the encrypted-evidence cleanup fix because encrypted pattern evidence was compared as ciphertext.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_forgetting.py::TestForgetMemory::test_forget_matches_encrypted_pattern_evidence_text` - encrypted-evidence cleanup regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_forgetting.py` - encrypted-evidence cleanup focused suite: 33 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - encrypted-evidence cleanup lint: passed.
- Changed paths:
  - apps/server/src/anima_server/services/agent/forgetting.py
  - apps/server/src/anima_server/services/agent/pattern_synthesis.py
  - apps/server/src/anima_server/services/agent/templates/prompts/pattern_synthesis.md.j2
  - apps/server/src/anima_server/services/agent/memory_blocks.py
  - apps/server/src/anima_server/services/agent/memory_salience.py
  - apps/server/src/anima_server/services/agent/memory_store.py
  - apps/server/src/anima_server/services/agent/prompt_budget.py
  - apps/server/src/anima_server/services/agent/prompt_loader.py
  - apps/server/src/anima_server/services/agent/sleep_agent.py
  - apps/server/src/anima_server/services/agent/sleep_tasks.py
  - apps/server/tests/test_forgetting.py
  - apps/server/tests/test_pattern_synthesis.py
  - apps/server/tests/test_sleep_agent.py
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-007-cross-episode-pattern-synthesis.md
- Notes:
  - Pattern memories use existing `MemoryItem` and `MemoryItemEvidence` storage with `category="pattern"` and `source="pattern_synthesis"`; no schema migration was required.
  - The full-suite failure is outside the SUM-007 diff; this branch does not modify `apps/server/src/anima_server/db/session.py`, `apps/server/tests/test_runtime_db.py`, or the failing SUM-006 migration.
  - Prompt rendering now decrypts `MemoryEpisode.emotional_arc` with `table="memory_episodes"` and `field="emotional_arc"` before sending sampled episodes to the LLM.
  - Manual sleep now generates any pending episode before pattern synthesis so a single forced sleep can synthesize from the newest episode.
  - Similar pattern classifications are skipped instead of inserted as separate pattern memories.
  - Pattern sampling excludes episodes flagged with `needs_regeneration=True`.
  - Forget/suppression cleanup now removes derived pattern memories that cite stale source episodes or directly contain forgotten content.
  - Deleted derived pattern memories are now scheduled for after-commit retrieval/vector index cleanup.
  - Pattern evidence text is decrypted before forget/suppression matching so encrypted evidence-only provenance can still trigger derived pattern cleanup.
  - Cross-episode pattern prompt rendering now respects the same heat visibility floor used by scored retrieval.
