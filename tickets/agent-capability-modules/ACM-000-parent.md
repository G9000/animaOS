# ACM-000 - Capability Module Standard Parent Tracker

- Status: backlog
- Priority: P1
- Scope: `apps/server`, `apps/desktop`, `docs/prds/capability-modules`, `docs/superpowers/plans`, `tickets/agent-capability-modules`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/capability-modules/agent-capability-modules-v1.md
- Plan: docs/superpowers/plans/2026-06-29-agent-capability-modules.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 17:58 MYT
- Started:
- Completed:

## Goal

Create the base Capability Module implementation foundation for the FastAPI agent service: the module contract, standard, registry/factory, lifecycle, and runtime plumbing that lets future body-system parts plug into the Brain System safely.

This parent is about the module platform itself, not Camera, Voice, Memory, or any specific capability. Brain System is the host runtime that loads/gates modules. Memory Core is a boundary that module outputs may use for durable promotion. Specific modules are consumers of this standard and should live in their own tickets/workstreams.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `ACM-001` | Capability module contract and registry foundation | `backlog` | none |
| `ACM-002` | Capability config/status API | `backlog` | `ACM-001` |
| `ACM-003` | Agent tool gating by capability policy | `backlog` | `ACM-001`, `ACM-002` |
| `ACM-004` | Desktop capability settings surface | `backlog` | `ACM-002` |
| `ACM-005` | Hidden desktop bridge channel for capability modules | `backlog` | `ACM-003`, `ACM-004` |
| `ACM-006` | Capability audit and retention policy | `backlog` | `ACM-002` |
| `ACM-007` | Architecture docs and validation | `backlog` | `ACM-001`, `ACM-002`, `ACM-003`, `ACM-004`, `ACM-005`, `ACM-006` |

## Deliverables

- Capability manifest contract.
- Capability registry/factory for registering module manifests.
- Version and compatibility rules for module parts.
- Server-side registry/config/status API.
- Tool gating through capability policy.
- Desktop settings surface for capability modules.
- Hidden bridge pattern for hardware-backed modules.
- Audit and retention rules.

## Acceptance

- Brain System remains the host runtime, not a module implemented by this ticket.
- The standard supports optional modules that can be installed, enabled, disabled, upgraded, or removed.
- Specific modules such as camera, voice, memory, action, and presence are examples/consumers, not the base deliverable.
- Agent-visible tools are derived from enabled module policy.
- Hidden bridge primitives are never direct model tools.
- Desktop can display capability status and configuration.
- Capability audit records do not contain raw sensitive payloads.

## Activity Log

- 2026-06-29 15:41 MYT - Parent tracker created for internal FastAPI-side capability module architecture.
- 2026-06-29 17:47 MYT - Reframed parent tracker as base Capability Module standard/factory rather than specific module implementation.
- 2026-06-29 17:52 MYT - Removed doc-only child tickets from the parent tracker; parent now tracks implementation work for the base module standard only.
- 2026-06-29 17:54 MYT - Reset parent status to backlog because implementation has not started; clarified initiative as module implementation foundation, contract, and standard.
- 2026-06-29 17:58 MYT - Cleaned architecture doc paths so the capability module folder contains module standard, usage, and capability-family docs only.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/agent-capability-modules/README.md
  - tickets/agent-capability-modules/ACM-000-parent.md
  - docs/architecture/agent/capability-modules/README.md
  - docs/architecture/agent/brain-system.md
  - docs/architecture/agent/capability-modules/module-contract.md
  - docs/architecture/agent/capability-modules/lifecycle-and-gating.md
  - docs/architecture/agent/capability-modules/desktop-bridges.md
  - docs/architecture/agent/capability-modules/module-families.md
  - docs/architecture/agent/capability-modules/perception-camera.md
  - docs/architecture/agent/capability-modules/body-system-doctrine.md
  - docs/architecture/agent/capability-modules/runtime-flow.md
  - docs/architecture/agent/capability-modules/data-boundaries.md
  - docs/architecture/agent/capability-modules/perception.md
  - docs/architecture/memory/memory-core-boundary.md
  - docs/architecture/agent/capability-modules/voice-core.md
  - docs/architecture/agent/capability-modules/action-local.md
  - docs/architecture/agent/capability-modules/presence-core.md
  - docs/architecture/system/external-integration-boundary.md
  - docs/architecture/agent/capability-modules/module-authoring-guide.md
  - docs/architecture/agent/capability-modules/body-system-diagrams.md
  - docs/architecture/agent/capability-modules/upgrade-and-compatibility.md
  - docs/architecture/README.md
  - docs/architecture/system/services.md
  - docs/prds/capability-modules/agent-capability-modules-v1.md
  - docs/superpowers/plans/2026-06-29-agent-capability-modules.md
- Notes:
  - Parent tracks implementation tickets only; implementation has not started.
