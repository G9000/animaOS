# MPB-000 - Memory package boundary hardening parent tracker

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/memory`, `apps/server/src/anima_server/services/agent`, `packages/anima-core`, `docs/architecture/memory`
- Parent: none
- Depends on: PR #67 / SUM-005 / SUM-011
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-07-03-memory-package-boundary-hardening.md
- Created: 2026-07-03 17:12 MYT
- Updated: 2026-07-03 17:12 MYT
- Started:
- Completed:

## Goal

Turn the minimal `services.memory` package boundary into the durable ownership surface for memory contracts, facades, retrieval planning, and background memory services while keeping existing `services.agent` imports compatible.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `MPB-001` | Boundary inventory and import rules | `backlog` | PR #67 |
| `MPB-002` | Contract fixture parity | `backlog` | `MPB-001` |
| `MPB-003` | Temporal claims and graph facades | `backlog` | `MPB-002` |
| `MPB-004` | Retrieval plan execution boundary | `backlog` | `MPB-002` |
| `MPB-005` | Background memory service facades | `backlog` | `MPB-002` |
| `MPB-006` | Compatibility shims and import migration | `backlog` | `MPB-003`, `MPB-004`, `MPB-005` |
| `MPB-007` | Docs, eval, and cutover checklist | `backlog` | `MPB-006` |

## Deliverables

- Documented memory package ownership rules.
- Shared Python/Rust contract fixture parity.
- `services.memory` facades for temporal claims, temporal graph, retrieval planning, patterns, foresight, and procedural memory.
- Compatibility shims so existing `services.agent` imports continue to work.
- Import-rule tests that guide new memory work toward `services.memory`.
- Final architecture docs and cutover smoke tests.

## Acceptance

- New memory-facing code can import stable contracts from `anima_server.services.memory`.
- Existing production imports from `anima_server.services.agent` continue to work.
- Python and Rust parse the same memory contract fixtures.
- No parallel versioned memory package path is introduced.
- Tickets record validation and changed paths as work proceeds.
- Parent remains backlog or in progress until all child tickets are complete.

## Completed Tickets

- none

## Activity Log

- 2026-07-03 17:12 MYT - Parent tracker created for memory package boundary hardening.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - tickets/memory-package-boundary/README.md
  - tickets/memory-package-boundary/MPB-000-parent.md
  - tickets/memory-package-boundary/MPB-001-boundary-inventory-import-rules.md
  - tickets/memory-package-boundary/MPB-002-contract-fixture-parity.md
  - tickets/memory-package-boundary/MPB-003-temporal-claims-graph-facades.md
  - tickets/memory-package-boundary/MPB-004-retrieval-plan-execution-boundary.md
  - tickets/memory-package-boundary/MPB-005-background-memory-facades.md
  - tickets/memory-package-boundary/MPB-006-compatibility-shims-import-migration.md
  - tickets/memory-package-boundary/MPB-007-docs-eval-cutover.md
  - docs/superpowers/plans/2026-07-03-memory-package-boundary-hardening.md
- Notes:
  - Planning-only tracker. No runtime implementation has started.
