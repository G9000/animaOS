# F15: Memory Provenance And Event Evidence

Status: partial implementation  
Created: 2026-05-17  
Updated: 2026-05-17  
Workstream: `scratchboard/v2-memory-recall-reliability`

## Summary

ANIMA needs durable memory evidence that records where a memory came from, who said it, when it was observed, and how confident the extractor was. This lets recall answer "latest", "count", and temporal questions from structured metadata instead of parsing dates out of text chunks.

## Problem

`MemoryItem` is the durable atomic memory surface, but it only stores content, category, tags, importance, embeddings, and lifecycle fields. `MemoryClaimEvidence` exists, but it is scoped to structured `MemoryClaim` rows and only stores source text plus a coarse source kind. It cannot represent freeform memory item provenance, runtime message IDs, transcript references, observed timestamps, speakers, or extraction confidence.

The result is that long-memory retrieval has to infer time and source from memory text. That works for some raw transcript chunks, but fails for promoted atomic facts like:

- "Rachel moved to the north side."
- "I bought a 1/72 scale B-29."
- "I prefer advanced PyTorch resources."

Those facts need source metadata to answer "what was the latest update?", "how many separate times did I mention this?", and "which happened first?" reliably.

## Decision

Add a new durable evidence table for `MemoryItem` provenance rather than extending `MemoryClaimEvidence`.

Implementation status: `MemoryItemEvidence` model and core migration exist; explicit saves, regex/LLM candidates, Soul Writer promotion/supersession, eval transcript imports, raw eval chunks, metadata-aware retrieval, and idempotent legacy/eval backfill are implemented. Remaining work is operationalizing backfill execution for existing user vaults and any future UI exposure of provenance.

Rationale:

- `MemoryClaimEvidence` belongs to the structured-claim layer and depends on `claim_id`.
- Freeform `MemoryItem` rows are the primary recall surface today, including items that never become structured claims.
- A dedicated evidence table can support provenance for explicit saves, extraction candidates, transcript imports, eval imports, and future event memories without forcing every memory through `MemoryClaim`.
- Structured claims can still link to `MemoryItem`; claim evidence remains claim-specific.

## Proposed Model

Table: `memory_item_evidence`

| Field | Type | Required | Purpose |
| --- | --- | --- | --- |
| `id` | integer PK | yes | Evidence row ID. |
| `user_id` | FK users | yes | Tenant boundary and query key. |
| `memory_item_id` | FK memory_items | yes | Durable memory this evidence supports. |
| `source_kind` | string | yes | `user_message`, `assistant_message`, `explicit_save`, `llm_extraction`, `regex_extraction`, `transcript`, `eval_import`, `legacy_backfill`. |
| `runtime_thread_id` | integer nullable | no | Runtime thread source when available. |
| `runtime_message_id` | integer nullable | no | Runtime message source when available. |
| `runtime_message_ids_json` | JSON nullable | no | Multi-message evidence span for extracted facts. |
| `transcript_ref` | string nullable | no | Archived transcript artifact when runtime rows are gone. |
| `sequence_id` | integer nullable | no | Message sequence inside a runtime thread or transcript. |
| `speaker` | string nullable | no | `user`, `assistant`, `system`, or `unknown`. |
| `observed_at` | datetime nullable | no | When the source statement happened. Primary temporal sort key. |
| `source_created_at` | datetime nullable | no | When the source row/artifact was created if distinct from observed time. |
| `confidence` | float | yes | Extractor/source confidence, default `1.0` for explicit saves and `0.8` for LLM extraction. |
| `extractor` | string nullable | no | `tool`, model name, `regex`, `eval_import`, or `backfill`. |
| `evidence_text` | encrypted text | yes | Minimal source quote or compact statement supporting the memory. |
| `metadata_json` | JSON nullable | no | Small structured extras such as eval dataset IDs or import batch IDs. |
| `created_at` | datetime | yes | Evidence row creation time. |

Indexes:

- `(user_id, memory_item_id)`
- `(user_id, observed_at)`
- `(user_id, source_kind, observed_at)`
- `(runtime_message_id)` where supported
- `(transcript_ref)` where supported

## Write Rules

- Every new `MemoryItem` promotion should create at least one evidence row.
- Explicit `save_to_memory` evidence uses `source_kind="explicit_save"`, `speaker="user"`, confidence `1.0`, and source message IDs when available.
- LLM and regex extraction evidence should copy `MemoryCandidate.source_message_ids`, previews, extractor/model, and observed timestamp when known.
- Transcript imports should set `transcript_ref`, `sequence_id`, `speaker`, and `observed_at` from transcript message timestamps.
- Eval imports should set `source_kind="eval_import"` and preserve dataset/import metadata in `metadata_json`.
- Superseded memories keep their evidence. New replacement memories get their own evidence rows and may copy prior evidence only when the source still supports the new content.

## Retrieval Rules

- Latest-update questions sort matching evidence by `observed_at DESC`, not by `MemoryItem.updated_at`.
- Temporal questions use evidence `observed_at` and `sequence_id` before parsing dates from text.
- Count questions count distinct evidence events when the memory item represents an event-like fact, and count distinct memory items only when evidence cannot distinguish events.
- Preference recommendations can boost evidence with `source_kind` from explicit user statements and higher confidence.
- Text parsing remains a fallback for legacy rows without structured evidence.

## Backfill Strategy

1. For existing `MemoryItem` rows without evidence, create one `legacy_backfill` row with `evidence_text=MemoryItem.content`, `observed_at=MemoryItem.created_at`, confidence `0.5`, and no runtime source IDs.
2. For candidates promoted after MR-006, copy `source_message_ids` into `runtime_message_ids_json` and set the first user message as the primary `runtime_message_id` when available.
3. For archived/eval raw transcript memories, parse `Session date:` only as a backfill hint and store the parsed value in `observed_at`.
4. Make the backfill idempotent by checking `(memory_item_id, source_kind, evidence_text hash)` before insertion.

## Privacy And Storage

- Evidence is durable soul data and belongs in SQLCipher with `MemoryItem`, not runtime PostgreSQL.
- `evidence_text` must be encrypted with the same field encryption path used for memory content.
- Runtime IDs may be stored as integers because they are references, not content, but evidence must continue to work after runtime rows are pruned by falling back to `transcript_ref` or `legacy_backfill`.
- Do not duplicate full transcripts in evidence rows; store the minimal supporting line or compact snippet.

## Success Criteria

| Capability | Target |
| --- | --- |
| New promoted memories have evidence | 100% of new `MemoryItem` rows created by explicit save, extraction, and transcript/eval import paths. |
| Latest recall uses source time | Latest-update probes sort by `observed_at` when evidence exists. |
| Count recall has event support | Count probes can count distinct evidence events for event-like memories. |
| Legacy data remains searchable | Existing memories receive idempotent `legacy_backfill` evidence rows. |
| Runtime pruning does not break provenance | Evidence remains useful after runtime messages are archived or deleted. |

## Out Of Scope For This Ticket

- Full event ontology or calendar-style event extraction.
- User-facing provenance UI.
- Backfilling perfect source message IDs for legacy memories when no runtime/archive source exists.
- Replacing `MemoryClaimEvidence`; claim evidence remains claim-scoped.

## References

- [Memory Recall Reliability Todo](../../../scratchboard/v2-memory-recall-reliability/todo.md)
- [Memory System Architecture](../../architecture/memory/memory-system.md)
- [Agent Runtime Architecture](../../architecture/agent/agent-runtime.md)
- [Structured User Profile PRD](F10-structured-user-profile.md)
