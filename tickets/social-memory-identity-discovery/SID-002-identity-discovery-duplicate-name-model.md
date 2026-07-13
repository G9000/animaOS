# SID-002 - Identity discovery and duplicate-name model

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`
- Parent: `SID-000`
- Depends on: `SID-001`
- Owner: unassigned
- PRD: docs/prds/memory/social-memory-identity-discovery-v1.md
- Plan: docs/superpowers/plans/2026-07-01-social-memory-identity-discovery.md
- Created: 2026-07-01 15:40 MYT
- Updated: 2026-07-01 15:40 MYT
- Started:
- Completed:

## Goal

Design the person identity model that lets Anima distinguish multiple people with the same display name using stable IDs, aliases, linked accounts, relationship labels, evidence, and confidence.

## Deliverables

- Person identity model proposal.
- Alias and linked-account rules.
- Merge and split audit requirements.
- Ambiguity handling rules for memory-sensitive turns.

## Acceptance

- Multiple people can share the same display name without sharing memory scope.
- Identity confidence is explicit.
- Low-confidence identity decisions require clarification when memory boundaries matter.
- Model is compatible with future F14 group memory.

## Activity Log

- 2026-07-01 15:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
