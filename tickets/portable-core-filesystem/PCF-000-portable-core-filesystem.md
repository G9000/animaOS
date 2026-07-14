# PCF-000 - Portable Core Filesystem

- Status: in_progress
- Priority: P0
- Scope: `apps/server`, `apps/desktop`, `apps/animus`, `apps/local-runtime-daemon`, `apps/anima-mod`, `packages/anima-file-tools`, `packages/anima-corefs`, `packages/anima-core`, `packages/api-client`, migrations, architecture docs
- Parent: none
- Depends on: none
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-14 21:22 MYT
- Started: 2026-07-13 21:27 MYT
- Completed:

## Goal

Define ANIMA CORE as animaOS's portable encrypted Soul-plus-CoreFS subsystem, make encrypted Core objects canonical for portable app content, reserve SQLCipher for ANIMA's internal continuity, and move disposable PostgreSQL state outside `.anima/`.

## Child Tickets

| Ticket | Title | Status | Depends on |
|---|---|---|---|
| PCF-001 | Filesystem key hierarchy and credential generations | done | none |
| PCF-002 | Shared file tools, immutable objects, catalogs, and CoreFS | in_progress | PCF-001 |
| PCF-003 | Machine-local Runtime and progressive indexing | backlog | PCF-002 |
| PCF-004 | Diary, folders, drafts, and notes | backlog | PCF-003 |
| PCF-005 | Canonical threads, messages, and transcript merge | backlog | PCF-003 |
| PCF-006 | Gallery, attachments, documents, and knowledge sources | backlog | PCF-003, PCF-005 |
| PCF-007 | Account profile, tasks, preferences, and credentials | backlog | PCF-004, PCF-006 |
| PCF-008 | Cutover, transfer, and first-release validation | backlog | PCF-001 through PCF-007 |
| PCF-009 | Later-release Soul cleanup and legacy retirement | backlog | PCF-008 plus observation/approval gate |
| PCF-010 | Retained-catalog GC and key retirement | backlog | PCF-008 plus retention/backup approval gate |

## Deliverables

- Approved PRD, storage/security specifications, and implementation plan.
- Linked full target-architecture graph covering topology, startup/indexing, tools, operations, authorization, and local transfer/recovery.
- Ten independently reviewable implementation slices.
- Local cold/live ANIMA CORE transfer, full/Soul/CoreFS recovery, removable-media streaming, and clean-machine rebuild validation.
- Separate later-release destructive cleanup gate.
- Separate retained-catalog pruning and cryptographic key-retirement gate.
- Dedicated Rust `anima-file-tools` and `anima-corefs` libraries shared safely with Animus HostFS through explicit backends.
- Customizable stable-ID folders, ownership/access policy, recoverable trash, and folder-scoped client extension grants.

## Acceptance

- Every child ticket is `done`.
- Copied `.anima/` restores Soul plus portable user-owned content without Runtime.
- SQLCipher contains only approved Soul tables after PCF-009; retained objects/catalogs and decrypt-only keys retire only through PCF-010.
- Full backend, desktop, migration, transfer, lock, recovery, and health validation is recorded.
- Product and technical naming consistently distinguish animaOS, ANIMA CORE, Soul, CoreFS, and Runtime.
- Codex-derived production patterns are selectively adapted with pinned provenance/Apache-2.0 notices; CoreFS improves multi-file patching to one-generation atomic publication.

## Completed Tickets

- PCF-001 - Filesystem key hierarchy and credential generations (latest PR #90 review follow-up completed 2026-07-14 18:27 MYT).

## Activity Log

- 2026-07-12 06:07 MYT - Parent tracker and child backlog created from the approved implementation plan.
- 2026-07-12 15:45 MYT - Locked animaOS as the product and ANIMA CORE as the portable subsystem/export family, with independent Soul/CoreFS local recovery.
- 2026-07-12 16:01 MYT - Updated PCF-001/PCF-008 after independent review to define scoped recovery keys and crash-safe local transfer publication.
- 2026-07-12 17:34 MYT - Locked the Rust/Python boundary, shared Animus file-tool library, customizable stable-role folders, least-privilege client grants, and pinned Codex production reference.
- 2026-07-12 17:34 MYT - Closed final review gaps for structural-only `manage`, broker-derived package identity, user-reauthenticated trash purge, complete Apache license/NOTICE distribution, and Animus submodule staging.
- 2026-07-12 18:00 MYT - Added the linked full target-architecture graph for topology, startup/indexing, tool routing, operations, authorization, and transfer/recovery.
- 2026-07-12 18:58 MYT - Closed the final execution-readiness gaps for standalone CI/release notices, stable Notes/Conversations roots, and principal-aware CoreFS API authorization.
- 2026-07-13 20:47 MYT - Reconciled the initiative with merged main: preserved current rich Journal HTML/media behavior, inventoried new document parsing/tool/context/compiler persistence boundaries, expanded migration tests/docs, and made catalog benchmark fixtures unambiguous.
- 2026-07-13 21:27 MYT - Began PCF-001 implementation in an isolated worktree after a green Rust and crypto/recovery baseline.
- 2026-07-14 12:00 MYT - Completed PCF-001 after six implementation/review hardening commits, clean requirements and quality re-reviews, full credential/recovery regressions, native crypto verification, migration/build/lint checks, and independent final hardening tests. PCF-002 is now unblocked.
- 2026-07-14 15:45 MYT - Reopened PCF-001 for a current-head review fix covering cross-Core vault import key-hierarchy rebinding; PCF-002 remains blocked until the follow-up is validated and reviewed.
- 2026-07-14 16:03 MYT - Completed and validated the PCF-001 cross-Core vault follow-up with a 25-test vault suite plus lint/build gates; PCF-002 is unblocked again.
- 2026-07-14 17:09 MYT - Reopened PCF-001 for a current-head cross-Core import review regression covering destination user identity, manifest-index consistency, and password-rotation safety; PCF-002 is blocked pending validation and re-review.
- 2026-07-14 17:12 MYT - Completed and validated destination-account rebinding for cross-Core imports with a red/green different-username regression, all 25 vault tests, lint, build, and diff gates; PCF-002 is unblocked again.
- 2026-07-14 17:25 MYT - Reopened PCF-001 for a current-head registration-crash follow-up covering orphaned Soul keyslots across legacy password rotation; PCF-002 is blocked pending validation and re-review.
- 2026-07-14 18:02 MYT - Completed and validated atomic orphaned-backfill cleanup during legacy password rotation with red/green crash recovery coverage, 91 credential tests, lint, build, and diff gates; PCF-002 is unblocked again.
- 2026-07-14 18:15 MYT - Reopened PCF-001 for a current-head stale-native-extension startup compatibility follow-up; PCF-002 is blocked pending validation and re-review.
- 2026-07-14 18:27 MYT - Completed and validated native-first manifest publication with a durable Python fallback for stale extensions: 48 CoreFS keyslot tests, 10 binding/permission tests, lint, build, and diff gates passed; PCF-002 is unblocked again.
- 2026-07-14 19:45 MYT - Started PCF-002 from merged `main`; the first reviewable slice establishes shared bounded file-operation contracts and migrates Animus HostFS onto them before CoreFS catalog work.
- 2026-07-14 21:12 MYT - Completed and validated PCF-002's first reviewable slice: shared bounded file tools, explicit Animus HostFS reuse, typed preflighted patch planning, and standalone Codex attribution/release gates. PCF-002 remains in progress for the CoreFS object/catalog implementation.
- 2026-07-14 21:22 MYT - Opened PCF-002 slice-one PR #91 and requested substantive Codex review; the parent remains in progress pending review/merge and later PCF-002 slices.

## Validation

- Commands:
  - `git diff --check`
  - scoped Markdown link/anchor, plan action/path, and docs-drift checks
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_diary_api.py apps/server/tests/test_document_parsing.py apps/server/tests/test_document_tools.py apps/server/tests/test_contextual_rerank.py apps/server/tests/test_html_ingestion.py apps/server/tests/test_structured_document.py apps/server/tests/test_web_fetch.py apps/server/tests/test_knowledge_autocompile.py apps/server/tests/test_retrieval_eval.py apps/server/tests/test_pdf_workflow_checkpoints.py -q`
  - `bun test apps/desktop/tests/journal-content.test.ts apps/desktop/tests/journal-html.test.ts`
- Changed paths:
  - `docs/prds/portable-core-filesystem-v1.md`
  - `docs/superpowers/specs/2026-07-12-portable-core-filesystem-design.md`
  - `docs/superpowers/specs/2026-07-12-portable-core-key-hierarchy-design.md`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
  - `docs/prds/README.md`
  - `docs/CHANGELOG.md`
  - `docs/architecture/README.md`
  - `docs/architecture/system/anima-core-filesystem.md`
  - `tickets/portable-core-filesystem/`
- Notes:
  - PCF-001 is complete; PCF-002 is the next implementation slice.
  - Merged-main reconciliation validation: 155 backend tests and 5 desktop Journal tests passed; all 13 scoped Markdown links/anchors and all plan action/path declarations passed. The repository-wide docs checker still reports pre-existing drift plus expected missing paths for planned PCF files; the PCF scope has no broken-link or non-planned-path finding.
