# PCF-010 - Retained-catalog garbage collection and key retirement

- Status: backlog
- Priority: P0
- Scope: Core catalog/object maintenance, cryptographic deletion, key retirement, Security UI
- Parent: `PCF-000`
- Depends on: `PCF-008`, accepted retention policy, verified current backup, no active transfer/rotation/migration
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-10-retained-catalog-garbage-collection-and-cryptographic-key-retirement`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-12 17:34 MYT
- Started:
- Completed:

## Goal

Safely reclaim unreachable catalog/object revisions and retire decrypt-only key material through an authenticated, resumable, backup-aware maintenance flow.

## Deliverables

- Explicit restore-window and pin policy with dry-run/doctor inventory.
- Authenticated mark/quarantine/sweep with Core-wide maintenance locking and crash-safe resume.
- Honest cryptographic-deletion reporting across local history and known backups.
- User-only `purge(trash_id, expected_trash_revision, confirmation)` for already-trashed content with recent reauthentication and one-use bound confirmation.
- Gated FRK/Object-DEK retirement API, Security UI, and reopen/restore verification.

## Acceptance

- No reachable or pinned catalog/object/key is removed under concurrency, corruption, crash, transfer, rotation, or migration tests.
- Pruning is idempotent and a changed `fs/HEAD` aborts/recomputes before deletion.
- Key retirement requires zero retained references, verified active-generation backup, and successful passphrase/recovery reopen before and after slot removal.
- UI clearly distinguishes local pruning from SSD/backup erasure and requires explicit irreversible confirmation.
- Live objects, stale trash revisions, unconfirmed recursive inventories, ANIMA/client callers, active-operation pins, and replayed confirmations cannot purge; wrapped Object DEKs retire only after zero local retained references are proven.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created as separately gated post-cutover maintenance.
- 2026-07-12 17:34 MYT - Added the concrete user-reauthenticated trash purge boundary and adversarial authorization/precondition tests.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Do not combine with PCF-008; coordinate retention and backup approval first.
