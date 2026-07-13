# SID-001 - Runtime audience context contract

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`, `packages/api-client/src`
- Parent: `SID-000`
- Depends on: agent runtime foundation
- Owner: unassigned
- PRD: docs/prds/memory/social-memory-identity-discovery-v1.md
- Plan: docs/superpowers/plans/2026-07-01-social-memory-identity-discovery.md
- Created: 2026-07-01 15:40 MYT
- Updated: 2026-07-01 15:40 MYT
- Started:
- Completed:

## Goal

Define the future-compatible runtime context shape for speaker, audience, conversation scope, group, and memory policy without changing current single-user behavior.

## Deliverables

- Runtime contract proposal for `speakerPersonId`, `audiencePersonIds`, `conversationScope`, `groupId`, and `memoryPolicy`.
- Mapping from current owner-only requests to default owner-private policy.
- Notes on prompt, tool, trace, and dry-run propagation points.

## Acceptance

- Contract distinguishes speaker from subject and audience.
- Contract does not rely on display names as memory boundaries.
- Existing single-user behavior can be represented without migration.
- Follow-up implementation work is clearly separated from this design ticket.

## Activity Log

- 2026-07-01 15:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
