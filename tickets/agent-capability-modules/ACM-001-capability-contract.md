# ACM-001 - Capability module contract and registry foundation

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `ACM-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/capability-modules/agent-capability-modules-v1.md
- Plan: docs/superpowers/plans/2026-06-29-agent-capability-modules.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 17:48 MYT
- Started:
- Completed:

## Goal

Define the server-side capability module contract and registry foundation.

This ticket creates the base standard/factory for capability modules. It should not implement Camera, Voice, Memory, Action, Presence, or any concrete capability module.

## Deliverables

- Capability type definitions.
- Manifest validation.
- Registry foundation for registering capability manifests.
- Reference/fixture manifests only where useful for tests.
- Version and compatibility fields.

## Acceptance

- Registry can load and validate a capability manifest.
- Registry can reject invalid ids, unsupported contract versions, and model-visible hidden bridge primitives.
- Registry supports required/optional flags as manifest properties without implementing concrete modules.
- No concrete camera, voice, memory, action, or presence module is required for this ticket.

## Activity Log

- 2026-06-29 15:41 MYT - Ticket created.
- 2026-06-29 17:48 MYT - Reframed ticket as the base module contract/registry foundation instead of concrete built-in module manifests.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - not started
