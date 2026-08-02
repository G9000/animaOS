# IL-014 - Sub-threshold moment crystallization (design ticket)

- Status: done
- Priority: P3
- Scope: `apps/server/src/anima_server/services/agent` (design first — no code until the design gate passes)
- Parent: none
- Depends on: IL-004, IL-005
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: docs/superpowers/specs/2026-08-02-il-014-subthreshold-crystallization-design.md
- Plan: none
- Created: 2026-07-30 15:50 MYT
- Updated: 2026-08-02 13:42 MYT
- Started: 2026-08-02 13:42 MYT
- Completed: 2026-08-02 13:42 MYT
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

- 2026-08-02 04:10 MYT - Design drafted (see Spec) and awaiting approval — the ticket
  stays `backlog` deliberately: its acceptance requires design sign-off
  before an implementation ticket is cut. The doc proposes the buffer
  mechanism (reusing IL-004 topic keys, soul-store, field-encrypted) and
  takes a position on each of the four tensions, with one open product
  question flagged for you: whether the pre-crystallization buffer should
  be VISIBLE to the user. That answer changes the schema and the consent
  surface, so it needs deciding before any code.

- 2026-08-02 13:42 MYT - CLOSED AS ALREADY DELIVERED — no code written. Investigation
  (see Spec) found this capability is IL-004, shipped 2026-07-18: its
  goal is verbatim "stop silently dropping sub-threshold memory
  candidates: accumulate them as weighted latent traces per topic and
  synthesize a fully-provenanced memory when cumulative weight crosses
  the crystallization threshold". Admission (`fold_to_trace` in
  soul_writer), accumulation (LatentTrace weight + weekly decay + cap),
  crystallization (`crystallize_due_traces` -> MemoryItem with
  source=latent_crystallization), soul-store scoping, vault export and
  right-to-forget are all in place.
  This ticket was filed from the v1.1 comparative analysis WITHOUT first
  checking whether the behavior existed — the mistake is recorded rather
  than quietly dropped. The design gate's blocking question (visible vs
  invisible buffer) was moot: IL-004 stores no content at all,
  `evidence_refs` holds identifiers only. One genuine refinement —
  requiring contributions from >=2 distinct sessions so a single long
  conversation can't crystallize a false 'pattern' — is filed as IL-016.

## Validation

- Commands:
  - No implementation: closed as already delivered by IL-004. Verified by
    reading the delivered code paths listed in the Spec.
- Changed paths:
  - `docs/superpowers/specs/2026-08-02-il-014-subthreshold-crystallization-design.md`
    (rewritten as the investigation record)
- Notes:
  - Follow-up `IL-016` carries the one genuine delta (session spread).
