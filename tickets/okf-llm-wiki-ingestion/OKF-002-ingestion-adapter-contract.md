# OKF-002 - Source registry and adapter contract

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion`
- Parent: `OKF-000`
- Depends on: `OKF-001`
- Owner: unassigned
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-06 23:23 MYT
- Started:
- Completed:

## Goal

Create a source-type-agnostic ingestion package with a registry, artifact/span storage helpers, and adapter contract for all source kinds.

## Deliverables

- `services/ingestion/` package with dataclasses and helper modules.
- Adapter base contract that emits normalized artifacts and spans.
- Source registry helpers for idempotent create/reuse behavior.
- Artifact/span replacement helpers with safe status transitions.
- Tests for adapter contract and failure behavior.

## Acceptance

- Registering the same source for the same user is idempotent by content or URI hash.
- Adapter outputs can represent page, time, line, row, cell, and image annotation locators.
- Failed adapters record failed run state and do not leave half-written spans.
- No implementation path is PDF-specific in the core contract.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Expected focused command: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py -q`
