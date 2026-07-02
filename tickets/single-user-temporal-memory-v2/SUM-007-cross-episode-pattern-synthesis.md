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
- Updated: 2026-07-02 23:14 MYT
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

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py::TestForceMode::test_force_bypasses_heat_gate` - failed before implementation with missing `pattern_synthesis` module, block renderer, and sleep task hook.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py::test_synthesis_creates_evidence_backed_pattern_memory` - failed before source evidence IDs were persisted in pattern metadata.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py::TestForceMode::test_force_bypasses_heat_gate` - 5 passed, 3 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- apps/server/tests/test_pattern_synthesis.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_prompt_budget.py apps/server/tests/test_single_user_memory_baseline_probes.py` - 47 passed, 14 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test` - stopped at user request before completion because it was lagging the local PC; no full-suite result recorded.
- Changed paths:
  - apps/server/src/anima_server/services/agent/pattern_synthesis.py
  - apps/server/src/anima_server/services/agent/templates/prompts/pattern_synthesis.md.j2
  - apps/server/src/anima_server/services/agent/memory_blocks.py
  - apps/server/src/anima_server/services/agent/memory_salience.py
  - apps/server/src/anima_server/services/agent/memory_store.py
  - apps/server/src/anima_server/services/agent/prompt_budget.py
  - apps/server/src/anima_server/services/agent/prompt_loader.py
  - apps/server/src/anima_server/services/agent/sleep_agent.py
  - apps/server/src/anima_server/services/agent/sleep_tasks.py
  - apps/server/tests/test_pattern_synthesis.py
  - apps/server/tests/test_sleep_agent.py
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-007-cross-episode-pattern-synthesis.md
- Notes:
  - Pattern memories use existing `MemoryItem` and `MemoryItemEvidence` storage with `category="pattern"` and `source="pattern_synthesis"`; no schema migration was required.
