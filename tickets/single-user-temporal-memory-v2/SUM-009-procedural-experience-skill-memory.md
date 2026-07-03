# SUM-009 - Procedural experience and skill memory

- Status: done
- Priority: P2
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-005`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-03 11:45 MYT
- Started: 2026-07-03 02:54 MYT
- Completed: 2026-07-03 03:36 MYT

## Goal

Let Anima learn reusable procedures from its own tool use, mistakes, recoveries, and successful user-specific approaches.

## Deliverables

- F11 agent experience model, extraction, embedding, and retrieval.
- F12 stable experience clustering.
- F13 skill distillation from repeated clusters.
- Learned skill prompt block with skill priority over raw experiences when confidence is high.
- Growth log integration for meaningful learning.

## Acceptance

- Tool failures and recoveries can become experience memory.
- Similar experiences cluster stably across restarts.
- Skills are distilled only after enough evidence exists.
- Skills are retrieved for matching future tasks.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-07-03 02:54 MYT - Claimed by Codex on branch `codex/sum-008-009-foresight-procedural`, based on PR #67 head.
- 2026-07-03 03:36 MYT - Completed with agent experience storage, clustering, skill distillation, prompt retrieval, and validation.
- 2026-07-03 10:49 MYT - Addressed PR #77 rereview feedback by leaving unembedded agent experiences unclustered so embedding outages cannot seed learned skill clusters.
- 2026-07-03 11:05 MYT - Addressed PR #77 rereview feedback by explicitly persisting matched-cluster JSON state updates across sessions.
- 2026-07-03 11:45 MYT - Addressed PR #77 rereview feedback by skipping procedural experience capture for approval resumes without an originating user prompt.

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_service.py::test_post_turn_hooks_skip_experience_capture_without_source_prompt` - PR #77 rereview regression failed before fix because promptless approval resumes scheduled a generic procedural experience extraction.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_service.py` - PR #77 rereview agent service suite: 26 passed, 18 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #77 rereview approval-resume lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 rereview approval-resume build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 rereview approval-resume whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_experience.py::test_existing_experience_cluster_state_updates_persist_across_sessions` - PR #77 rereview regression failed before fix because matched cluster JSON state reloaded with count `1` after adding a second similar experience.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_experience.py` - PR #77 rereview experience suite: 6 passed, 6 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #77 rereview lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 rereview build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 rereview whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_experience.py::test_unembedded_experiences_do_not_enter_skill_clusters` - PR #77 rereview regression failed before fix because unembedded experiences were assigned to `cluster_{user}_000`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_experience.py` - PR #77 rereview experience suite: 5 passed, 5 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #77 rereview lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 rereview build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 rereview whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_experience.py` - failed before implementation because `AgentExperience` was not exported from `anima_server.models`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_experience.py` - 4 passed, 4 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py apps/server/tests/test_agent_experience.py` - 8 passed, 8 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_prompt_budget.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_service.py` - 64 passed, 24 warnings.
  - `bun run db:server:heads` - `20260703_0002 (head)`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server -- --maxfail=1 -q` - 1775 passed, 1 skipped, 310 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - health smoke for `GET /health`: 200 ok.
- Changed paths:
  - apps/server/alembic_core/versions/20260703_0002_create_agent_experience_memory.py
  - apps/server/src/anima_server/models/__init__.py
  - apps/server/src/anima_server/models/agent_runtime.py
  - apps/server/src/anima_server/services/agent/agent_experience.py
  - apps/server/src/anima_server/services/agent/memory_blocks.py
  - apps/server/src/anima_server/services/agent/prompt_budget.py
  - apps/server/src/anima_server/services/agent/service.py
  - apps/server/tests/test_agent_experience.py
  - apps/server/tests/test_agent_service.py
- Notes:
  - PR #77 rereview fix leaves consolidation/reflection hooks intact on approval resume while preventing promptless tool-only turns from seeding generic experience memories.
  - PR #77 rereview fix flags `ExperienceClusterState.state_json` as modified after centroid/count/activity mutations so SQLAlchemy persists matched-cluster updates.
  - Raw past approaches are suppressed when a matching high-confidence distilled skill is available, keeping the prompt path compact.
