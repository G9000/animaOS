# MPB-007 - Docs, eval, and cutover checklist

- Status: backlog
- Priority: P2
- Scope: `docs/architecture/memory`, `apps/server/tests`, `tickets/memory-package-boundary`
- Parent: `MPB-000`
- Depends on: `MPB-006`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-07-03-memory-package-boundary-hardening.md
- Created: 2026-07-03 17:12 MYT
- Updated: 2026-07-03 17:12 MYT
- Started:
- Completed:

## Goal

Finalize memory package boundary docs, cutover tests, and parent tracker status after the boundary hardening work lands.

## Deliverables

- `docs/architecture/memory/memory-package-boundary.md`.
- Public package-surface smoke tests.
- Final parent tracker update.
- Completion checklist documenting remaining intentional legacy modules.

## Acceptance

- Docs explain what `services.memory` owns and what remains under `services.agent`.
- Package-surface smoke tests import the stable public modules.
- Parent tracker is marked done only after every MPB child ticket is done.
- Validation includes package-boundary tests, server lint, server build, and diff check.

## Activity Log

- 2026-07-03 17:12 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
