# MPB-006 - Compatibility shims and import migration

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`, `apps/server/src/anima_server/services/memory`, `apps/server/tests`
- Parent: `MPB-000`
- Depends on: `MPB-003`, `MPB-004`, `MPB-005`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-07-03-memory-package-boundary-hardening.md
- Created: 2026-07-03 17:12 MYT
- Updated: 2026-07-03 17:12 MYT
- Started:
- Completed:

## Goal

Move safe memory-facing imports to `services.memory` while preserving compatibility shims for existing `services.agent` callers.

## Deliverables

- Import migrations for low-risk memory-facing code.
- Compatibility shims where public `services.agent` imports already exist.
- Import-rule tests to prevent new direct imports when a memory facade exists.

## Acceptance

- Existing tests pass after import migration.
- New memory-facing code paths use `services.memory`.
- Existing public `services.agent` import paths do not break.
- Import-rule tests are narrow and allow intentional legacy implementation modules.

## Activity Log

- 2026-07-03 17:12 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
