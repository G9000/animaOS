# IL-005 - Forgetting as distillation (F7 extension)

- Status: backlog
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent/forgetting.py`, `apps/server/src/anima_server/services/agent/claims.py`, `apps/server/src/anima_server/models`
- Parent: `IL-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-15 17:10 MYT
- Started:
- Completed:

## Goal

Make passive forgetting distill instead of delete: decayed casual/transient/emotional-pattern items dissolve into semantic tendency claims plus a content-free tombstone, while anchored classes and user-initiated deletion keep existing F7 semantics.

## Deliverables

- Distillation step in the F7 decay path: affective/topical signature → `tendency` namespace claim, EMA-merged 0.85/0.15, `origin: distilled`.
- Tombstone rows retaining only memory class, affect label, and time range; content/embeddings/evidence cryptographically deleted.
- `forget_audit_log` mode `distilled`.
- User-initiated deletion also removes the item's tendency contribution (right-to-forget precedence).
- Class exemptions: identity, life_event, relationship never distill.

## Acceptance

- Distilled tendencies retrievable as semantic claims; tombstones verified content-free via export.
- Explicit deletion removes tendency contributions (test).
- Exempt classes follow unchanged F7 behavior (regression tests pass).

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
