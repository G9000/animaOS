# SID-004 - Memory scope metadata design

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`, `docs/architecture/memory`
- Parent: `SID-000`
- Depends on: `SID-002`, `SID-003`
- Owner: unassigned
- PRD: docs/prds/memory/social-memory-identity-discovery-v1.md
- Plan: docs/superpowers/plans/2026-07-01-social-memory-identity-discovery.md
- Created: 2026-07-01 15:40 MYT
- Updated: 2026-07-01 15:40 MYT
- Started:
- Completed:

## Goal

Design memory metadata that separates who created a memory, who it is about, what scope produced it, and who may hear it.

## Deliverables

- Metadata proposal for `createdByPersonId`, `subjectPersonIds`, `sourceScope`, and `allowedAudience`.
- Rules for private, group shared, guest, abstract-only, and sealed scopes.
- Retrieval filtering requirements before prompt assembly and trace construction.
- Compatibility notes for `MemoryItem`, `MemoryEpisode`, knowledge graph, transcript recall, and runtime images.

## Acceptance

- A memory about Alex A cannot be retrieved for Alex B.
- A private memory learned from Leo about Alex remains Leo-private unless explicitly shared.
- Scope metadata supports future F14 group memory without collapsing into display-name matching.
- Trace and prompt-safety requirements are documented.

## Activity Log

- 2026-07-01 15:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
