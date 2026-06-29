# VMI-007 - Legacy backfill, docs, and final validation

- Status: done
- Priority: P2
- Scope: `apps/server`, `docs/architecture`, `tickets/visual-memory-image-assets`
- Parent: `VMI-000`
- Depends on: `VMI-005`, `VMI-006`
- Owner: Codex
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 13:02 MYT
- Started: 2026-06-29 12:33 MYT
- Completed: 2026-06-29 13:02 MYT

## Goal

Provide an idempotent migration path for existing chat image attachments and update documentation so future agents understand the visual memory model.

## Deliverables

- Backfill helper for legacy `content_json.attachments`.
- Tests for idempotent backfill, missing-file reporting, and fetch compatibility.
- Architecture doc updates for storage, indexing, OCR/text extraction capability detection, retrieval, proactive behavior, deletion, and future PDF/video/GIF extension boundaries.
- Final validation recorded in this ticket and parent tracker.

## Acceptance

- Running backfill more than once does not duplicate image assets or links.
- Missing legacy files are reported without aborting the full backfill.
- Architecture docs explain the central store and chat link model.
- Architecture docs explain that PDFs keep using the document pipeline, GIFs are v1 image assets without frame-level analysis, and video/timecoded media indexing is future work.
- Full repo validation is run or any environment-sensitive blocker is recorded with exact command output.

## Activity Log

- 2026-06-29 10:53 MYT - Ticket created.
- 2026-06-29 11:30 MYT - Added docs requirement for OCR capability detection and future media extension boundaries.
- 2026-06-29 12:33 MYT - Claimed by Codex after completing `VMI-006`; starting legacy backfill and docs.
- 2026-06-29 13:02 MYT - Added idempotent legacy chat-image backfill, architecture docs, docs-sync plan correction, and final validation records.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_backfill.py -q` - 2 passed.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_deletion.py -q` - 4 passed.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_asset_models.py apps/server/tests/test_image_assets.py apps/server/tests/test_chat_image_assets.py apps/server/tests/test_image_indexing.py apps/server/tests/test_image_retrieval_context.py apps/server/tests/test_proactive_image_memory.py apps/server/tests/test_image_backfill.py apps/server/tests/test_chat_attachments.py apps/server/tests/test_dashboard_api.py::test_proactive_notice_endpoint_accepts_custom_instruction apps/server/tests/test_document_store.py -q` - 67 passed, 3 warnings.
  - `bun test packages/api-client/tests/client.test.ts` - 16 passed.
  - `bun run lint` - passed.
  - `git diff --check` - passed.
  - `bun run build` - passed for server, desktop, and `cargo check -p animus`.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run db:server:current` - passed.
  - `uv run --project . alembic -c alembic_runtime.ini heads` from `apps/server` - `018_image_assets (head)`.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - 1689 passed, 1 skipped, 1 order-dependent failure in `test_creation_flow.py::test_agent_can_generate_thinking_monologue_draft`.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_creation_flow.py::test_agent_can_generate_thinking_monologue_draft -q` - 1 passed.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_creation_flow.py -q` - 28 passed.
- Changed paths:
  - apps/server/src/anima_server/services/images/backfill.py
  - apps/server/tests/test_image_backfill.py
  - docs/architecture/agent/agent-runtime.md
  - docs/architecture/agent/document-processing.md
  - docs/architecture/memory/memory-system.md
  - docs/CHANGELOG.md
  - docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Notes:
  - Docs-code-sync still exits 1 due pre-existing broken path references in older docs/plans, but after correcting this plan it reports no visual-memory docs or plan matches.
  - Direct runtime Alembic `current` timed out against the configured local PostgreSQL URL; the revision graph check reports `018_image_assets` as head.
