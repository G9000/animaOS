# Single-User Temporal Memory v2 Baseline Audit

- Ticket: `SUM-001`
- Audit date: 2026-06-29 MYT
- Scope: live `apps/server` memory code, architecture notes, and PRD drift that affects the `SUM-002` through `SUM-010` plan.

## Summary

The live memory system is still centered on SQLCipher soul storage for durable identity and memory, with runtime PostgreSQL used for messages, queues, retrieval caches, and retryable work. The current implementation has working fact/preference/goal/relationship memory blocks, evidence-backed `MemoryItem` rows, episodic memory, emotional signal blocks, a SQLite-backed knowledge graph, heat-aware scoring, and sleep task bookkeeping.

This baseline also found several stale PRD claims. Most notably, the F3 predict-calibrate implementation referenced by the PRD roll-up is not present in live code: `predict_calibrate.py` and `test_predict_calibrate.py` do not exist, and the active extraction path is direct regex plus LLM candidate creation.

## Live Storage Boundary

| Area | Live code path | Baseline state |
| --- | --- | --- |
| Durable memory items | `apps/server/src/anima_server/models/agent_runtime.py:183` | `MemoryItem` is the canonical durable row for facts, preferences, goals, relationships, focus, embeddings, heat, supersession, and access metadata. |
| Evidence rows | `apps/server/src/anima_server/models/agent_runtime.py:415` | `MemoryItemEvidence` records source kind, runtime message references, observed time, speaker, confidence, encrypted evidence text, and metadata. |
| Episodes | `apps/server/src/anima_server/models/agent_runtime.py:263` | `MemoryEpisode` stores summarized session memory and is surfaced in prompt memory blocks. |
| Claims | `apps/server/src/anima_server/models/agent_runtime.py:477` | `MemoryClaim` and claim evidence provide partial structured profile storage, but not the full F10 profile model. |
| Knowledge graph | `apps/server/src/anima_server/models/agent_runtime.py:685`, `apps/server/src/anima_server/models/agent_runtime.py:713` | `KGEntity` and `KGRelation` are live SQLite/soul tables. Entities have optional embeddings and checksums; relations track source and destination entities, relation type, mentions, and optional source memory. |
| Runtime messages | `apps/server/src/anima_server/models/runtime.py:43`, `apps/server/src/anima_server/models/runtime.py:363` | `RuntimeThread` and `RuntimeMessage` are operational runtime rows, not the portable memory authority. |
| Editable core blocks | `apps/server/src/anima_server/models/consciousness.py:26` | `SelfModelBlock` stores soul/persona/human and other core self-model sections. |
| Agent profile metadata | `apps/server/src/anima_server/models/consciousness.py:75` | `AgentProfile` stores creation/profile metadata for the agent, separate from the user's human memory. |

## Retrieval And Prompt Recall

| Capability | Live code path | Baseline finding |
| --- | --- | --- |
| Static and turn memory blocks | `apps/server/src/anima_server/services/agent/memory_blocks.py:76`, `apps/server/src/anima_server/services/agent/memory_blocks.py:163` | Prompt memory is assembled through runtime and turn-specific memory block builders. |
| Fact/preference/goal/relationship recall | `apps/server/src/anima_server/services/agent/memory_blocks.py:313`, `apps/server/src/anima_server/services/agent/memory_blocks.py:344`, `apps/server/src/anima_server/services/agent/memory_blocks.py:375`, `apps/server/src/anima_server/services/agent/memory_blocks.py:461` | These blocks use scored memory retrieval. SUM-001 adds deterministic probes for fact recall. |
| Human/profile recall | `apps/server/src/anima_server/services/agent/memory_blocks.py:727` | The human block merges ground-truth `User` profile fields with `SelfModelBlock(section="human")`. Full typed user profile remains future F10 work. |
| Emotional recall | `apps/server/src/anima_server/services/agent/memory_blocks.py:1032`, `apps/server/src/anima_server/services/agent/memory_blocks.py:1056` | Recent emotional signals and promoted emotional patterns are visible as memory blocks. SUM-001 adds a deterministic emotional recall probe. |
| Episode recall | `apps/server/src/anima_server/services/agent/memory_blocks.py:565` | Recent episodes surface in prompt context. They provide pattern evidence, but there is no dedicated cross-episode pattern synthesis service yet. |
| Hybrid memory search | `apps/server/src/anima_server/services/agent/embeddings.py:1071` | Semantic plus lexical retrieval is merged with RRF. Runtime pgvector is preferred when available, with SQLCipher item embeddings as canonical source material. |
| Adaptive retrieval filtering | `apps/server/src/anima_server/services/agent/embeddings.py:1203` | Adaptive cutoffs are applied after candidate retrieval and are logged in turn retrieval metadata. |
| Long-memory tool routing | `apps/server/src/anima_server/services/agent/tools.py:869`, `apps/server/src/anima_server/services/agent/templates/system_prompt.md.j2:27` | The system prompt instructs the model to call `search_long_memory` for cross-session counts, latest values, temporal ordering, or preference-driven recommendations. There is not yet a typed retrieval query planner. |

## Evidence And Temporal Recall

| Capability | Live code path | Baseline finding |
| --- | --- | --- |
| Wide evidence retrieval | `apps/server/src/anima_server/services/agent/evidence_retrieval.py:633` | `retrieve_wide_evidence()` expands memory hits into evidence snippets and supports retrieval modes such as aggregate, latest update, temporal, and preference. |
| Evidence ordering | `apps/server/src/anima_server/services/agent/evidence_retrieval.py:697` | Evidence rows are sorted with observed-time metadata. SUM-001 adds a deterministic latest-update probe to lock current behavior. |
| Evidence-backed promotion | `apps/server/src/anima_server/services/agent/soul_writer.py:104`, `apps/server/src/anima_server/services/agent/provenance.py:312` | Soul Writer promotes runtime candidates into durable memories and evidence. LLM and predict-calibrate candidate sources are both accepted, but live predict-calibrate generation is absent. |

## Write And Consolidation Paths

| Capability | Live code path | Baseline finding |
| --- | --- | --- |
| Direct extraction | `apps/server/src/anima_server/services/agent/consolidation.py:287`, `apps/server/src/anima_server/services/agent/consolidation.py:348` | `run_background_extraction()` uses deterministic regex extraction plus direct LLM extraction to create runtime memory candidates. |
| LLM extraction | `apps/server/src/anima_server/services/agent/consolidation.py:110` | The active LLM path extracts candidate memory directly; it does not call a live predict-calibrate module. |
| Soul Writer promotion | `apps/server/src/anima_server/services/agent/soul_writer.py:104` | Soul Writer remains the durable promotion path from runtime candidates to SQLCipher memory and evidence. |
| Sleep orchestration | `apps/server/src/anima_server/services/agent/sleep_agent.py:338` | Consolidation is routed through sleep tasks. Tasks remain sequential, which is an intentional SQLite single-writer constraint. |
| Restart cursor | `apps/server/src/anima_server/services/agent/sleep_agent.py:379`, `apps/server/src/anima_server/services/agent/sleep_agent.py:556` | SUM-001 wires consolidation results to the latest runtime message id and unprocessed message count when a runtime DB factory is available. Legacy/no-runtime calls keep the old fallback payload. |

## Knowledge Graph

| Capability | Live code path | Baseline finding |
| --- | --- | --- |
| Entity embeddings | `apps/server/src/anima_server/services/agent/knowledge_graph.py:168`, `apps/server/src/anima_server/services/agent/knowledge_graph.py:301` | Entity embeddings are generated during graph ingestion and stored on `KGEntity` when supplied to `upsert_entity()`. The old PRD wording that `embedding_json` is never populated is stale. |
| Semantic graph query | `apps/server/src/anima_server/services/agent/knowledge_graph.py:227`, `apps/server/src/anima_server/services/agent/knowledge_graph.py:277` | Graph query can generate a query embedding and compare it to entity embeddings with cosine similarity. |
| Alias dedup | `apps/server/src/anima_server/services/agent/knowledge_graph.py:301` | Entity dedup still uses exact normalized names plus token/fuzzy matching before insert. Embedding-based alias merge remains a follow-up for `SUM-003`. |
| Vault export/import | `apps/server/src/anima_server/services/vault.py:117`, `apps/server/src/anima_server/services/vault.py:727`, `apps/server/src/anima_server/services/vault.py:814`, `apps/server/src/anima_server/services/vault.py:1357` | SUM-001 adds KG entities and relations to vault snapshots, restore ordering, capsule graph sections, and serialization. |

## Heat And Forgetting

| Capability | Live code path | Baseline finding |
| --- | --- | --- |
| Visibility floor | `apps/server/src/anima_server/services/agent/forgetting.py:33`, `apps/server/src/anima_server/services/agent/memory_store.py:570` | Memories with decayed nonzero heat below the visibility floor are filtered out of prompt retrieval. |
| Scored retrieval | `apps/server/src/anima_server/services/agent/memory_store.py:554`, `apps/server/src/anima_server/services/agent/memory_store.py:643` | `get_memory_items_scored()` ranks by retrieval score and preserves query embedding blending. |
| Pool ordering | `apps/server/src/anima_server/services/agent/memory_store.py:588` | SUM-001 builds the candidate pool from both heat-ranked and recency-ranked slices before Python scoring, so hot older memories and fresh unscored memories both survive the pre-score cap. |

## Drift And Follow-Up Scope

| Item | Baseline finding | Follow-up |
| --- | --- | --- |
| Predict-calibrate | `apps/server/src/anima_server/services/agent/predict_calibrate.py` and `apps/server/tests/test_predict_calibrate.py` do not exist. Prompt templates for prediction and delta extraction remain, but the live extraction path is direct extraction. | Decide whether to restore F3 as a real module or revise the F3 PRD/status before building on it. |
| Retrieval router | The model chooses among visible memory, recall tools, and `search_long_memory` based on prompt instructions. There is no explicit query-plan object that selects profile, graph, memory items, episodes, transcripts, foresight, or procedural memory. | `SUM-005`. |
| Structured user profile | User fields, `SelfModelBlock(section="human")`, `MemoryClaim`, and `MemoryClaimEvidence` are partial profile mechanisms. There is no typed evidence-backed profile field table with lifecycle semantics. | `SUM-004`. |
| Cross-episode pattern synthesis | Episodes and emotional patterns exist, but there is no service that periodically synthesizes recurring stressors, avoidance loops, preference shifts, or unresolved threads across episodes. | `SUM-007`. |
| Foresight | No live `foresight_signals` table/model/block/extraction path is present in server code. | `SUM-008`. |
| Procedural experience memory | No live `AgentExperience`, `ExperienceCluster`, or `AgentSkill` model/service is present. | `SUM-009`. |
| External adapter seams | Runtime tool adapters exist, but this memory v2 work has not introduced optional external memory adapters. SQLCipher remains canonical. | `SUM-010`. |

## Baseline Probes Added

`apps/server/tests/test_single_user_memory_baseline_probes.py` adds deterministic probes for:

- factual recall through the facts memory block
- emotional recall through recent emotional context
- profile recall through `User` fields plus the human core block
- temporal recall through latest evidence ordering
- pattern baseline through repeated episode evidence

These probes deliberately avoid provider calls and verify the system's current baseline before deeper architecture changes.
