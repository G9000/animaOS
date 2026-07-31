# IL-014 - Sub-threshold moment crystallization (design ticket)

- Status: backlog
- Priority: P3
- Scope: `apps/server/src/anima_server/services/agent` (design first — no code until the design gate passes)
- Parent: none
- Depends on: IL-004, IL-005
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: none
- Plan: none
- Created: 2026-07-30 15:50 MYT
- Updated: 2026-07-30 15:50 MYT
- Started:
- Completed:

Standalone follow-up beyond the closed Inner Life v1 scope — tracked in
`IL-000`'s "Follow-ups Beyond v1 Scope" section.

## Goal

Today a conversational moment either clears the memory-extraction bar or
vanishes entirely. That loses a real texture of long relationships: many
individually forgettable moments that share a shape ("we keep joking about
X", "they sigh whenever Y comes up") should eventually become ONE remembered
thing. Design — and only after an explicit design review, implement — a
sub-threshold accumulation buffer: moments below the extraction threshold
land in a bounded runtime-store buffer with a salience estimate; when the
accumulated weight of same-theme entries crosses a threshold, they collapse
into a single synthesized MemoryItem ("crystallized from N small moments")
with provenance, and the buffer entries are deleted.

This is deliberately a DESIGN ticket. The mechanism has hard tensions to
resolve on paper first:

1. **Right-to-forget:** buffered fragments reference raw conversational
   content below the user's visibility threshold. Forgetting a topic must
   scrub matching buffer entries (they are derived data, like dreams — the
   IL-007 scrub precedent applies), and a crystallized item must carry
   provenance that the forget path can match.
2. **Encryption boundary:** buffer entries carry user content and MUST be
   field-encrypted in the runtime store (or live in the soul store), with
   the same DEK gating as every other content path.
3. **Consent/visibility:** crystallized memories appear "from nowhere" in
   the user's memory list. They must be labeled as synthesized-from-many
   (provenance-visible), not presented as a verbatim recollection.
4. **Quality bar:** theme-matching below the extraction threshold risks
   crystallizing noise. The design must define the similarity signal
   (embedding clustering vs. topic keys) and a conservative weight
   threshold, with an eval before enabling by default.

## Deliverables

- A design doc (spec) covering the four tensions above, buffer schema,
  crystallization trigger, provenance shape, and forget-path integration.
- Explicit design approval gate before any implementation ticket is cut.

## Acceptance

- Design reviewed and approved (or explicitly rejected with rationale
  recorded — a "no" closing this ticket is a valid outcome).

## Activity Log

- 2026-07-30 15:50 MYT - Filed from the Inner Life v1.1 comparative analysis
  as the highest-value not-yet-adopted idea, deliberately gated on design
  because of the right-to-forget and encryption tensions.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
