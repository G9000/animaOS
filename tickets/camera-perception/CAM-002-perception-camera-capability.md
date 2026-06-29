# CAM-002 - Built-in `perception.camera` capability manifest

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `CAM-000`
- Depends on: `ACM-001`, `ACM-002`, `CAM-001`
- Owner: unassigned
- PRD: docs/prds/perception/camera-perception-v1.md
- Plan: docs/superpowers/plans/2026-06-29-camera-perception.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 15:41 MYT
- Started:
- Completed:

## Goal

Create the optional built-in FastAPI-side capability manifest that owns camera perception setup, policy, safe defaults, and capability metadata.

## Deliverables

- `perception.camera` built-in capability manifest.
- Config schema/help text for capability settings.
- Config schema for consent, manual capture, agent-requested capture, retention, frame limits, and audit.
- Tests proving defaults are safe.

## Acceptance

- Module id is `perception.camera`.
- Agent-requested capture defaults to disabled.
- Consent mode defaults to `ask_each_time`.
- Retention defaults to `transient_only`.
- Help/setup copy explains camera permission and privacy boundaries.

## Activity Log

- 2026-06-29 15:41 MYT - Ticket created.
- 2026-06-29 15:41 MYT - Renamed ticket around `perception.camera` capability manifest terminology.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - not started
