# CAM-005 - Manual chat camera capture

- Status: backlog
- Priority: P1
- Scope: `apps/desktop`, `packages/standard-templates`
- Parent: `CAM-000`
- Depends on: `CAM-003`
- Owner: unassigned
- PRD: docs/prds/perception/camera-perception-v1.md
- Plan: docs/superpowers/plans/2026-06-29-camera-perception.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 15:41 MYT
- Started:
- Completed:

## Goal

Let the user deliberately capture a webcam snapshot and send it as a normal chat image attachment.

## Deliverables

- Camera item in attachment menu.
- Manual capture flow into pending image attachments.
- Preview/removal behavior matching uploaded images.
- Disabled/hidden state when manual capture is off in the capability config.

## Acceptance

- Manual capture is user-triggered only.
- Captured frame appears in the pending attachment tray.
- Sending uses the existing chat image attachment pipeline.
- Removing the pending image revokes the preview object URL.

## Activity Log

- 2026-06-29 15:41 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - not started
