# CAM-001 - Perception capability naming and architecture contract

- Status: done
- Priority: P1
- Scope: `docs/prds/perception`, `docs/superpowers/plans`, `tickets/camera-perception`
- Parent: `CAM-000`
- Depends on: none
- Owner: Codex
- PRD: docs/prds/perception/camera-perception-v1.md
- Plan: docs/superpowers/plans/2026-06-29-camera-perception.md
- Created: 2026-06-29 15:35 MYT
- Updated: 2026-06-29 15:41 MYT
- Started: 2026-06-29 15:35 MYT
- Completed: 2026-06-29 15:41 MYT

## Goal

Define what the feature is called and where it belongs architecturally before implementation.

## Deliverables

- Capability Modules architecture.
- Perception family name.
- `perception.camera` module id.
- Client-assisted capability architecture.
- Updated parent tracker and implementation plan.

## Acceptance

- The PRD says camera perception is optional and capability-enabled.
- The plan separates capability registry, desktop bridge, server host, and memory boundary work.
- The parent tracker lists implementation tickets in dependency order.

## Activity Log

- 2026-06-29 15:35 MYT - Initial camera perception tickets created.
- 2026-06-29 15:41 MYT - Reframed feature as optional perception capability and completed planning contract ticket.
- 2026-06-29 15:41 MYT - Updated contract to FastAPI-side `perception.camera` Agent Capability Module.

## Validation

- Commands:
  - not run; documentation/ticket planning only
- Changed paths:
  - docs/prds/perception/camera-perception-v1.md
  - docs/superpowers/plans/2026-06-29-camera-perception.md
  - tickets/camera-perception/CAM-000-parent.md
  - tickets/camera-perception/CAM-001-perception-capability-contract.md
- Notes:
  - No code validation required for this planning-only ticket.
