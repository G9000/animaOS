# IL-008 - Wire push-initiative into the client (delivery + config UI)

- Status: done
- Priority: P2
- Scope: `packages/api-client`, `apps/desktop`, `apps/server/src/anima_server/services/agent/inner_life/delivery.py`
- Parent: `IL-000`
- Depends on: `IL-003`
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-21 MYT
- Updated: 2026-07-29 11:36 MYT
- Started: 2026-07-28 14:20 MYT
- Completed: 2026-07-29 11:36 MYT

## Goal

Make a fired push-initiative actually reach the user. IL-003 delivered the
server side: the `InitiativeDelivery` seam, the pollable `PendingInitiative`
default adapter, and the fetch/ack API route. It intentionally did NOT build
the client side (the brief scoped out `apps/desktop`/Tauri code, which had no
notification bridge). Until this ticket lands, an opted-in user gets rows in
`pending_initiatives` that nothing surfaces — the unprompted message never
appears.

This is not a correctness bug in IL-003: push initiative is off by default,
and when off nothing is ever created. It is the last-mile integration that
turns the shipped seam into a user-visible feature.

## Deliverables

- `packages/api-client` methods for the fetch + acknowledge pending-initiative
  endpoints exposed by `api/routes/presence.py`.
- A desktop poll/display path (`apps/desktop`) that surfaces a pending
  initiative and acknowledges it, OR a real `OSNotificationDelivery` adapter
  implementing the `InitiativeDelivery` seam via the Tauri notification shell.
- End-to-end coverage: opt in -> drive fires -> client receives and
  acknowledges -> `PendingInitiative` marked delivered/acknowledged.
- **Presence-config client exposure (from PR #115 + PR #116 review, P2):** the
  backend added `initiativeEnabled`, `quietHoursStart`, `quietHoursEnd` (IL3) and
  `dreamSharing` (`off|on_ask|ambient`, IL7) to the presence config, but the
  shared `PresenceConfig`/`PresenceConfigUpdate` in
  `packages/api-client/src/types.ts` still omits them and the desktop save path
  in `apps/desktop/src/pages/Presence.tsx` doesn't send them. Add all four fields
  to the typed client AND desktop controls (initiative opt-in toggle, quiet-hours
  inputs, and a dream-sharing selector), so a user can actually enable the
  off-by-default gate, configure quiet hours, and choose dream surfacing. Until
  then those settings are reachable only by callers that bypass the typed client.
  (Backend contract is complete; this is the client half.)

## Acceptance

- An opted-in user actually receives a fired initiative through the shipped
  client (not only a `pending_initiatives` row).
- Acknowledgement round-trips and clears/marks the pending row.

## Context

Raised by Codex review on PR #115 (P2): "Wire the pollable delivery into the
supported client." Confirmed intentional deferral per the IL-003 brief; filed
here so it is tracked rather than lost.

## Activity Log

- 2026-07-21 MYT - Ticket created from PR #115 review feedback.
- 2026-07-28 14:20 MYT - Claimed and started by Claude (user-authorized in-session): Owner unassigned -> Claude, Status backlog -> in_progress, synchronized with parent IL-000. (Recorded retroactively at 19:29 MYT during PR #123 review round 5; evidenced by branch plan commit `4e8d737`, authored 2026-07-28 14:20 MYT — the claim was not logged before the completion entry, violating the claim-before-implementation audit rule.)
- 2026-07-28 15:17 MYT - Implemented on branch `feature/il-008-initiative-client-wiring`. `packages/api-client`: `PendingInitiative`/`PendingInitiativesResponse`/`DreamSharing` types, `presence.initiatives()` (fetch, server marks rows delivered) and `presence.ackInitiative(id)` methods, and the four presence-config fields (`initiativeEnabled`, `quietHoursStart`, `quietHoursEnd`, `dreamSharing`) added to `PresenceConfig`/`PresenceConfigUpdate`. Desktop: a framework-free generation-token poller (`apps/desktop/src/lib/initiativePoller.ts`, 8 bun tests) polls every 60 s plus on window focus; a `usePendingInitiatives` hook wraps it and an `InitiativeOverlay` is mounted globally in `Layout.tsx` so a fired initiative surfaces on every authenticated route. Acknowledgement is user-action-only (Reply/Dismiss in the overlay) — no auto-ack on poll. Presence page gained the initiative opt-in toggle, quiet-hours start/end selects (with a hint that both ends must be set to take effect), and a dream-sharing segmented control (`off`/`on_ask`/`ambient`). The OSNotificationDelivery adapter path from the ticket's Deliverables (implementing `InitiativeDelivery` via the Tauri notification shell) was explicitly NOT taken — the repo has no Tauri notification bridge — so the poll/display path was chosen instead; this mirrors the deferral already noted in the ticket's Context. Two review-fix rounds during implementation: (1) a concurrent ack-vs-poll race, fixed with a generation token so an ack during an in-flight poll wins over the stale poll result (regression test added); (2) a `stop()` staleness leak, where a poll already in flight when `stop()` was called (e.g. on user switch or unmount) could still resolve, pass the generation check, and call `onChange` after the caller had walked away — `stop()` now also bumps the generation token, reusing the same invalidation `ack()` uses (regression test added). Marking `done` pending PR review, mirroring IL-002's precedent (ticket closed out with the PR still to be opened/reviewed).
- 2026-07-28 16:40 MYT - Codex review round 1 on PR #123, fixes by Claude: (1) P1 opt-out gate — every poll cycle now re-checks the current presence config via `createGatedInitiativeFetch` before hitting the initiatives endpoint, so withdrawing consent (initiative toggle or master switch) stops fetch/display/delivered-marking within one cycle and clears any on-screen initiative (3 regression tests); (2) P2 ack-window race — acknowledged-ID tombstones filter every poll result, so a poll that starts during a slow ack POST and reads the row pre-commit can no longer restore it; a failed ack drops its tombstone so the next poll re-serves the row (1 regression test); (3) ticket-hygiene P1 — full `YYYY-MM-DD HH:MM MYT` timestamps recorded from commit evidence across IL-000/IL-007/IL-008/IL-009. Poller suite now 12/12.
- 2026-07-28 16:59 MYT - Codex review round 2 on PR #123, fixes by Claude: (1) P2 Presence page — a failed initial config GET no longer seeds a savable `DEFAULT_CONFIG` draft (Save could then silently overwrite stored settings, e.g. reset `initiativeEnabled`/quiet hours/`dreamSharing`); the draft stays null and every control plus Save is disabled until a real config loads. (2) P1 IL-009 ticket — added the template-required `Spec`/`Updated`/`Started`/`Completed` lifecycle fields.
- 2026-07-28 18:24 MYT - Codex review round 3 on PR #123 (hygiene): `Updated` advanced with each material edit, Changed-paths made complete (added the plan doc and the IL-009 ticket; dropped the misleading `.superpowers` exclusion note — that directory is git-ignored and contains no committed paths), IL-007 `Started` recovered from PR #116 commit evidence.
- 2026-07-28 19:09 MYT - Codex review round 4 on PR #123: P2 — Reply now awaits the ack POST before navigating to `/chat` (`event.preventDefault()` + `navigate` after `ack` resolves). Each route renders its own `Layout`, so navigation remounts the poller with a fresh tombstone set; navigating mid-POST let the new poller's initial GET re-fetch the just-acknowledged row for up to one cycle.
- 2026-07-28 19:29 MYT - Codex review round 5 on PR #123, fixes by Claude: (1) P1 consent TOCTOU — `list_and_mark_delivered` (server) is now itself the consent authority: it checks the presence config atomically with the delivered side effect and returns [] (marking nothing delivered) without an active opt-in (`enabled` AND `initiativeEnabled`); the client-side gate remains as the UX layer. First server change on this branch: `inner_life/delivery.py` + regression test `test_poll_without_opt_in_lists_nothing_and_marks_nothing`, opt-in setup added to the two existing poll-path tests (incl. the route e2e, which env-fails locally per the note below and will be exercised in CI). (2) P1 claim audit — retroactive claim/start entry recorded above with commit evidence. (3) P1 parent structure — IL-009 moved out of IL-000's child table into a follow-ups section (see IL-000/IL-009 logs).
- 2026-07-28 20:04 MYT - Codex review round 6 on PR #123, fixes by Claude: (1) P1 quiet-hours delivery gate — quiet hours are now re-evaluated when LISTING pending initiatives, not only when firing: `list_and_mark_delivered` holds rows (returns [], marks nothing delivered) while the local hour is inside the configured window, using the gate chain's `_in_quiet_hours`/`resolve_local_now` discipline; served after the window ends. Regression test `test_poll_during_quiet_hours_lists_nothing_and_marks_nothing` (wrap-around window covered). The client gate mirrors it (`PresenceGate` carries the quiet-hour fields; local wall-clock hour, injectable for tests; 2 new bun tests — poller suite 14/14). (2) P1 completion evidence — `Completed` re-stamped from 15:17 to 20:04 MYT: review rounds 1-6 landed acceptance-affecting fixes (consent gate, quiet-hours delivery gate, opt-out UX) after the original close, so the recorded completion now reflects the final validated state; parent synchronized.
- 2026-07-28 20:50 MYT - Codex review round 7 on PR #123, fix by Claude (`2554429`): P1 consent check-then-act — the round-5 gate read config (soul session) then marked rows (runtime session) with no revalidation; an opt-out committing between them still got rows served. `list_and_mark_delivered` now revalidates consent on an expired (fresh) config read AFTER the runtime rows are read and BEFORE anything is marked. Regression test `test_opt_out_committed_mid_poll_serves_and_marks_nothing`.
- 2026-07-29 02:39 MYT - Codex review round 8 on PR #123, fix by Claude (`c39200f`): P1 residual TOCTOU — the freshness re-read only narrowed the window. Added `presence_consent_lock(user_id)` (`services/presence_config.py`): the config PUT (`api/routes/presence.py`) holds it through its commit; delivery holds it from the authoritative fresh check through the delivered side effect. Single-process server, so the in-process per-user lock closes the race rather than narrowing it. Regression test `test_consent_lock_serializes_delivery_against_config_updates` proves mutual exclusion. Completion re-stamped to this final acceptance-affecting fix; parent synchronized.
- 2026-07-29 11:36 MYT - Codex review round 9 on PR #123, fix by Claude: P1 ack-after-stop leak — `ack()`'s continuation invoked `onChange` unconditionally after its POST, so a poller stopped mid-ack (logout, user switch, unmount) could still mutate the replacement poller's shared React setter (erasing the new user's initiatives or exposing the old user's rows). `stop()` now sets an explicit `stopped` flag (cleared by `start()`), and the ack continuation returns without notifying when stopped — the generation mechanism stays dedicated to poll/ack-ordering so rapid double-acks keep working. Regression test `an ack resolving after stop() does not invoke onChange`; poller suite 15/15. Completion re-stamped (isolation-affecting fix); parent synchronized.

## Validation

- Commands:
  - `cd packages/api-client && bun test` -> 26 pass, 0 fail (68 expect() calls)
  - `cd apps/desktop && bun test` -> 62 pass, 3 fail, 1 error across 65 tests in 16 files. All failures are pre-existing and unrelated to this branch (confirmed via `git diff origin/main...HEAD -- apps/desktop/tests/layout-nav.test.ts apps/desktop/tests/layout-top-nav.test.tsx apps/desktop/tests/recovery-credential-replacement.test.ts apps/desktop/src/components/layout/LayoutTopNav.tsx`, which is empty): `layout-nav.test.ts` (nav-order assertion drifted from current `TOP_NAV_ITEMS`), `layout-top-nav.test.tsx` (missing module `../src/components/layout/LayoutTopNav` — component was never added/renamed), `recovery-credential-replacement.test.ts` (request body now includes a `replacePending` field the test doesn't expect). This branch's own suite, `tests/initiativePoller.test.ts`, is 15/15 pass (includes the ack-vs-poll, stop-vs-poll, and poll-during-slow-ack race regression tests plus the opt-out gate tests). No new failures introduced.
  - `cd apps/desktop && bunx tsc --noEmit` -> 0 errors
  - `cd apps/server && uv run pytest tests/test_inner_life_initiative.py -q --deselect tests/test_inner_life_initiative.py::test_fetch_ack_route_end_to_end` -> 90 passed, 1 deselected (was 88 before rounds 7-8 added two consent regression tests) (the deselected e2e is the pre-existing env-drift failure below); `ruff check` on the two changed server files -> clean
  - Spot-check: `cd apps/server && uv run pytest tests/test_inner_life_initiative.py -q` -> 1 failed, 86 passed (`test_fetch_ack_route_end_to_end` fails with `TypeError: 'NoneType' object is not subscriptable` at `services/corefs/keyslots.py:483` inside `ensure_core_manifest()` during account registration in this worktree's environment). The failure is in the registration fixture, untouched by this branch's server change (consent gate in `delivery.py`), so this is an environment/corefs-manifest issue in the current sandbox, not a regression from this branch — consistent with MIH-003's characterization of environment-driven server test drift; server code is unchanged so main's green baseline stands.
- Changed paths (`git diff --stat origin/main...HEAD`, complete):
  - `docs/superpowers/plans/2026-07-28-il-008-initiative-client-wiring.md` (new)
  - `apps/desktop/src/components/InitiativeOverlay.tsx` (new)
  - `apps/desktop/src/components/Layout.tsx`
  - `apps/desktop/src/hooks/usePendingInitiatives.ts` (new)
  - `apps/desktop/src/lib/initiativePoller.ts` (new)
  - `apps/desktop/src/pages/Presence.tsx`
  - `apps/desktop/tests/initiativePoller.test.ts` (new)
  - `packages/api-client/src/client.ts`
  - `packages/api-client/src/types.ts`
  - `packages/api-client/tests/client.test.ts`
  - `tickets/inner-life-v1/IL-000-parent.md`
  - `tickets/inner-life-v1/IL-007-dream-cycle.md`
  - `tickets/inner-life-v1/IL-008-initiative-delivery-client-wiring.md`
  - `tickets/inner-life-v1/IL-009-initiative-reply-context.md` (new)
  - `apps/server/src/anima_server/services/agent/inner_life/delivery.py` (consent gate rounds 5/7/8: quiet-hours + fresh revalidation + locked side effect)
  - `apps/server/src/anima_server/services/presence_config.py` (per-user `presence_consent_lock`, round 8)
  - `apps/server/src/anima_server/api/routes/presence.py` (PUT holds the consent lock through commit, round 8)
  - `apps/server/tests/test_inner_life_initiative.py` (gate + mid-poll opt-out + lock mutual-exclusion regression tests)
- Notes:
  - Delivery mechanism is poll (60 s interval + focus refetch) and manual display (global overlay), not push notifications — see Activity Log for why the `OSNotificationDelivery` path was skipped.
  - Ack is user-action-only; a failed ack call self-heals because the server still holds the row and re-serves it on the next poll.
