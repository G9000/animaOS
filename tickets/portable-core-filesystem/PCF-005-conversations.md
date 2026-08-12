# PCF-005 - Canonical threads, messages, and transcript merge

- Status: in_progress
- Priority: P0
- Scope: `apps/server` chat/thread/transcript services
- Parent: `PCF-000`
- Depends on: `PCF-003`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-5-canonical-threads-messages-and-transcript-merge`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-13 04:00 MYT
- Started: 2026-08-13 04:00 MYT
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
- 2026-08-13 04:00 MYT - Codex claimed PCF-005 after rechecking that PCF-003 is done and no existing branch, worktree, owner, or activity entry claims the ticket. Started on local stacked branch `codex/pcf-005-conversations` from published PCF-004 head `83f2490058f9e8c6565716cc5eee0b44bed530d3` in `/Users/julio/animaOS`; PCF-004 remains independently open on its cost-deferred package-evidence gate.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - PCF-003 is done. PCF-005 is active on a local stacked branch; no PCF-005 publication, PR creation, review request, monitoring, or merge is currently authorized.
