# PCF-004 - Diary, folders, drafts, and notes

- Status: in_progress
- Priority: P1
- Scope: `apps/server` diary/CoreFS, `apps/desktop` Journal
- Parent: `PCF-000`
- Depends on: `PCF-003`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Spec: `docs/superpowers/specs/2026-08-02-corefs-resumable-preparation-design.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-4-diary-folders-drafts-and-notes-vertical-slice`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-02 17:10 MYT
- Started: 2026-08-02 04:06 MYT
- Completed:

## Goal

Make encrypted sanitized-HTML diary objects plus CoreFS folder, draft, and note objects canonical while preserving the existing rich Journal API and UI behavior without leaving embedded media inline.

## Deliverables

- Versioned sanitized-HTML diary codec, Markdown/sanitized-HTML note codecs, and idempotent SQLCipher conversion; plain diary text becomes escaped HTML paragraphs without lossy Markdown conversion.
- First-class empty/custom folder support; unique `core.journal` and `core.notes` stable-role bindings; default `owner=user`/`agentAccess=write`; and attachment CoreFS URIs.
- Inline `data:` media decoded under MIME/size limits, deduplicated into encrypted CoreFS binary objects, and replaced with stable CoreFS URIs before atomic publication.
- Journal drafts migrated out of plaintext localStorage.
- Backend and Bun desktop tests covering current `Journal.tsx`, content selection, HTML sanitization, covers, and attachments.

## Acceptance

- Existing diary data, folders, covers, and attachments round-trip with stable IDs/hashes.
- Current Tiptap formatting, attachment-only entries, cover-only entries, and valid inline images round-trip; canonical diary HTML contains no base64 `data:` URLs.
- Plain-text and HTML legacy bodies use the same versioned sanitization contract, and malformed/oversized embedded media cannot partially publish a diary revision.
- Empty folders survive migration.
- Journal still resolves after its root is renamed/moved, and ANIMA can read/write private diary content unless the user explicitly lowers access.
- Standalone Notes resolve through the same stable folder ID after rename, move, and restart; their root defaults to `owner=user`/`agentAccess=write`.
- Journal drafts are encrypted Core objects and UI behavior remains functional.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 17:34 MYT - Added stable Journal role and explicit private-diary ownership/access defaults.
- 2026-07-12 18:58 MYT - Added the `core.notes` root, ownership defaults, and rename/move/restart acceptance coverage.
- 2026-07-13 20:47 MYT - Reconciled migration with the merged Tiptap Journal: canonicalized sanitized HTML, extracted inline media to CoreFS objects, and added the current content/sanitizer helpers and tests to scope.
- 2026-08-02 04:06 MYT - Claimed PCF-004 from clean `main` at `51678d08680747d90ff0c03c0e091331456ae837` after PCF-003 completion. Executing the approved Task 4 plan directly in the main checkout per user direction, with test-first backend and Journal slices followed by independent specification and quality review.
- 2026-08-02 04:19 MYT - The first native integration pass proved the planned converter entry point did not exist: public mutation is intentionally frozen, the sequential crate-private shadow mutator cannot atomically publish a writing graph, and PyO3 exposes read/validation only. Removed the parallel encrypted-filesystem prototype, retained the green portable codec/migration groundwork in `7ac84178`, and corrected Task 4 to add one sealed session-scoped validation-batch API, deterministic native IDs, exact-head CAS, and role resolution while leaving public mutation frozen until PCF-008.
- 2026-08-02 17:01 MYT - Completed the bounded PCF-004 implementation and repeated independent specification/quality loops through `e30179bb`. Atomic inactive-catalog publication, production SQLCipher preparation, metadata/API parity, portable names, draft/media staging, sanitizer parity, stable-role lifecycle, exact reruns, 100 MiB attachments, and the public 20,000,000-character contract are green. Blocked on a material native security-protocol decision for legitimate writing corpora above 1 GiB: current Python/PyO3/Rust transport materializes the whole corpus, while prepared object tokens contain wrapped DEKs and physical identities only in memory. Safe bounded preparation requires an authenticated persistent preparation journal/head, restart recovery, abandonment/GC, rotation, and session-close semantics followed by one exact-head atomic finalization. Do not weaken atomicity or raise/remove bounds without an approved design.
- 2026-08-02 17:10 MYT - User approved the recommended authenticated persistent preparation protocol, clearing the design-decision blocker. Resumed PCF-004 without changing its original `Started:` timestamp and drafted the repository spec for bounded per-object preparation, encrypted `PREPARATION_HEAD` state, exact-CAS single-generation finalization, crash recovery, explicit abandonment, retention-gated GC, rotation exclusion, and bounded session close. Implementation remains gated on independent written-spec review and user approval of the committed document.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_diary_api.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_notes.py -q` (focused bands passed throughout; latest affected PCF-004 band `28 passed`)
  - `cargo test -p anima-corefs --lib --no-fail-fast` (`216 passed`, `1 ignored`)
  - `cargo test -p anima-core --lib` (all `218` tests passed across the final affected run)
  - `bun test apps/desktop/tests/journal-corefs.test.ts apps/desktop/tests/journal-draft-migration.test.ts apps/desktop/tests/journal-html.test.ts` (`8 passed` in the final affected run)
  - `bun run build` (passed)
  - `bun run lint:server` (passed)
  - `bun run check:repo` and `git diff --check` (passed)
- Changed paths:
  - `Cargo.lock`
  - `apps/desktop/src/pages/Journal.tsx`
  - `apps/desktop/src/pages/journal/{draft-migration.ts,html.ts}`
  - `apps/desktop/tests/{journal-corefs.test.ts,journal-draft-migration.test.ts,journal-html.test.ts}`
  - `apps/server/src/anima_server/api/routes/diary.py`
  - `apps/server/src/anima_server/schemas/diary.py`
  - `apps/server/src/anima_server/services/corefs/{diary_migration.py,formats.py,writing-sanitizer-v1.json}`
  - `apps/server/src/anima_server/services/sessions.py`
  - `apps/server/tests/{conftest.py,test_corefs_diary_migration.py,test_corefs_indexer.py,test_corefs_notes.py,test_diary_api.py}`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
  - `packages/anima-core/{Cargo.toml,src/ffi.rs}`
  - `packages/anima-corefs/src/{catalog/v2.rs,id.rs,transaction.rs}`
  - `packages/anima-corefs/src/logical/{backend.rs,mod.rs,path.rs,service.rs,wire.rs}`
  - `packages/anima-corefs/src/transaction/converter.rs`
  - `packages/anima-corefs/tests/{logical_path.rs,logical_snapshot.rs,opaque_id.rs,validation_batch.rs}`
  - `packages/api-client/src/{client.ts,types.ts}`
  - `tickets/portable-core-filesystem/{PCF-000-portable-core-filesystem.md,PCF-004-diary-notes.md}`
- Notes:
  - Legacy SQLCipher remains authoritative and `VALIDATION_HEAD` remains inactive; no partial or authoritative cutover occurred.
  - A full `bun run test` was attempted twice but remained compute-active beyond five-minute tool bounds without a summary; no repository-wide pass is claimed.
  - Strict Clippy remains blocked only by documented untouched baseline warnings outside the PCF-004 diff.
  - The protocol direction is approved; independent written-spec review and user approval of the committed document remain before implementation planning.
