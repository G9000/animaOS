# MPB-002 - Contract fixture parity

- Status: backlog
- Priority: P1
- Scope: `apps/server/tests/fixtures/memory_contract`, `apps/server/src/anima_server/services/memory`, `packages/anima-core`
- Parent: `MPB-000`
- Depends on: `MPB-001`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-07-03-memory-package-boundary-hardening.md
- Created: 2026-07-03 17:12 MYT
- Updated: 2026-07-03 17:12 MYT
- Started:
- Completed:

## Goal

Make Python and Rust consume the same memory contract fixtures so the package boundary does not drift across languages.

## Deliverables

- JSON fixtures for temporal facts, temporal relationships, salience, and recall traces.
- Python fixture tests for `services.memory`.
- Rust fixture tests for `anima_core::memory_contract`.

## Acceptance

- The same fixture files parse in Python and Rust.
- Snake_case enum values stay aligned.
- Fixture failures produce actionable contract drift signals.
- Existing contract tests still pass.

## Activity Log

- 2026-07-03 17:12 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
