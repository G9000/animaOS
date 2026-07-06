# OKF-003 - OKF import and export

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion/okf.py`
- Parent: `OKF-000`
- Depends on: `OKF-001`, `OKF-002`
- Owner: unassigned
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-06 23:23 MYT
- Started:
- Completed:

## Goal

Implement OKF-compatible markdown bundle import and export for compiled knowledge concepts.

## Deliverables

- OKF serializer and parser.
- Export layout with `index.md`, `log.md`, and one markdown file per concept.
- Import path that creates or updates concepts and concept links.
- Tests for frontmatter, unknown fields, unknown types, relative links, and round trips.

## Acceptance

- Every exported concept has required OKF `type` frontmatter.
- Optional OKF fields round-trip: `title`, `description`, `resource`, `tags`, `timestamp`.
- Unknown frontmatter fields are preserved.
- Unknown concept types do not fail import.
- Bundle-relative markdown links are preserved and resolved when possible.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Expected focused command: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_okf_import_export.py -q`
