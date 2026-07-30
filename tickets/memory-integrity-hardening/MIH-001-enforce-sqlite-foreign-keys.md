# MIH-001 - Enforce SQLite foreign keys

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/db`, `apps/server/src/anima_server/services/vault.py`, `apps/server/src/anima_server/services/eval_reset.py`
- Parent: none
- Depends on: none
- Owner: Claude
- PRD: none
- Plan: none
- Created: 2026-07-19 03:34 MYT
- Updated: 2026-07-30 21:43 MYT
- Started: 2026-07-30 17:40 MYT
- Completed: 2026-07-30 21:43 MYT

## Goal

Make the schema's `ondelete="CASCADE"` declarations actually fire on the per-user SQLite soul databases, or explicitly decide they never will and remove the reliance on them.

## Context

SQLite does not enforce foreign keys unless `PRAGMA foreign_keys = ON` is issued per connection, and the codebase never issues it (only WAL/busy_timeout/cipher pragmas in `db/session.py`). Every `ondelete="CASCADE"` in `models/agent_runtime.py` is therefore decorative. The IL-004/IL-005 reviews (#108, #112) turned up a series of findings that are all this same root cause:

- Vault import bulk-deletes `MemoryClaim` without cascading to `MemoryClaimEvidence` (fixed by hand in #112).
- Eval reset must delete `tendency_contributions` before claims/items or they orphan (fixed by hand in #112).
- Direct `DELETE /api/memory/{id}/items/{id}` orphans `tendency_contributions` because the `tombstone_item_id` cascade doesn't fire (fixed by hand in #112).
- The unique `tombstone_item_id` constraint can then collide with reused rowids after an orphaning reset.

Each was patched individually. Enabling FK enforcement would collapse this entire class.

## Deliverables

- A per-connection `PRAGMA foreign_keys = ON` on the SQLite soul (and runtime, if SQLite) engines via a SQLAlchemy `connect` event, gated to the sqlite dialect.
- A full-suite pass proving nothing relied on the current non-enforcement (some manual delete-ordering may now be redundant, some may surface latent bugs — audit both).
- A decision recorded either way: enforce (and simplify the now-redundant manual cascades) or document that FKs are intentionally unenforced and keep the manual compensation as the contract.

## Acceptance

- FK-dependent deletes cascade correctly on SQLite (or a documented decision not to enforce).
- No regression in the pre-existing baseline; new FK-cascade tests for the claim/tendency/evidence chains.

## Activity Log

- 2026-07-19 03:34 MYT - Ticket created from IL-004/IL-005 review findings.

- 2026-07-30 17:40 MYT - Claimed and started by Claude (branch
  `mih-001-enforce-sqlite-fks`).
- 2026-07-30 18:05 MYT - Decision: ENFORCE. `PRAGMA foreign_keys = ON`
  added to all three `_make_engine` SQLite connect listeners (after
  `PRAGMA key` on the SQLCipher path — statements before the key fail).
  The full-suite audit surfaced exactly two latent reliances, both fixed:
  (1) the direct item-delete route left superseded predecessors as hidden
  zombie rows; enforced SET NULL resurrected them, so the route now walks
  the supersession chain like forget_memory already documents (evidence,
  tendency scrub, reconsolidation logs, vector/index removal per chain
  item); (2) vault restore inserts payloads in snapshot order, not
  FK-dependency order — restore now defers constraint checking to COMMIT
  (`PRAGMA defer_foreign_keys = ON`, transaction-scoped), so order stops
  mattering while an inconsistent snapshot still fails loudly. The
  existing manual delete-ordering compensations are KEPT as defense in
  depth. 4 regression tests in `tests/test_sqlite_fk_enforcement.py`.

- 2026-07-30 20:29 MYT - Completed: post-fix full suite green (3170/0).
  One test expectation legitimately changed with the chain-delete
  semantics (test_memory_writes_update_rust_index now asserts index
  deletes for the whole supersede chain, documented in-test).

- 2026-07-30 21:43 MYT - PR #132 review round 1 (2 P1s — both real
  data-loss holes in the enforcement rollout), completion re-stamped:
  (1) a memories-scope vault restore bulk-deleted users, whose ON DELETE
  CASCADE now executes immediately (defer_foreign_keys defers checks,
  not actions) and destroyed the preserved tables — scoped restores now
  upsert user rows field-wise with no delete; (2) Alembic batch_alter
  rebuilds (copy-create-DROP-rename) fired the old parent's cascades
  into child tables mid-upgrade — the migration runner now disables FKs
  via the raw DBAPI cursor (a SQLAlchemy-level pragma would autobegin a
  transaction and silently no-op) and restores enforcement in a finally.
  Regression tests: an intermediate-revision (20260316_0001) seeded
  upgrade preserving agent_steps/agent_messages through the 0002 batch
  rebuild, and a memories-scope restore preserving threads/tasks while
  merging the user row. Suite evidence refreshed below.

## Validation

- Commands:
  - `uv run pytest tests/test_sqlite_fk_enforcement.py` — 6 passed
  - `uv run pytest tests/test_vault.py` — 25 passed
  - Full-suite audit (enforcement on, pre-fix): 2 failed / 3167 passed —
    both failures triaged as latent non-enforcement reliances (see log)
  - Full suite on the round-1 head — **3172 passed, 0 failed, 10
    skipped**, run 2026-07-30 22:18 MYT
- Changed paths:
  - `apps/server/src/anima_server/db/session.py` (pragmas + FK-off migration runner)
  - `apps/server/src/anima_server/api/routes/memory.py`
  - `apps/server/src/anima_server/services/vault.py`
  - `apps/server/tests/test_sqlite_fk_enforcement.py` (new)
- Notes:
  - Evidence: PR #112 review threads (5+ findings share this root cause).
