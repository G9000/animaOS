# SUM-008 - Foresight signals

- Status: done
- Priority: P2
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-002`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-03 13:52 MYT
- Started: 2026-07-03 02:54 MYT
- Completed: 2026-07-03 03:36 MYT

## Goal

Implement future-oriented memory so Anima can remember commitments, expected events, and follow-up opportunities.

## Deliverables

- F8 `ForesightSignal` model and migration.
- Consolidation extraction for future events and expected outcomes.
- Relative date resolution against conversation timestamp.
- Lifecycle sweep for active, due, occurred, stale, and cancelled signals.
- Retrieval and proactive prompt integration.

## Acceptance

- Future events are extracted without requiring explicit task creation.
- Relative dates resolve deterministically in tests.
- Stale/due lifecycle transitions are covered.
- Foresight signals remain evidence-backed.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-07-03 02:54 MYT - Claimed by Codex on branch `codex/sum-008-009-foresight-procedural`, based on PR #67 head.
- 2026-07-03 03:36 MYT - Completed with foresight signal storage, extraction, lifecycle sweep, prompt integration, and validation.
- 2026-07-03 10:21 MYT - Addressed PR #77 review feedback by bounding regex extraction to sentence boundaries and anchoring relative dates to source runtime message timestamps.
- 2026-07-03 10:49 MYT - Addressed PR #77 rereview feedback by running foresight lifecycle in scheduled sleeptime and filtering overdue active prompt rows before limiting.
- 2026-07-03 11:05 MYT - Addressed PR #77 rereview feedback by adding foresight payload rules to the LLM memory extraction prompt and system message.
- 2026-07-03 11:29 MYT - Addressed PR #77 rereview feedback by ranking due and dated foresight rows ahead of undated rows in prompt retrieval.
- 2026-07-03 11:45 MYT - Addressed PR #77 rereview feedback by resolving relative foresight dates against the saved user timezone.
- 2026-07-03 12:07 MYT - Addressed PR #77 rereview feedback by defaulting lifecycle sweeps and prompt filtering to the saved user timezone.
- 2026-07-03 13:52 MYT - Addressed PR #77 current-head review feedback by keeping recently occurred foresight rows prompt-visible during their follow-up window.

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py::test_prompt_foresight_keeps_recently_occurred_followups` - PR #77 current-head review regression failed before fix because recently occurred rows were excluded from prompt retrieval.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py::test_prompt_foresight_keeps_recently_occurred_followups` - PR #77 current-head review occurred-followup regression: 1 passed, 1 warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py apps/server/tests/test_agent_experience.py apps/server/tests/test_agent_service.py` - PR #77 current-head review focused backend set: 45 passed, 34 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #77 current-head review lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 current-head review build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 current-head review whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py::test_foresight_lifecycle_defaults_to_user_local_date` - PR #77 rereview regression failed before fix because lifecycle sweeps defaulted to UTC instead of the saved user timezone.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py::test_prompt_foresight_defaults_to_user_local_date` - PR #77 rereview regression failed before fix because prompt filtering defaulted to UTC instead of the saved user timezone.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py` - PR #77 rereview local-date suite: 10 passed, 8 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_sleep_agent.py` - PR #77 rereview lifecycle caller suite: 22 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #77 rereview local-date lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 rereview local-date build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 rereview local-date whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py::test_relative_foresight_extraction_uses_user_timezone_for_local_dates` - PR #77 rereview regression failed before fix because foresight extraction could not accept a user timezone for local date resolution.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py` - PR #77 rereview timezone suite: 8 passed, 6 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_consolidation.py` - PR #77 rereview consolidation suite: 11 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_pending_memory_ops.py` - PR #77 rereview timezone tool suite: 22 passed, 18 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #77 rereview timezone lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 rereview timezone build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 rereview timezone whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py::test_prompt_foresight_prioritizes_dated_rows_over_undated_rows` - PR #77 rereview regression failed before fix because an undated row was returned before an upcoming dated row.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py` - PR #77 rereview foresight suite: 7 passed, 6 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #77 rereview lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 rereview build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 rereview whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_consolidation.py::test_llm_memory_extraction_prompt_requests_foresight` - PR #77 rereview regression failed before fix because the LLM extraction prompt/system message did not request foresight payloads.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_consolidation.py` - PR #77 rereview consolidation suite: 11 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #77 rereview lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 rereview build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 rereview whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_sleep_agent.py::test_scheduled_sleeptime_runs_foresight_lifecycle` - PR #77 rereview regression failed before fix because scheduled sleeptime did not run foresight lifecycle transitions.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py::test_prompt_foresight_skips_overdue_active_rows_before_limiting` - PR #77 rereview regression failed before fix because old overdue active rows could consume the prompt query limit before filtering.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_sleep_agent.py` - PR #77 rereview sleep suite: 22 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py` - PR #77 rereview foresight suite: 6 passed, 5 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #77 rereview lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 rereview build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 rereview whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py::test_foresight_extraction_does_not_cross_sentence_boundaries` - PR #77 review regression failed before fix with a bogus headache/product-review foresight signal.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_consolidation.py::test_run_background_extraction_anchors_foresight_to_source_message_time` - PR #77 review regression failed before fix because `observed_at` was not propagated from `RuntimeMessage.created_at`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py apps/server/tests/test_agent_consolidation.py` - PR #77 review focused suite: 15 passed, 6 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - PR #77 review fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #77 review fix build: passed with existing Vite chunk-size warning.
  - `git diff --check` - PR #77 review fix whitespace check: passed.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py` - failed before implementation because `ForesightSignal` was not exported from `anima_server.models`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py` - 4 passed, 4 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py apps/server/tests/test_agent_experience.py` - 8 passed, 8 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_prompt_budget.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_service.py` - 64 passed, 24 warnings.
  - `bun run db:server:heads` - `20260703_0002 (head)`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server -- --maxfail=1 -q` - 1775 passed, 1 skipped, 310 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - health smoke for `GET /health`: 200 ok.
- Changed paths:
  - apps/server/alembic_core/versions/20260701_0003_add_memory_salience.py
  - apps/server/alembic_core/versions/20260703_0001_create_foresight_signals.py
  - apps/server/src/anima_server/models/__init__.py
  - apps/server/src/anima_server/models/agent_runtime.py
  - apps/server/src/anima_server/services/agent/consolidation.py
  - apps/server/src/anima_server/services/agent/foresight.py
  - apps/server/src/anima_server/services/agent/memory_blocks.py
  - apps/server/src/anima_server/services/agent/prompt_budget.py
  - apps/server/src/anima_server/services/agent/sleep_agent.py
  - apps/server/src/anima_server/services/agent/sleep_tasks.py
  - apps/server/src/anima_server/services/agent/templates/prompts/memory_extraction.md.j2
  - apps/server/src/anima_server/services/user_timezone.py
  - apps/server/tests/test_agent_consolidation.py
  - apps/server/tests/test_foresight.py
  - apps/server/tests/test_sleep_agent.py
- Notes:
  - PR #77 current-head review fix includes `occurred` foresight rows for seven days after their end date so the prompt can naturally follow up before stale cleanup.
  - PR #77 rereview fix uses the saved world-context timezone for default lifecycle and prompt dates, keeping extracted local dates and status/prompt filtering on the same calendar day.
  - PR #77 rereview fix reads the saved `Timezone:` world-context value and passes it into regex/LLM foresight relative-date resolution.
  - PR #77 rereview fix ranks due rows first, dated rows next, and undated rows last so undated LLM foresight cannot starve upcoming dated events from prompt retrieval.
  - PR #77 rereview fix adds foresight schema/rules to the LLM memory extraction prompt and includes foresight in the extraction system message.
  - Added a defensive guard to the inherited salience migration so stamped legacy soul databases missing `memory_items` can reach metadata repair; this unblocked full-suite validation.
