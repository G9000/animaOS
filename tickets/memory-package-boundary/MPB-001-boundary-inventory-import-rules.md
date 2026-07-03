# MPB-001 - Boundary inventory and import rules

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/memory`, `apps/server/src/anima_server/services/agent`, `docs/architecture/memory`
- Parent: `MPB-000`
- Depends on: PR #67
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-07-03-memory-package-boundary-hardening.md
- Created: 2026-07-03 17:12 MYT
- Updated: 2026-07-03 17:12 MYT
- Started:
- Completed:

## Goal

Create the inventory and rules that define which memory APIs belong under `services.memory` and which legacy implementation paths may remain under `services.agent`.

## Deliverables

- Import inventory test for memory-adjacent modules.
- Initial allowlist for legacy `services.agent` memory imports.
- Architecture doc section describing memory package ownership rules.

## Acceptance

- Inventory identifies current memory implementation modules without changing runtime behavior.
- Rules state that new memory-facing APIs should be exposed from `services.memory`.
- Compatibility with existing `services.agent` imports remains explicit.
- No parallel versioned memory package path is introduced.

## Activity Log

- 2026-07-03 17:12 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
