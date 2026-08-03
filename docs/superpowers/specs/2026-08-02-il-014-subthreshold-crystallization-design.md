# IL-014 — Sub-threshold moment crystallization: already delivered by IL-004

- Ticket: `tickets/inner-life-v1/IL-014-subthreshold-crystallization.md`
- Status: **investigated — no work required. IL-014 closed as delivered.**
- Date: 2026-08-02

## Finding

IL-014 was filed from the Inner Life v1.1 comparative analysis as "the last
unstolen idea": many individually forgettable moments that share a shape
should eventually become one remembered thing.

**That capability already exists.** It is IL-004, shipped 2026-07-18.

IL-004's goal, verbatim:

> Stop silently dropping sub-threshold memory candidates: accumulate them as
> weighted latent traces per topic and synthesize a fully-provenanced memory
> when cumulative weight crosses the crystallization threshold.

The ticket was filed without first checking whether the behavior existed, and
an earlier revision of this document proposed building it. That was wrong; the
document is kept as the record of why IL-014 is being closed rather than
implemented.

## Evidence

| IL-014 proposed | IL-004 ships | Where |
| --- | --- | --- |
| Admit below-threshold moments | `fold_to_trace` decision for score ∈ [0.25·θp, θp) | `soul_writer.plan_candidate_promotion` |
| Theme key | `LatentTrace.topic_key` from `derive_topic_key` | `models/agent_runtime.py`, `claims.py` |
| Accumulating salience | `weight ← min(1, weight + 0.5·s)`, weekly ×0.98 decay, per-user cap | `inner_life/latent.py` |
| Collapse into one memory | `crystallize_due_traces` → `MemoryItem(source="latent_crystallization")` with contributing evidence | `latent_traces.py` |
| Soul store, field-encrypted | Soul store; `topic_key` is a derived identifier, not content | `models/agent_runtime.py` |
| Vault export/import | `latentTraces` in the snapshot and both scope allowlists | `services/vault.py` |
| Right-to-forget | Explicit forget deletes matching traces and scrubs `evidence_refs`; crystallization re-validates refs at synthesis | `forgetting.py`, `latent_traces.py` |

## The tension that turned out not to exist

The blocking question in the earlier draft was whether the pre-crystallization
buffer should be visible to the user, since it would accumulate conversational
content the user had not seen.

IL-004 answered it more cleanly than either option on offer: **it stores no
content at all.** `LatentTrace.evidence_refs` holds identifiers only —
candidate id, source message ids, content hash — "never copied text", per the
model docstring. There is no hidden content to expose, so no consent surface is
needed and the invisible/visible trade-off is moot.

## Genuine deltas (small, optional)

Stated honestly rather than used to justify the ticket:

1. **Grouping.** The reference idea accumulated purely by volume and collapsed
   into one synthetic "burst" memory; IL-004 groups by topic. IL-004's version
   is better — a topic-coherent memory beats an arbitrary bundle. **No change
   wanted.**
2. **Session spread.** IL-004 lets weight accumulate within a single
   conversation, so one long rambling session can crystallize a "pattern" that
   is really a single occasion. Requiring contributions from ≥2 distinct
   sessions would make a crystallized memory mean "recurring" rather than
   "discussed at length". Filed separately as `IL-016`; genuinely small.

## Outcome

- `IL-014` → done, closed as delivered by IL-004. No code written.
- `IL-016` filed for the session-spread refinement.
