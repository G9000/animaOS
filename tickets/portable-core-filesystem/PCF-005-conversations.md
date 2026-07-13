# PCF-005 - Canonical threads, messages, and transcript merge

- Status: backlog
- Priority: P0
- Scope: `apps/server` chat/thread/transcript services
- Parent: `PCF-000`
- Depends on: `PCF-003`
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-5-canonical-threads-messages-and-transcript-merge`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-12 18:58 MYT
- Started:
- Completed:

## Goal

Make versioned encrypted message segments canonical and merge active PostgreSQL, legacy SQLCipher, and transcript history safely.

## Deliverables

- Message event/CAS/segment implementation.
- Canonical visible-message projection excluding internal execution state.
- Thread APIs and display backed by CoreFS.
- Idempotent active/archive merge with conflict quarantine.
- Inactive shadow-catalog validation until PCF-008; after activation Runtime messages retain references/operational metadata, not duplicate plaintext visible bodies.
- Unique `core.conversations` root binding with default `owner=shared`/`agentAccess=manage` and stable-folder resolution across rename, move, and restart.

## Acceptance

- Rollover, ordering, edit/delete conflict, terminal deletion, corruption, and concurrency tests pass.
- User-visible history and attachments survive migration.
- Tool calls, thinking, prompts, traces, and retrieval internals are absent from canonical messages.
- Pre-cutover writes remain on legacy authority; no unmarked CoreFS mutation can bypass the global cutover marker.
- Thread list, display, append, edit/delete, and reactivation resolve the same `core.conversations` folder ID after rename, move, and restart.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 18:58 MYT - Added the `core.conversations` root, shared/manage defaults, and rename/move/restart acceptance coverage.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim after PCF-003 is done.
