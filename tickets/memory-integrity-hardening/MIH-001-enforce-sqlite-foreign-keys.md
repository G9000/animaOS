# MIH-001 - Enforce SQLite foreign keys

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/db`, `apps/server/src/anima_server/services/vault.py`, `apps/server/src/anima_server/services/eval_reset.py`
- Parent: none
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: none
- Created: 2026-07-19 03:34 MYT
- Updated: 2026-07-19 03:34 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Evidence: PR #112 review threads (5+ findings share this root cause).
