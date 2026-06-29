# CAM-006 - Audit, retention, and visual-memory boundary

- Status: backlog
- Priority: P1
- Scope: `apps/server`, `apps/desktop`
- Parent: `CAM-000`
- Depends on: `CAM-004`, `CAM-005`
- Owner: unassigned
- PRD: docs/prds/perception/camera-perception-v1.md
- Plan: docs/superpowers/plans/2026-06-29-camera-perception.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 15:41 MYT
- Started:
- Completed:

## Goal

Make perception use inspectable without silently creating durable memories or retaining raw frames.

## Deliverables

- Lightweight audit event records for request, approval/denial, result status, retention mode.
- No raw image bytes in audit records.
- Retention mode enforcement for `transient_only`.
- Documentation of future Visual Memory Image Assets integration.

## Acceptance

- Agent-requested frames are deleted after analysis.
- Audit records contain metadata only.
- Manual chat snapshots are treated as deliberate user attachments.
- Durable memory promotion is explicit and not automatic.

## Activity Log

- 2026-06-29 15:41 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - not started
