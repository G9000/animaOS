# IL-009 - Carry initiative context into the Reply round-trip

- Status: backlog
- Priority: P3
- Scope: `apps/desktop/src/components/InitiativeOverlay.tsx`, `apps/desktop/src/pages/chat`
- Parent: none
- Depends on: `IL-008`
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: none
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-28 16:13 MYT
- Updated: 2026-07-28 19:29 MYT
- Started:
- Completed:

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

## Validation

- Commands: not run yet.
- Changed paths: none.
- Notes: none.
