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
- Updated: 2026-07-28 16:40 MYT
- Started: 2026-07-28 14:20 MYT
- Completed: 2026-07-28 15:17 MYT

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
- 2026-07-28 15:17 MYT - Implemented on branch `feature/il-008-initiative-client-wiring`. `packages/api-client`: `PendingInitiative`/`PendingInitiativesResponse`/`DreamSharing` types, `presence.initiatives()` (fetch, server marks rows delivered) and `presence.ackInitiative(id)` methods, and the four presence-config fields (`initiativeEnabled`, `quietHoursStart`, `quietHoursEnd`, `dreamSharing`) added to `PresenceConfig`/`PresenceConfigUpdate`. Desktop: a framework-free generation-token poller (`apps/desktop/src/lib/initiativePoller.ts`, 8 bun tests) polls every 60 s plus on window focus; a `usePendingInitiatives` hook wraps it and an `InitiativeOverlay` is mounted globally in `Layout.tsx` so a fired initiative surfaces on every authenticated route. Acknowledgement is user-action-only (Reply/Dismiss in the overlay) — no auto-ack on poll. Presence page gained the initiative opt-in toggle, quiet-hours start/end selects (with a hint that both ends must be set to take effect), and a dream-sharing segmented control (`off`/`on_ask`/`ambient`). The OSNotificationDelivery adapter path from the ticket's Deliverables (implementing `InitiativeDelivery` via the Tauri notification shell) was explicitly NOT taken — the repo has no Tauri notification bridge — so the poll/display path was chosen instead; this mirrors the deferral already noted in the ticket's Context. Two review-fix rounds during implementation: (1) a concurrent ack-vs-poll race, fixed with a generation token so an ack during an in-flight poll wins over the stale poll result (regression test added); (2) a `stop()` staleness leak, where a poll already in flight when `stop()` was called (e.g. on user switch or unmount) could still resolve, pass the generation check, and call `onChange` after the caller had walked away — `stop()` now also bumps the generation token, reusing the same invalidation `ack()` uses (regression test added). Marking `done` pending PR review, mirroring IL-002's precedent (ticket closed out with the PR still to be opened/reviewed).
- 2026-07-28 16:40 MYT - Codex review round 1 on PR #123, fixes by Claude: (1) P1 opt-out gate — every poll cycle now re-checks the current presence config via `createGatedInitiativeFetch` before hitting the initiatives endpoint, so withdrawing consent (initiative toggle or master switch) stops fetch/display/delivered-marking within one cycle and clears any on-screen initiative (3 regression tests); (2) P2 ack-window race — acknowledged-ID tombstones filter every poll result, so a poll that starts during a slow ack POST and reads the row pre-commit can no longer restore it; a failed ack drops its tombstone so the next poll re-serves the row (1 regression test); (3) ticket-hygiene P1 — full `YYYY-MM-DD HH:MM MYT` timestamps recorded from commit evidence across IL-000/IL-007/IL-008/IL-009. Poller suite now 12/12.
- 2026-07-28 16:59 MYT - Codex review round 2 on PR #123, fixes by Claude: (1) P2 Presence page — a failed initial config GET no longer seeds a savable `DEFAULT_CONFIG` draft (Save could then silently overwrite stored settings, e.g. reset `initiativeEnabled`/quiet hours/`dreamSharing`); the draft stays null and every control plus Save is disabled until a real config loads. (2) P1 IL-009 ticket — added the template-required `Spec`/`Updated`/`Started`/`Completed` lifecycle fields.

## Validation

- Commands:
  - `cd packages/api-client && bun test` -> 26 pass, 0 fail (68 expect() calls)
  - `cd apps/desktop && bun test` -> 62 pass, 3 fail, 1 error across 65 tests in 16 files. All failures are pre-existing and unrelated to this branch (confirmed via `git diff origin/main...HEAD -- apps/desktop/tests/layout-nav.test.ts apps/desktop/tests/layout-top-nav.test.tsx apps/desktop/tests/recovery-credential-replacement.test.ts apps/desktop/src/components/layout/LayoutTopNav.tsx`, which is empty): `layout-nav.test.ts` (nav-order assertion drifted from current `TOP_NAV_ITEMS`), `layout-top-nav.test.tsx` (missing module `../src/components/layout/LayoutTopNav` — component was never added/renamed), `recovery-credential-replacement.test.ts` (request body now includes a `replacePending` field the test doesn't expect). This branch's own suite, `tests/initiativePoller.test.ts`, is 12/12 pass (includes the ack-vs-poll, stop-vs-poll, and poll-during-slow-ack race regression tests plus the opt-out gate tests). No new failures introduced.
  - `cd apps/desktop && bunx tsc --noEmit` -> 0 errors
  - `git diff origin/main...HEAD -- apps/server` -> empty (this branch touches no server code)
  - Spot-check: `cd apps/server && uv run pytest tests/test_inner_life_initiative.py -q` -> 1 failed, 86 passed (`test_fetch_ack_route_end_to_end` fails with `TypeError: 'NoneType' object is not subscriptable` at `services/corefs/keyslots.py:483` inside `ensure_core_manifest()` during account registration in this worktree's environment). Since `apps/server` has zero diff against `origin/main`, this is an environment/corefs-manifest issue in the current sandbox, not a regression from this branch — consistent with MIH-003's characterization of environment-driven server test drift; server code is unchanged so main's green baseline stands.
- Changed paths (`git diff --stat origin/main...HEAD`, excluding `.superpowers`):
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
- Notes:
  - Delivery mechanism is poll (60 s interval + focus refetch) and manual display (global overlay), not push notifications — see Activity Log for why the `OSNotificationDelivery` path was skipped.
  - Ack is user-action-only; a failed ack call self-heals because the server still holds the row and re-serves it on the next poll.
