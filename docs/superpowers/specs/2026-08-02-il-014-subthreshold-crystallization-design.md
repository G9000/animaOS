# IL-014 — Sub-threshold moment crystallization (design)

- Ticket: `tickets/inner-life-v1/IL-014-subthreshold-crystallization.md`
- Status: **proposed — awaiting approval.** No implementation ticket should be
  cut until the four tensions below are accepted or amended.
- Date: 2026-08-02
- Depends on: IL-004 (latent traces), IL-005 (distillation)

## The behavior we want

Today a conversational moment either clears the memory-extraction bar or
vanishes. That loses a real texture of long relationships: many individually
forgettable moments that share a shape — "we keep joking about the neighbour's
dog", "they go quiet whenever work comes up" — should eventually become **one**
remembered thing, without any single one of them having been worth keeping.

The companion should be able to say *"you mention the boat a lot"* without ever
having decided, in the moment, that any particular boat mention was memorable.

## Mechanism

A bounded per-user buffer of sub-threshold moments, and a crystallization pass
that collapses a themed cluster into a single `MemoryItem`.

1. **Admission.** Extraction already scores candidate moments. Moments scoring
   below the extraction threshold but above a floor (a genuine signal, not
   noise) get a buffer row: theme key, salience estimate, timestamp, and a
   short content excerpt.
2. **Theme key.** Reuse IL-004's latent-topic key derivation
   (`derive_topic_key`) rather than inventing a second clustering scheme —
   crucially, it is already field-encrypted and already participates in
   right-to-forget.
3. **Crystallization.** When one theme's accumulated salience crosses a
   threshold, the buffer rows for that theme collapse into a single
   `MemoryItem` with `source="crystallized"` and provenance recording the
   contributing row count and time span. The rows are deleted in the same
   transaction.
4. **Decay.** Buffer rows age out on the IL-004 leak schedule, so a theme that
   stops recurring never crystallizes.

## The four tensions (why this needs approval, not just implementation)

### 1. Right to forget

Buffer rows hold conversational content below the user's visibility threshold —
they are, in effect, memories the user has not been shown and cannot browse.

**Proposal:** buffer rows are derived data with the same standing as dreams.
`forget_memory` must scrub buffer rows whose theme key matches the forgotten
content, and a crystallized item must carry provenance that the forget path can
match, exactly like `dream_journal.source_refs`. A crystallized item is a normal
`MemoryItem` and is forgettable normally.

**Open question for approval:** should buffer rows be *visible* to the user
(a "forming impressions" view) before they crystallize? Invisible accumulation
of conversational content is the part most likely to feel like surveillance if
it were ever surfaced unexpectedly — and the IL-011 held-thought work set a
precedent that the companion only claims what it can show grounding for.

### 2. Encryption boundary

Buffer content is user content. It must be field-encrypted with the same
`DOMAIN_MEMORIES` DEK as every other content column, and every read path needs
an active-DEK gate (`df` fails open — the mistake IL-010 had to fix twice).

**Proposal:** buffer lives in the **soul** store, not the runtime store.
It is not rebuildable — discarding it loses real signal — and the runtime store
is explicitly the rebuildable tier.

### 3. Confabulation risk

A crystallized memory is a claim the user never made: *"you mention the boat a
lot"* is an inference over moments none of which were individually notable. If
the clustering is loose, the companion asserts a pattern that isn't there — the
exact failure mode the IL-003 material rule and IL-011 grounding rules exist to
prevent.

**Proposal:** crystallization requires (a) at least N distinct moments —
suggest 3, matching the "one is chance, two coincidence, three a pattern"
heuristic already used elsewhere in the drive work — (b) spread over at least
two distinct sessions, so a single rambling conversation can't manufacture a
pattern, and (c) the crystallized text states its own basis ("you've brought
this up a few times") rather than asserting a fact.

### 4. Evaluation before default-on

**Proposal:** ship behind a config flag defaulting OFF, with an eval over
recorded conversations measuring precision (are crystallized items ones a human
would agree with?) before it becomes default behavior. IL-010 shipped
default-off for the same reason and that was the right call.

## What this does NOT do

- No LLM call at admission time. Admission is arithmetic over the extraction
  score that already exists; only crystallization may involve generation, and
  only over already-buffered material.
- No change to the extraction threshold itself. This is strictly additive
  below the existing bar.

## Recommendation

The mechanism is sound and the value is real — it is the one idea from the
comparative analysis that the current system has no analogue for. But it is
also the only one that **accumulates user content the user has not seen**, and
that is a product decision, not an engineering one.

I'd want an explicit answer on tension #1 (visible or invisible buffer) before
writing code, because it changes the schema (a user-facing view needs stable
ids and a read API) and the consent surface (a Presence toggle).
