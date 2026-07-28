# IL-000 - Inner Life v1 parent tracker

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`, `apps/server/src/anima_server/models`, `apps/server/src/anima_server/main.py`
- Parent: none
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-28 20:04 MYT
- Started: 2026-07-15 16:55 MYT
- Completed: 2026-07-28 20:04 MYT

## Goal

Deliver Inner Life v1: continuous affect state with offline catch-up, drive-based push initiative, latent trace crystallization, forgetting as distillation, recall reconsolidation, and the dream cycle — per the PRD.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `IL-001` | Affect state vector with decay-to-baseline dynamics | `done` | none |
| `IL-002` | Presence tick loop and offline catch-up | `done` | `IL-001` |
| `IL-003` | Drive accumulators and push initiative channel | `done` | `IL-001`, `IL-002` |
| `IL-004` | Latent trace buffer and crystallization | `done` | none |
| `IL-005` | Forgetting as distillation (F7 extension) | `done` | none |
| `IL-006` | Recall reconsolidation (F2 extension) | `done` | none |
| `IL-007` | Dream cycle (F5 extension) | `done` | `IL-001`, `IL-002`, `IL-006` |
| `IL-008` | Wire push-initiative into the client (delivery + config UI) | `done` | `IL-003` |

## Follow-ups Beyond v1 Scope

- `IL-009` - Initiative reply context carry-over (`backlog`, depends on `IL-008`): optional polish filed from the IL-008 final review. Tracked as a standalone follow-up, not a child of this closed v1 parent — the v1 acceptance ("all child tickets done") is judged over the child table above.

## Completed Ticket History

- 2026-07-15 19:55 MYT - `IL-001` done: affect state vector (branch feature/il-001-affect-state-vector), review-approved after one fix round.
- 2026-07-18 03:46 MYT - `IL-004` done: latent trace crystallization (branch feature/il-004-latent-traces), two review rounds + final review, all findings fixed.
- 2026-07-18 19:29 MYT - `IL-005` done: forgetting as distillation (branch feature/il-005-distillation), task review clean, final review fixes applied.
- 2026-07-19 05:10 MYT - `IL-006` done: recall reconsolidation (branch feature/il-006-reconsolidation), task review approved after 1 fix round, final whole-branch review (controller-run, spend limit) clean.
- 2026-07-16 13:30 MYT - `IL-002` done: presence tick loop and offline catch-up (branch feature/il-002-presence-tick), pending review.
- 2026-07-21 07:27 MYT - `IL-003` done: drive accumulators and push initiative (branch feature/il-003-initiative, merged main as `5e38dbf`, PR #115). First user-visible Inner Life behavior. Task review + adversarial whole-branch review (Critical wiring fix: per-user soul-store resolution) + 8 further PR review rounds, all fixed with regression tests. Client-side delivery wiring intentionally deferred, filed as `IL-008`.
- 2026-07-23 02:16 MYT - `IL-007` done: dream cycle (branch feature/il-007-dream-cycle, squash-merged main as `bc7363c`, PR #116). Many PR review rounds hardened right-to-forget dream scrubbing, field-encrypted dream source_refs, bounded dream attempts, and dream-sharing gating. Client-side dream surfacing folded into `IL-008` scope notes.
- 2026-07-28 15:17 MYT - `IL-008` done: push-initiative client wiring (branch feature/il-008-initiative-client-wiring, PR to follow). Poll/display path chosen over an `OSNotificationDelivery` adapter — no Tauri notification bridge exists in the repo. One task-review fix round each on the poller's concurrent ack-vs-poll race (generation-token fix) and the `stop()` staleness leak (in-flight poll invalidation on stop), both with regression tests. Last child ticket; Inner Life v1 scope is now complete end-to-end pending this PR's merge.

## Deliverables

- IL-001 Affect state vector (IL1)
- IL-002 Presence tick and offline catch-up (IL2)
- IL-003 Drive accumulators and push initiative (IL3)
- IL-004 Latent trace crystallization (IL4)
- IL-005 Forgetting as distillation (IL5)
- IL-006 Recall reconsolidation (IL6)
- IL-007 Dream cycle (IL7)
- IL-008 Client wiring for push initiative + presence-config UI (last-mile delivery)

## Acceptance

- All child tickets done.
- PRD section 6 success metrics measurable (instrumentation in place).
- No user-visible behavior without presence_config opt-in; all outputs provenance-traceable.

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-15 17:10 MYT - Added implementation plan reference, child ticket status table, and completed-ticket history per review feedback.
- 2026-07-28 14:10 MYT - Close-out pass: IL-007 marked done (was stale after PR #116 merged), IL-008 added to the child table, parent moved to in_progress. Remaining scope: `IL-008` (client delivery + presence-config UI).
- 2026-07-28 14:20 MYT - `IL-008` claimed and started by Claude (child backlog -> in_progress, Owner -> Claude) as one child-and-parent transaction; parent remained in_progress. (Recorded retroactively at 19:29 MYT during PR #123 review round 5; evidenced by branch plan commit `4e8d737`, authored 2026-07-28 14:20 MYT.)
- 2026-07-28 15:17 MYT - `IL-008` (last child ticket) done; all eight child tickets are now `done`. Parent moved to `done`. Inner Life v1 — continuous affect state with offline catch-up, drive-based push initiative (now client-visible), latent trace crystallization, forgetting as distillation, recall reconsolidation, and the dream cycle — is complete end-to-end once the IL-008 PR merges.
- 2026-07-28 16:13 MYT - `IL-009` filed as a backlog follow-up from the IL-008 final whole-branch review (Reply-context carry-over UX gap). This is post-v1 polish, not a blocker: it does not change the parent's `done` status or IL-008's completion.
- 2026-07-28 19:29 MYT - Codex review round 5 on PR #123: moved `IL-009` out of the child table into a "Follow-ups Beyond v1 Scope" section so the `done` parent is structurally consistent (no backlog child inside the acceptance-bearing table); IL-009's own metadata now records the lineage without a parent link.
- 2026-07-28 20:04 MYT - Codex review round 6 on PR #123: `Completed` re-stamped from 15:17 to 20:04 MYT on this parent and `IL-008` — review rounds landed acceptance-affecting fixes (consent + quiet-hours delivery gates) after the original close, so completion now postdates the final validated state, per the completion-evidence rule.

## Validation

- Each child ticket carries its own Validation section with the exact commands and results for its scope; this parent has no independent validation beyond those. See:
  - `tickets/inner-life-v1/IL-001-affect-state-vector.md`
  - `tickets/inner-life-v1/IL-002-presence-tick-offline-catchup.md`
  - `tickets/inner-life-v1/IL-003-drive-accumulators-push-initiative.md`
  - `tickets/inner-life-v1/IL-004-latent-trace-crystallization.md`
  - `tickets/inner-life-v1/IL-005-forgetting-as-distillation.md`
  - `tickets/inner-life-v1/IL-006-recall-reconsolidation.md`
  - `tickets/inner-life-v1/IL-007-dream-cycle.md`
  - `tickets/inner-life-v1/IL-008-initiative-delivery-client-wiring.md` (most recent: api-client 26/26, desktop suite clean of new failures with `initiativePoller.test.ts` 14/14, `tsc --noEmit` 0 errors, server consent-gate change validated with `tests/test_inner_life_initiative.py` 88 passed)
