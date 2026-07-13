# PCF-000 - Portable Core Filesystem

- Status: backlog
- Priority: P0
- Scope: `apps/server`, `apps/desktop`, `apps/animus`, `apps/local-runtime-daemon`, `apps/anima-mod`, `packages/anima-file-tools`, `packages/anima-corefs`, `packages/anima-core`, `packages/api-client`, migrations, architecture docs
- Parent: none
- Depends on: none
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-12 18:58 MYT
- Started:
- Completed:

## Goal

Define ANIMA CORE as animaOS's portable encrypted Soul-plus-CoreFS subsystem, make encrypted Core objects canonical for portable app content, reserve SQLCipher for ANIMA's internal continuity, and move disposable PostgreSQL state outside `.anima/`.

## Child Tickets

| Ticket | Title | Status | Depends on |
|---|---|---|---|
| PCF-001 | Filesystem key hierarchy and credential generations | backlog | none |
| PCF-002 | Shared file tools, immutable objects, catalogs, and CoreFS | backlog | PCF-001 |
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

- None.

## Activity Log

- 2026-07-12 06:07 MYT - Parent tracker and child backlog created from the approved implementation plan.
- 2026-07-12 15:45 MYT - Locked animaOS as the product and ANIMA CORE as the portable subsystem/export family, with independent Soul/CoreFS local recovery.
- 2026-07-12 16:01 MYT - Updated PCF-001/PCF-008 after independent review to define scoped recovery keys and crash-safe local transfer publication.
- 2026-07-12 17:34 MYT - Locked the Rust/Python boundary, shared Animus file-tool library, customizable stable-role folders, least-privilege client grants, and pinned Codex production reference.
- 2026-07-12 17:34 MYT - Closed final review gaps for structural-only `manage`, broker-derived package identity, user-reauthenticated trash purge, complete Apache license/NOTICE distribution, and Animus submodule staging.
- 2026-07-12 18:00 MYT - Added the linked full target-architecture graph for topology, startup/indexing, tool routing, operations, authorization, and transfer/recovery.
- 2026-07-12 18:58 MYT - Closed the final execution-readiness gaps for standalone CI/release notices, stable Notes/Conversations roots, and principal-aware CoreFS API authorization.

## Validation

- Commands:
  - documentation review only
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
  - Implementation has not started.
