# PCF-009 - Later-release Soul cleanup and legacy retirement

- Status: backlog
- Priority: P0
- Scope: SQLCipher schema purity and irreversible legacy retirement
- Parent: `PCF-000`
- Depends on: `PCF-008`, stable observation window, verified backup, explicit cleanup approval
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-9-later-release-soul-cleanup-and-legacy-retirement`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-23 06:40 MYT
- Started:
- Completed:

## Goal

In a separate later release, remove converted app data and obsolete ORM mappings from SQLCipher while preserving every internal Soul record and validated provenance.

## Deliverables

- Gated cleanup Alembic target.
- `SoulOwner` with preserved numeric compatibility ID plus opaque Core owner UUID.
- Enumerated retained FK rebuilds and Core-URI provenance conversion.
- App table/model removal, SQLCipher vacuum, and legacy recovery retirement.
- Removal of `memory_vectors`, experience centroid state, and every Soul-side embedding/cache column with unlock-scoped in-memory rebuilds.

## Acceptance

- Pre-apply authorization and post-apply retirement gates pass independently.
- Untouched table hashes match; transformed table hashes match deterministic expected conversions.
- `PRAGMA foreign_key_check` is clean and no `User`/`UserKey` consumers remain.
- SQLCipher schema allowlists contain no search index, embedding, chunk, queue, or access-log state.
- Passphrase/recovery unlock, Runtime rebuild, and clean transfer pass after cleanup.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created as an explicitly deferred later-release cleanup.
- 2026-08-23 06:40 MYT - Recorded three greenfield-unreachable legacy code
  paths found during PCF-008 closeout review as retirement candidates for this
  ticket: (1) `apps/server/src/anima_server/services/vault.py` retains the
  full V1 vault/capsule import-export machinery (about 2.9k lines) although
  only `export_database_snapshot`/`restore_database_snapshot` are still
  imported (by `db/user_store.py`); (2)
  `db/user_store.py:_migrate_legacy_shared_database_locked` still recognizes a
  legacy shared `anima.db` at the configured `database_url` and migrates it
  into the canonical Soul path at startup, before any release-field authority
  check; (3) `services/agent/inner_monologue.py:run_quick_reflection` falls
  back to the shared `SessionLocal` when no `db_factory` is passed (all
  production call sites pass one). None is reachable on a greenfield install;
  retire them here under this ticket's gates.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Do not combine with PCF-008.
