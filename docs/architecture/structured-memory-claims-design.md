---
title: Structured Memory Claims Design
last_edited: 2026-03-15
status: draft
scope: apps/server
---

# Structured Memory Claims Design

This document defines the long-term replacement for ANIMA's current
English-biased string heuristics in long-term memory writes.

The goal is not to remove `memory_items` immediately. The goal is to introduce
one canonical, language-agnostic claim layer that can resolve duplicates,
updates, and contradictions before they become durable memory.

## Why This Exists

Today the backend stores durable user memory primarily as freeform text in
`memory_items.content`.

That works for early product progress, but it has hard limits:

- dedupe depends too much on English phrasing
- contradiction handling is partly heuristic and partly delayed to sleep-time jobs
- multilingual input cannot reliably map to the same memory concept
- storage mixes three different things into one string:
  - the claim itself
  - the rendered sentence
  - the evidence/provenance

That is acceptable as a bridge. It is not the right foundation.

## Design Goal

Move from:

- durable memory = rendered text statement

to:

- durable memory = structured claim with provenance, confidence, and lifecycle
- rendered text = one view of that claim

## Non-Goals

This design does not aim to:

- replace the current runtime prompt system in one step
- make the first extractor perfect across all languages
- require a new external orchestration framework
- force every memory into a rigid ontology on day one

The migration should be incremental and dual-write safe.

## Core Model

Introduce a canonical `memory_claims` layer under `memory_items`.

Recommended split:

- `memory_claims`: canonical structured claims and claim lifecycle
- `memory_claim_evidence`: optional evidence rows that support a claim over time
- `memory_items`: prompt/search-facing rendered memory rows during migration

Short version:

- claims are truth objects
- memory items are serving objects

## Proposed Schema

### `memory_claims`

Suggested first version:

| Column | Purpose |
|---|---|
| `id` | primary key |
| `user_id` | owner |
| `memory_item_id` | optional link to serving-layer row |
| `subject_type` | usually `user`, later can support `assistant_self`, `relationship_person` |
| `namespace` | coarse area like `profile`, `preference`, `goal`, `relationship`, `focus` |
| `slot` | canonical attribute, for example `occupation`, `birthday`, `location`, `drink_preference` |
| `value_text` | normalized human-readable value |
| `value_json` | structured value when needed |
| `polarity` | `positive`, `negative`, `neutral`, or `unknown` |
| `confidence` | 0.0-1.0 |
| `status` | `active`, `superseded`, `rejected`, `pending_review` |
| `language` | language of the source utterance, for example `en`, `ms`, `zh` |
| `surface_form` | original extracted phrase or short rendered statement |
| `canonical_key` | normalized key used for dedupe/update resolution |
| `source_kind` | `user_message`, `session_note`, `reflection`, `manual`, `migration` |
| `source_ref` | stable source id where possible |
| `extractor` | `regex`, `llm`, `manual`, `migration` |
| `superseded_by` | replacement claim id |
| `created_at` | created time |
| `updated_at` | updated time |

Recommended uniqueness direction:

- no global uniqueness constraint on raw values
- index on `(user_id, subject_type, namespace, slot, status)`
- index on `(user_id, canonical_key, status)`

The claim layer must support ambiguity, not pretend ambiguity never happens.

### `memory_claim_evidence`

This can be phase 2 if needed, but it is the right long-term shape.

| Column | Purpose |
|---|---|
| `id` | primary key |
| `claim_id` | parent claim |
| `source_kind` | `user_message`, `assistant_summary`, `session_note`, `manual` |
| `source_ref` | message id, note id, log id, etc. |
| `language` | source language |
| `evidence_text` | exact supporting snippet or paraphrase |
| `confidence_delta` | support strength from this evidence row |
| `created_at` | timestamp |

This lets you keep multiple supporting moments without rewriting the claim row.

## Claim Semantics

The extractor should output a structured claim candidate, not just text.

Example:

```json
{
  "subject_type": "user",
  "namespace": "profile",
  "slot": "occupation",
  "value_text": "product designer",
  "polarity": "neutral",
  "confidence": 0.93,
  "language": "en",
  "surface_form": "I work as a product designer"
}
```

Malay version of the same idea:

```json
{
  "subject_type": "user",
  "namespace": "profile",
  "slot": "occupation",
  "value_text": "product designer",
  "polarity": "neutral",
  "confidence": 0.91,
  "language": "ms",
  "surface_form": "Saya bekerja sebagai product designer"
}
```

These should resolve to the same canonical fact even though the source language
and surface text differ.

## Resolution Rules

The write path should stop deciding memory truth on raw rendered strings.

It should resolve claim candidates using structured rules.

### 1. Duplicate

Treat as duplicate when:

- `subject_type`, `namespace`, `slot`, `value`, and `polarity` are equivalent
- confidence is not materially stronger than the active claim

Action:

- keep existing active claim
- add evidence row if useful
- do not create a new serving memory item

### 2. Update

Treat as update when:

- same `subject_type` and same single-value slot
- new value conflicts with old value
- new claim has stronger evidence or direct recency

Examples:

- `occupation = product designer` -> `occupation = design lead`
- `focus = finish runtime migration` -> `focus = clean up memory pipeline`
- `likes green tea` -> `dislikes green tea`

Action:

- create new active claim
- supersede old active claim
- update or regenerate the serving memory item

### 3. Coexist

Allow coexistence when:

- slot is multi-value by design
- values are not mutually exclusive

Examples:

- likes green tea
- likes coffee
- goals: ship server runtime
- goals: improve memory quality

Action:

- keep multiple active claims

### 4. Defer

Defer when:

- the extractor is uncertain
- the slot is ambiguous
- evidence is too weak for durable memory
- the contradiction is real but unresolved

Action:

- mark `pending_review`
- optionally keep only as session note or daily-log evidence
- do not write into active durable memory yet

## Slot Policy

The claim system should explicitly distinguish single-value and multi-value slots.

### Single-value slots

Only one active claim at a time:

- `display_name`
- `username`
- `age`
- `birthday`
- `occupation`
- `employer`
- `location`
- `gender`
- `current_focus`

### Multi-value slots

Multiple active claims can coexist:

- likes
- dislikes
- hobbies
- goals
- recurring relationships
- favorite_media

### Special-case slots

Need stronger rules or entity linking:

- relationships
- health-related statements
- identity-sensitive self-model claims

## Extraction Pipeline

Recommended write pipeline:

```text
conversation turn
  |
  v
candidate extraction
  - regex fallback
  - structured LLM extractor
  |
  v
canonicalization
  - detect language
  - normalize slot
  - normalize value
  - derive canonical_key
  |
  v
gating
  - confidence threshold
  - slot-specific policy
  - source trust policy
  |
  v
resolution
  - duplicate
  - update
  - coexist
  - defer
  |
  v
claim write
  |
  +--> evidence write
  |
  +--> memory_item materialization/update
```

## Source Trust Policy

Not all writers should have equal authority.

Recommended ranking:

1. manual user edit from memory API
2. direct user statement in current turn
3. promoted session note
4. deterministic extraction
5. structured LLM extraction
6. reflection output
7. deep synthesis or inferred profile merge

This matters because durable identity and profile facts should not be rewritten
by low-trust background synthesis.

## Self-Model Boundary

Do not mix user-profile claims and self-model claims into one undifferentiated
memory stream.

Recommended boundary:

- `memory_claims` is for facts about the user, the relationship, goals,
  preferences, and focus
- `self_model_blocks` remains the storage for `soul`, `identity`,
  `inner_state`, `working_memory`, `growth_log`, and `intentions`

If you later add structured self-model claims, use a separate namespace or a
separate table. Do not let ordinary user-memory extractors rewrite stable
identity text.

## Serving Layer Strategy

Do not switch prompt construction directly to `memory_claims` on day one.

Safer sequence:

1. claims become canonical write-time truth
2. `memory_items` remains the prompt/search serving layer
3. `memory_items` becomes a rendered projection of active claims
4. prompt blocks can later read directly from claims where useful

This keeps the runtime stable while the claim layer matures.

## Rendering Strategy

One claim can have multiple renderings:

- canonical rendering for prompt blocks
- UI-friendly rendering for memory pages
- source-language rendering for audit/provenance

So the system should store:

- normalized semantic value
- source-language surface form
- generated or canonical display text

Do not force one English sentence to carry all three jobs.

## Migration Plan

### Phase 1: Add Schema

Add the new claim table without changing runtime behavior.

Deliverables:

- `memory_claims` table
- optional `memory_claim_evidence` table
- basic SQLAlchemy models and Alembic revision

### Phase 2: Introduce Claim Objects in Code

Add typed claim models in the Python backend.

Deliverables:

- `ClaimCandidate`
- canonicalization helpers
- slot policy config
- resolution engine

Primary files:

- `apps/server/src/anima_server/services/agent/consolidation.py`
- `apps/server/src/anima_server/services/agent/memory_store.py`

### Phase 3: Dual-Write

During consolidation:

- write claim rows
- continue writing `memory_items`
- link each serving memory item back to its active claim where possible

This is the first production-safe milestone.

### Phase 4: Conservative Backfill

Backfill old `memory_items` into claims with a migration script.

Rules:

- only backfill high-confidence patterns automatically
- mark uncertain backfilled claims as `pending_review`
- never delete original `memory_items` during initial backfill

### Phase 5: Claim-First Resolution

Switch dedupe/update/conflict logic to claims only.

At this point:

- string heuristics become fallback extraction helpers
- serving memory rows are projections, not truth

### Phase 6: Prompt and Search Upgrades

Once claim quality is stable:

- build prompt memory blocks from claims for selected categories
- allow search to query structured fields and rendered text together

## Acceptance Criteria

This design is working when:

- the same fact in English and Malay resolves to one active claim
- preference flips supersede prior claims without manual string rules
- weak or ambiguous extractions are deferred instead of written
- memory search and prompt retrieval can still serve coherent text
- backfill from existing `memory_items` does not corrupt active memory

## Immediate Implementation Recommendation

Do not jump straight from the current system to a full ontology.

The next practical move is:

1. add `memory_claims`
2. define a small initial slot set
3. dual-write in consolidation
4. keep `memory_items` as the runtime serving layer

Recommended initial slots:

- `display_name`
- `age`
- `birthday`
- `occupation`
- `employer`
- `location`
- `current_focus`
- `likes`
- `dislikes`

That is enough to prove the architecture without overfitting.

## What To Avoid

Avoid these traps:

- expanding regex rules language by language forever
- treating raw English rendering as canonical memory truth
- letting reflection write directly into stable profile facts
- migrating prompt assembly and storage shape at the same time
- pretending every uncertain claim must become durable memory

## Final View

The current heuristic improvements are still worth keeping.

They should now be treated as temporary write-safety rails while ANIMA moves to
a structured claim architecture.

That is the better long-term direction because it aligns with the actual
product problem:

- multilingual users
- durable continuity
- trustworthy updates
- fewer wrong memories
- cleaner separation between truth, evidence, and rendering
