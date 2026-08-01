# IL-009 - Carry initiative context into the Reply round-trip

- Status: done
- Priority: P3
- Scope: `apps/desktop/src/components/InitiativeOverlay.tsx`, `apps/desktop/src/pages/chat`
- Parent: none
- Depends on: `IL-008`
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: none
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-28 16:13 MYT
- Updated: 2026-08-01 22:04 MYT
- Started: 2026-07-30 17:14 MYT
- Completed: 2026-08-01 22:04 MYT

## Goal

Clicking Reply on a pending initiative in the `InitiativeOverlay` acknowledges
it and navigates to `/chat`, but nothing carries the initiative's text into
the chat context — the user has to remember what the companion said and
reply from memory. Deliver a mechanism so the reply the user types actually
references what the companion said, e.g. one of:

- Prefill the chat composer with a quoted/paraphrased reference to the
  initiative text.
- Show a transient notice above the composer echoing the initiative that
  triggered the navigation.
- Seed the initiative text into the chat context so the model itself has it
  available even if the composer stays empty.

## Deliverables

- A carry-over path from `InitiativeOverlay`'s Reply action into
  `apps/desktop/src/pages/chat` so the initiative text (or a reference to it)
  is visible to the user and/or available to the model at the start of the
  reply.
- Coverage for the carry-over path (unit and/or integration, matching the
  existing test conventions in this area).

## Acceptance

- After clicking Reply on a pending initiative, the user lands on `/chat`
  with the initiative's content carried over in some visible or contextual
  form — not solely relying on the user's memory of the notification.

## Context

Raised by the IL-008 final whole-branch review as a UX gap in the
acknowledgement round-trip: the IL-008 overlay intentionally shipped without
this carry-over (Reply only acks + navigates), scoping it out as a follow-up
rather than blocking IL-008 on it.

## Activity Log

- 2026-07-28 16:13 MYT - Ticket created from the IL-008 final whole-branch review.
- 2026-07-28 16:59 MYT - Codex review round 2 on PR #123: added the template-required `Spec`/`Updated`/`Started`/`Completed` lifecycle fields.
- 2026-07-28 19:29 MYT - Codex review round 5 on PR #123: detached from `IL-000` (`Parent: none`) — the v1 parent is `done` and a backlog child inside its acceptance-bearing table made the tracker inconsistent. Lineage: filed from the IL-008 final review; IL-000 lists this under "Follow-ups Beyond v1 Scope".

- 2026-07-30 17:14 MYT - Claimed and started by Claude (branch
  `il-009-initiative-reply-context`).
- 2026-07-30 17:23 MYT - Implemented via the chat page's existing
  seeded-thread contract: Reply now navigates with
  `initiativeReplyState(current)` (new pure helper in
  `lib/initiativeReply.ts`) — the initiative text renders verbatim as the
  opening assistant message of a fresh thread AND rides into the user's
  first send as context messages, so it is visible to the user and
  available to the model. State is captured before the async ack so the
  overlay advancing to the next pending row mid-flight can't swap the
  seeded text. 3 tests.

- 2026-07-30 18:39 MYT - PR #131 review round 1 (two P1s). (1) Reopened:
  the 17:23 MYT completion had recorded validation via a scripted edit
  whose pattern missed this file's single-line Validation format, so the
  ticket was certified done with `not run yet` on disk — prior completion
  timestamp preserved here per the reopen routine; re-closed at this
  entry's timestamp with the evidence actually recorded below. (2) Fixed
  the in-place navigation gap: Reply while Chat is ALREADY mounted only
  updated location.state, which mount-time refs never re-read — the ack
  succeeded and the text vanished. Chat now classifies each navigation
  (`classifySeedNavigation`, pure + tested) and applies seed state once
  per location.key, deferring mid-stream arrivals until the stream
  settles rather than dropping them or swapping the thread live.

- 2026-07-30 19:38 MYT - PR #131 review round 2 (P1 + P2), completion
  re-stamped: (1) an in-place seed navigation now CLOSES the active
  server-side thread first (mirrors handleNewThread) — clearing only the
  client refs let the first reply's get_or_create_thread land in the
  still-active old conversation; the fresh thread is created on first
  send. (2) Seed contexts ACCUMULATE (`mergeSeedContexts`, pure +
  tested): a second Reply during the same stream, or while the first
  seeded thread is still unsent, adds its message instead of discarding
  the previously acked initiative's text. 25 desktop tests green.

- 2026-07-30 20:43 MYT - PR #131 review round 3 (P1), completion
  re-stamped: the in-place seed's thread closure was fire-and-forget, so
  a fast submit (e.g. an existing draft) could still reach the server
  while the old thread was active. Sends are now gated on the in-flight
  closure (ref-guarded, with a one-moment notice) until the close
  settles.

- 2026-07-30 21:59 MYT - PR #131 review round 4 (3 P1s), completion
  re-stamped: (1) handleSubmit consumed the seed refs BEFORE sendMessage
  could reject — a guard rejection then retried on the non-seed path and
  the model never saw the acked initiative; sendMessage now returns
  whether the send actually proceeded and the refs are consumed only on
  acceptance. (2) A FAILED thread close silently cleared the rotation
  guard, so the next reply landed in the still-active old thread; the
  guard is now a pending-close id that sendMessage itself re-settles
  (retry-on-next-send) before any reply is routed — self-healing for
  latency AND failure. (3) The AGENTS.md validation gate ran on an
  isolated server (:8899, temp data dir): GET /health ok; critical-flow
  smoke — auth register+login, memory item create/list, settings
  (presence GET/PUT round-trip incl. initiativeEnabled), chat history +
  brief greeting, initiatives poll — all pass, recorded below.

- 2026-07-30 23:43 MYT - PR #131 review round 5 (P1 + 2 P2), completion
  re-stamped: (1) submissions are serialized by a synchronous in-flight
  latch — the close-await yields before `streaming` is set, so a double
  submit previously passed every guard twice and started two streams;
  (2) handleSubmit now consumes only the seed PREFIX it actually sent —
  a Reply merging mid-send stays queued instead of being discarded by
  the unconditional clear; (3) the recorded `bun run build` evidence was
  from the round-2 head — re-run and recorded on THIS final head.

- 2026-07-31 00:36 MYT - PR #131 review round 6 (P1 + P2), completion
  re-stamped: (1) settleSeedClose memoizes its in-flight promise — the
  eager close and the send guard now share ONE POST instead of racing
  two concurrent closes into duplicate on_thread_close side effects;
  (2) abandoning the seeded reply (selecting another thread / New
  Thread) clears the pending-close guard with one final best-effort
  close — except when re-opening the very thread the close targets —
  so a persistently failing close can no longer wedge unrelated
  conversations. Build re-run on this head: pass.

- 2026-07-31 03:03 MYT - PR #131 review round 7 (2 P1s), completion
  re-stamped: (1) the MOUNT seed path (Reply from another route) now
  registers the active server thread for closure exactly like the
  in-place path — it previously only cleared client state, so the first
  submit mixed the reply into the old conversation; (2) New Thread now
  ADOPTS a pending seed close instead of abandoning it (its semantics
  want the old conversation closed anyway) — abandoning left the old
  thread active with a usable composer. Build re-run on this head: pass.

- 2026-07-31 13:35 MYT - PR #131 review round 8 (2 P1s), completion re-stamped:
  (1) abandoning a seed no longer bypasses the memoized close — it
  reuses the in-flight request (retrying only if that one definitively
  failed, which is sequential rather than concurrent), so selecting
  another thread mid-close can't duplicate on_thread_close; (2) a mount
  seed whose /threads request FAILED no longer treats 'no active thread'
  as proven — it flags discovery as unknown and the send guard re-runs
  discovery (registering any needed close) before routing the reply,
  rejecting with a retry message while it stays unknown.

- 2026-08-01 21:02 MYT - PR #131 review round 9 (P1): the discovery guard only closed
  AFTER /threads resolved, so a mount seed left an unsafe window — 
  seedActiveRef is live immediately and a fast submit during startup
  streamed with no threadId, letting the server pick the still-active
  old thread. The guard now STARTS closed for mount seeds
  (locationState.seedThread) and opens only when discovery actually
  succeeds; in-place seeds clear it since they know the thread id
  synchronously. Build re-run on this head: pass.

- 2026-08-01 21:26 MYT - PR #131 review round 10 (P1): the round-8 close memoization
  held a bare promise, so thread A's in-flight request could settle (or
  fail) on behalf of a newly pending thread B — B's close skipped while
  A stayed active, or A's retry closing B. The in-flight close is now
  keyed by thread id: reuse only on a match, the success path clears the
  pending marker only if it still points at that thread, and
  `classifySeedCloseAbandon` takes `inFlightThreadId` so the association
  is part of the tested contract. (The round's second thread — mount-seed
  discovery init — was reviewed at 4350044, the Update-branch head that
  predates the round-9 fix; no code change needed.)

- 2026-08-01 22:04 MYT - PR #131 review round 11 (P1): a send suspended on the
  discovery/close awaits resumed without rechecking intent — if the user
  selected another thread or pressed New Thread meanwhile, it posted the
  captured seed with a stale thread id, creating a hidden conversation
  and yanking the UI there when the trace returned. A conversation-intent
  epoch is bumped by every visible-conversation change (thread select,
  New Thread, new seed) and compared after the awaits; a mismatch aborts
  the send and leaves the draft for a deliberate resend.

## Validation

- Commands:
  - `bun test tests/initiativeReply.test.ts tests/initiativePoller.test.ts` —
    30 pass (15 IL-009 tests + the 15 poller regressions)
  - `bunx tsc --noEmit` — clean
  - `bun run build` (Nx server + desktop, cargo check) — pass on the
    FINAL head (round 11), 2026-08-01 22:04 MYT
- Changed paths:
  - `apps/desktop/src/lib/initiativeReply.ts` (new)
  - `apps/desktop/src/components/InitiativeOverlay.tsx`
  - `apps/desktop/src/pages/chat/Chat.tsx`
  - `apps/desktop/tests/initiativeReply.test.ts` (new)
  - Validation gate (2026-07-30 21:59 MYT, isolated server :8899):
    `GET /health` -> status ok; smoke: auth (register + login), memory
    (create + list), settings (presence GET/PUT), chat (history + brief),
    initiatives poll — all green. Desktop suite: 25/25 owned tests (the
    3 failing files — layout-nav, layout-top-nav, recovery-credential —
    fail identically without this branch's changes; pre-existing drift).
- Notes:
  - Server suite untouched (no server files in this branch's diff).
  - Residual risk: a seed navigation arriving mid-stream is deferred, not
    applied instantly — deliberate (applying would swap the thread under a
    live stream; dropping would lose the acked text).
