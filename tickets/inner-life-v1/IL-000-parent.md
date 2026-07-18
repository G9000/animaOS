# IL-000 - Inner Life v1 parent tracker

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`, `apps/server/src/anima_server/models`, `apps/server/src/anima_server/main.py`
- Parent: none
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-16 13:30 MYT
- Started:
- Completed:

## Goal

Deliver Inner Life v1: continuous affect state with offline catch-up, drive-based push initiative, latent trace crystallization, forgetting as distillation, recall reconsolidation, and the dream cycle — per the PRD.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `IL-001` | Affect state vector with decay-to-baseline dynamics | `done` | none |
| `IL-002` | Presence tick loop and offline catch-up | `done` | `IL-001` |
| `IL-003` | Drive accumulators and push initiative channel | `backlog` | `IL-001`, `IL-002` |
| `IL-004` | Latent trace buffer and crystallization | `done` | none |
| `IL-005` | Forgetting as distillation (F7 extension) | `done` | none |
| `IL-006` | Recall reconsolidation (F2 extension) | `done` | none |
| `IL-007` | Dream cycle (F5 extension) | `backlog` | `IL-001`, `IL-002`, `IL-006` |

## Completed Ticket History

- 2026-07-15 19:55 MYT - `IL-001` done: affect state vector (branch feature/il-001-affect-state-vector), review-approved after one fix round.
- 2026-07-18 03:46 MYT - `IL-004` done: latent trace crystallization (branch feature/il-004-latent-traces), two review rounds + final review, all findings fixed.
- 2026-07-18 19:29 MYT - `IL-005` done: forgetting as distillation (branch feature/il-005-distillation), task review clean, final review fixes applied.
- 2026-07-19 05:10 MYT - `IL-006` done: recall reconsolidation (branch feature/il-006-reconsolidation), task review approved after 1 fix round, final whole-branch review (controller-run, spend limit) clean.
- 2026-07-16 13:30 MYT - `IL-002` done: presence tick loop and offline catch-up (branch feature/il-002-presence-tick), pending review.

## Deliverables

- IL-001 Affect state vector (IL1)
- IL-002 Presence tick and offline catch-up (IL2)
- IL-003 Drive accumulators and push initiative (IL3)
- IL-004 Latent trace crystallization (IL4)
- IL-005 Forgetting as distillation (IL5)
- IL-006 Recall reconsolidation (IL6)
- IL-007 Dream cycle (IL7)

## Acceptance

- All child tickets done.
- PRD section 6 success metrics measurable (instrumentation in place).
- No user-visible behavior without presence_config opt-in; all outputs provenance-traceable.

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-15 17:10 MYT - Added implementation plan reference, child ticket status table, and completed-ticket history per review feedback.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
