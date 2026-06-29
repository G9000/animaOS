# CAM-004 - Server perception host and gated agent tool

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `CAM-000`
- Depends on: `ACM-003`, `ACM-005`, `CAM-002`, `CAM-003`
- Owner: unassigned
- PRD: docs/prds/perception/camera-perception-v1.md
- Plan: docs/superpowers/plans/2026-06-29-camera-perception.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 15:41 MYT
- Started:
- Completed:

## Goal

Implement the generic server-side perception host that exposes `view_camera_snapshot` only when the `perception.camera` capability is enabled.

## Deliverables

- Hidden action schema filtering or equivalent capability boundary.
- Async tool support if needed by the perception host.
- Capability-gated `view_camera_snapshot` tool.
- Transient frame validation, temp-file lifecycle, model analysis, and deletion.
- Handling for missing desktop bridge and non-vision model configuration.

## Acceptance

- Fresh install does not expose `view_camera_snapshot`.
- Tool is unavailable when `perception.camera` is disabled.
- Non-vision models fail before desktop capture is requested.
- Invalid frame payloads are rejected.
- Temp frame is removed after analysis.
- The main agent receives only a text perception report.

## Activity Log

- 2026-06-29 15:41 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - not started
