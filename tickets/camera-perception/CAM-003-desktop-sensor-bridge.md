# CAM-003 - Desktop sensor bridge and consent UI

- Status: backlog
- Priority: P1
- Scope: `apps/desktop`
- Parent: `CAM-000`
- Depends on: `CAM-002`
- Owner: unassigned
- PRD: docs/prds/perception/camera-perception-v1.md
- Plan: docs/superpowers/plans/2026-06-29-camera-perception.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 15:41 MYT
- Started:
- Completed:

## Goal

Implement the desktop-owned camera bridge that captures one frame only when the user and capability policy allow it.

## Deliverables

- Camera capture helper.
- Hidden `camera_capture_frame` action bridge.
- Ask-each-time consent prompt.
- Visible camera activity state.
- Bridge connects only while user is unlocked and the capability is enabled.

## Acceptance

- Camera access never runs while logged out.
- Denying consent returns a clear error to the server.
- Hidden capture action is not advertised directly to the LLM.
- Capture respects max width, max height, MIME type, and quality.
- Permission/device errors are user-readable.

## Activity Log

- 2026-06-29 15:41 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - not started
