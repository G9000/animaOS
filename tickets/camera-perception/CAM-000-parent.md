# CAM-000 - Camera Perception Capability Module Parent Tracker

- Status: in_progress
- Priority: P1
- Scope: `apps/server`, `apps/desktop`, `packages/standard-templates`, `docs/prds/perception`, `docs/superpowers/plans`, `tickets/camera-perception`
- Depends on: `ACM-000`
- Owner: Codex
- PRD: docs/prds/perception/camera-perception-v1.md
- Plan: docs/superpowers/plans/2026-06-29-camera-perception.md
- Created: 2026-06-29 15:35 MYT
- Updated: 2026-06-29 15:41 MYT
- Started: 2026-06-29 15:35 MYT
- Completed:

## Goal

Track the optional `perception.camera` capability module. Camera perception must be enabled by the FastAPI capability registry, not required by Brain System and not implemented in the external Bun integration service.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `CAM-001` | Perception capability naming and architecture contract | `done` | none |
| `CAM-002` | Built-in `perception.camera` capability manifest | `backlog` | `ACM-001`, `ACM-002`, `CAM-001` |
| `CAM-003` | Desktop sensor bridge and consent UI | `backlog` | `ACM-004`, `ACM-005`, `CAM-002` |
| `CAM-004` | Server perception host and gated agent tool | `backlog` | `ACM-003`, `ACM-005`, `CAM-002`, `CAM-003` |
| `CAM-005` | Manual chat camera capture | `backlog` | `CAM-003` |
| `CAM-006` | Audit, retention, and visual-memory boundary | `backlog` | `CAM-004`, `CAM-005` |
| `CAM-007` | Tests, docs, and final validation | `backlog` | `CAM-002`, `CAM-003`, `CAM-004`, `CAM-005`, `CAM-006` |

## Deliverables

- Camera perception PRD framed as an optional Perception Mod.
- Built-in `perception.camera` capability manifest with safe defaults.
- Desktop camera bridge that owns camera permissions and consent.
- Server perception host that performs transient analysis and deletes raw frames.
- Manual chat camera snapshots using the existing image attachment path.
- Audit and retention rules that do not silently create durable visual memories.
- Tests and documentation for capability, desktop, and server boundaries.

## Acceptance

- Fresh install has no agent camera capability visible by default.
- `perception.camera` can be enabled/disabled through the FastAPI capability registry.
- Agent-requested capture is disabled by default even when the capability exists.
- Default consent mode is `ask_each_time`.
- Raw desktop bridge primitive is hidden from direct model tool selection.
- Non-vision models fail before any frame capture request.
- Agent-requested raw frames are deleted after analysis.
- Manual snapshots appear as deliberate user chat attachments.
- Audit records contain no raw image bytes.
- Durable memory promotion remains explicit and compatible with Visual Memory Image Assets.

## Completed Tickets

- `CAM-001` - Perception capability naming and architecture contract

## Activity Log

- 2026-06-29 15:35 MYT - Parent tracker created and claimed by Codex.
- 2026-06-29 15:35 MYT - Implementation paused for plan review after user clarified plan-first workflow.
- 2026-06-29 15:41 MYT - Reframed initiative as optional perception capability.
- 2026-06-29 15:41 MYT - Reframed again as FastAPI-side `perception.camera` Agent Capability Module, not external `anima-mod`.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - docs/prds/perception/camera-perception-v1.md
  - docs/superpowers/plans/2026-06-29-camera-perception.md
  - tickets/camera-perception/CAM-000-parent.md
  - tickets/camera-perception/CAM-001-perception-capability-contract.md
  - tickets/camera-perception/CAM-002-perception-camera-capability.md
  - tickets/camera-perception/CAM-003-desktop-sensor-bridge.md
  - tickets/camera-perception/CAM-004-server-perception-host.md
  - tickets/camera-perception/CAM-005-manual-chat-camera-capture.md
  - tickets/camera-perception/CAM-006-audit-retention-memory-boundary.md
  - tickets/camera-perception/CAM-007-tests-docs-validation.md
- Notes:
  - Planning-only update; no validation commands run yet.
