# OKF-009 - API client and desktop knowledge library

- Status: backlog
- Priority: P2
- Scope: `packages/api-client`, `apps/desktop/src/pages/knowledge`, `apps/desktop/src/components/knowledge`
- Parent: `OKF-000`
- Depends on: `OKF-007`, `OKF-008`
- Owner: unassigned
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-06 23:23 MYT
- Started:
- Completed:

## Goal

Expose source and concept reads through the API client and add a minimal desktop knowledge library surface.

## Deliverables

- API client types for sources, artifacts, spans, concepts, links, lint findings, and OKF import/export results.
- API client methods for source/concept read, compile, search, import/export, and lint.
- Desktop knowledge library route with source list, concept list, concept markdown body, citations, lint action, and OKF export action.
- Desktop build/typecheck validation.

## Acceptance

- Client methods are typed and compile.
- Desktop surface is a working library view, not a landing page.
- User can inspect sources and concepts.
- Concept view shows source citations.
- Lint and export actions are wired to API client methods or clear disabled states if backend route gating requires it.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Expected focused command: `bun run build:desktop`
