# OKF-003 - OKF import and export

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion/okf.py`
- Parent: `OKF-000`
- Depends on: `OKF-001`, `OKF-002`
- Owner: codex
- Model: 5.5
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 13:02 MYT
- Started: 2026-07-07 00:31 MYT
- Completed: 2026-07-07 00:33 MYT

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
- 2026-07-07 00:31 MYT - Claimed by Codex, set status to `in_progress`, and started OKF import/export tests.
- 2026-07-07 00:33 MYT - Added OKF bundle serializer/parser, permissive import, export layout, and link resolution.

- 2026-07-07 13:02 MYT - Ticket metadata normalized: status done, owner codex, model 5.5.

## Validation
- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_models.py apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_okf_import_export.py -q` - passed, 15 tests.
  - `uv run --project apps/server ruff check apps/server/src/anima_server/services/ingestion/okf.py apps/server/tests/test_okf_import_export.py` - passed.
- Changed paths:
  - `apps/server/src/anima_server/services/ingestion/okf.py`
  - `apps/server/tests/test_okf_import_export.py`
- Notes:
  - Import is permissive for unknown concept types and preserves unknown frontmatter fields in `frontmatter_json`.
