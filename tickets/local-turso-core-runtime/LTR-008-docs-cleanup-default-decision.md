# LTR-008 - Documentation, cleanup, and default decision

- Status: backlog
- Priority: P1
- Scope: `docs`, `apps/server`, `tickets/local-turso-core-runtime`
- Parent: `LTR-000`
- Depends on: `LTR-004`, `LTR-006`, `LTR-007`
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md
- Plan: docs/superpowers/plans/2026-06-29-local-turso-core-runtime.md
- Created: 2026-06-29 15:50 MYT
- Updated: 2026-06-29 15:50 MYT
- Started:
- Completed:

## Goal

Update architecture, operations, and rollout documentation after the Turso migration work reaches its validation gates, then decide whether Turso becomes the default local engine.

## Deliverables

- Updated architecture docs for local Turso Soul and Runtime.
- Updated setup/config docs and environment variable references.
- Health and troubleshooting notes for migration, rollback, and PostgreSQL bypass.
- Decision record for default engine status.
- Cleanup plan for any deprecated PostgreSQL lifecycle code if default cutover is approved.

## Acceptance

- Docs explain that Turso Cloud is not required.
- Docs preserve the Soul/Runtime/Archive mental model.
- Rollback instructions are present.
- Default engine decision is explicit: default, experimental, or blocked.
- Parent tracker reflects final child ticket states.

## Activity Log

- 2026-06-29 15:50 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none

