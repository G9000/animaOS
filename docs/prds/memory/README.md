# Memory PRDs — Implementation Status Index

Last audited: 2026-05-17

## Status Legend

| Status | Meaning |
|--------|---------|
| SHIPPED | Core logic, DB models, integration, and tests all exist. Minor residual items only. |
| NOT STARTED | PRD written, zero implementation. |
| PARTIAL | Some infrastructure exists but the PRD-specific work has not been done. |

---

## Feature Index

| ID | Feature | Status | Est. % | Tests | Priority | Depends On |
|----|---------|--------|--------|-------|----------|------------|
| F1 | [Hybrid Search (BM25 + RRF)](F1-hybrid-search.md) | SHIPPED | ~95% | 51 | P0 | — |
| F2 | [Heat Scoring](F2-heat-scoring.md) | SHIPPED | ~95% | 10 | P1 | — |
| F3 | [Predict-Calibrate Consolidation](F3-predict-calibrate.md) | SHIPPED | ~95% | 22 | P1 | F1 |
| F4 | [Knowledge Graph](F4-knowledge-graph.md) | SHIPPED | ~93% | 35 | P1 | — |
| F5 | [Async Sleep-Time Agents](F5-async-sleep-agents.md) | SHIPPED | ~90% | 18 | P2 | F2, F3, F4 |
| F6 | [Batch Segmentation](F6-batch-segmentation.md) | SHIPPED | ~98% | 25 | P2 | — |
| F7 | [Intentional Forgetting](F7-intentional-forgetting.md) | SHIPPED | ~93% | 28 | P2 | — |
| F8 | [Foresight Signals](F8-foresight-signals.md) | NOT STARTED | 0% | 0 | P1 | — |
| F9 | [Episode Extraction Upgrade](F9-episode-extraction-upgrade.md) | NOT STARTED | 0% | 0 | P1 | — |
| F10 | [Structured User Profile](F10-structured-user-profile.md) | PARTIAL | ~15% | 0 | P2 | — |
| F11 | [Agent Experience Extraction](F11-agent-experience-extraction.md) | NOT STARTED | 0% | 0 | P1 | — |
| F12 | [Experience Clustering](F12-experience-clustering.md) | NOT STARTED | 0% | 0 | P2 | F11 |
| F13 | [Skill Distillation](F13-skill-distillation.md) | NOT STARTED | 0% | 0 | P2 | F11, F12 |
| F14 | [Multi-User & Group Memory](F14-multi-user-group-memory.md) | NOT STARTED | 0% | 0 | P2 | F10 |
| F15 | [Memory Provenance And Event Evidence](provenance-and-event-memory.md) | PARTIAL | ~75% | 15 | P0 | F1, F10 |

**Total dedicated tests across shipped features: 204**

---

## Shipped Features (F1–F7) — All Gaps

Consolidated list of every gap found during detailed per-requirement audits. Sorted by impact.

### Functional Gaps (behavior differs from PRD)

| ID | Gap | Impact | Fix Effort |
|----|-----|--------|------------|
| F5.5 | Tasks run **sequentially**, not parallel via `asyncio.gather()` | Slower background processing. **Intentional** — SQLite can't handle concurrent writes. | N/A (design constraint) |
| F3 | PRD roll-up references `predict_calibrate.py` and `test_predict_calibrate.py`, but those files are absent in live server code | Predict-calibrate should not be treated as a dependency until the module is restored or the PRD is revised | Medium — product/architecture decision before implementation |
| F7.9 | `needs_regeneration` flags set on derived references but never acted on | Stale derived references (episodes, growth log entries citing superseded facts) remain stale indefinitely | Medium — need LLM-based regeneration task in sleep-time |
| F4.7 | Entity dedup uses normalized-name and token/fuzzy matching, not embedding similarity | Misses aliases like "NYC" ↔ "New York City"; entity embeddings now exist but are not used for alias merge | Medium — add embedding-based candidate matching to graph upsert/dedup |

### Resolved or Rebaselined by SUM-001

| ID | Change | Verification |
|----|--------|--------------|
| F2.9 | `get_memory_items_scored()` now combines heat-ranked and recency-ranked candidate slices before Python-side scoring and query blending | `test_scored_retrieval_pool_keeps_hot_older_items`, `test_scored_retrieval_pool_keeps_fresh_unscored_items` |
| F4.15 | Vault export/import now includes `kg_entities` and `kg_relations` with graph capsule grouping and restore ordering | `test_export_and_import_vault_restores_knowledge_graph` |
| F5.14/F5.23 | Consolidation result payloads now include the latest runtime message cursor and processed-message count when runtime DB rows are available | `test_consolidation_task_records_latest_runtime_message_cursor` |

### Dead Code / Cleanup

| ID | Item | Impact |
|----|------|--------|
| F1.8 | `_text_similarity()` / `search_by_text()` still in `vector_store.py` | Dead code — not used by main `hybrid_search()` path. Can be removed. |

### Missing Benchmarks (no functional impact)

| ID | Item |
|----|------|
| F1 AC3/AC4 | No explicit performance benchmark for BM25 build time or memory usage |
| F3 AC6 | No formal measurement of LLM token reduction from predict-calibrate |
| F4 AC6/T13 | No performance benchmark for graph traversal latency |

### Design Decisions (intentional deviations, not gaps)

| ID | Decision | Rationale |
|----|----------|-----------|
| F2 | `interaction_depth` proxied by `reference_count` | Code comment: "proxied by ref_count in v1" — separate tracking deferred |
| F3 | Heuristic quality gates (Option B), not LLM-based (Option A) | Deliberate choice — downstream `store_memory_item()` dedup is the real safety net |
| F4 | Entity extraction uses JSON prompt, not tool calling | Consistent with codebase patterns; works with all LLM providers |
| F5 | Sequential task execution, not parallel | SQLite single-writer constraint — not a bug |
| F7 | No batch-confirm endpoint for topic forgetting | Topic endpoint returns candidates; user calls single-forget per item |

---

## Unimplemented Features (F8–F13) — Summary

### F8 — Foresight Signals (0%)
Zero implementation. No table, no model, no extraction, no consumption. Requires: migration, model, extraction in consolidation, memory block, proactive greeting integration, lifecycle sweep, API endpoints. **Effort: Small.**

### F9 — Episode Extraction Upgrade (0%)
Episode system works end-to-end, but none of the F9 prompt upgrades applied:
- No dual-time format (relative + absolute)
- No entity-grounded narration (uses pronouns, not names)
- No conciseness constraints
- No conversation timestamp or user name in template context

**Effort: Tiny** — prompt template update + 2 extra template variables. No schema changes.

### F10 — Structured User Profile (~15%)
`MemoryClaim` + `MemoryClaimEvidence` provide partial structured storage (4 namespaces, evidence linking, supersession). Full vision unimplemented:
- No 7 profile categories
- No systematic extraction pipeline
- No prose rendering for system prompt
- No user-facing profile API
- No reconciliation with `human` block

**Effort: Medium.**

### F11 — Agent Experience Extraction (0%)
New PRD. No implementation. Requires: migration, `AgentExperience` model, extraction pipeline from tool call chains, `past_approaches` memory block, growth log integration. **Effort: Medium.**

### F12 — Experience Clustering (0%)
New PRD. Depends on F11. Requires: cluster state table, incremental centroid-based clustering, integration with F11. **Effort: Small** (pure computation, no LLM calls).

### F13 — Skill Distillation (0%)
New PRD. Depends on F11 + F12. Requires: `AgentSkill` model, LLM-based incremental skill extraction, sleep-time scheduling, `learned_skills` memory block. **Effort: Medium.**

### F14 — Multi-User & Group Memory (0%)
New PRD. One AI identity serving multiple users with per-user private memory, shared group memory, group profile extraction, and cross-user knowledge graph. Requires: `groups`, `group_members`, `group_memories`, `group_profiles` tables; `group_id` on threads/episodes/KG; memory scoping rules; system prompt adaptation; adapter-level participant identification. **Effort: Large.**

---

## Suggested Implementation Priority

Based on effort/impact ratio:

| Priority | Feature | Rationale |
|----------|---------|-----------|
| 1 | **F9** (Episode Upgrade) | Tiny effort (prompt-only), improves all episode quality immediately |
| 2 | **F3 rebaseline** | Live predict-calibrate code is absent despite PRD status claims; decide whether to restore or revise before depending on it |
| 3 | **F8** (Foresight) | Small effort, directly enhances proactive companion |
| 4 | **F7.9 fix** | Medium effort, completes forgetting system |
| 5 | **F10** (User Profile) | Medium effort, builds on existing claims infrastructure |
| 6 | **F4.7 semantic alias dedup** | Medium effort, improves graph quality after entity embeddings are now live |
| 7 | **F11** (Experiences) | Medium effort, unlocks procedural learning pipeline |
| 8 | **F12 → F13** | Depends on F11, completes the learning loop |
| 9 | **F14** (Multi-User) | Large effort, closes the last structural gap vs multi-tenant memory systems |

---

## Dependency Graph

```
F1 (Hybrid Search) ──────────────────────────┐
                                              ├──> F3 (Predict-Calibrate)
F2 (Heat Scoring) ───────────────────────────┐│
F4 (Knowledge Graph) ───────────────────────┐││
                                            ├┤├──> F5 (Sleep-Time Agents)
                                            │││
F6 (Batch Segmentation) ─ standalone        │││
F7 (Forgetting) ─ standalone                │││
F8 (Foresight) ─ standalone                 │││
F9 (Episode Upgrade) ─ standalone           │││
F10 (User Profile) ─ standalone             │││
                                            │││
F10 (User Profile) ──────────────────────────────────> F14 (Multi-User & Group Memory)
                                            │││
F11 (Experience Extraction) ─────────────────┤│
                              │              ││
                              └──> F12 (Clustering) ──> F13 (Skill Distillation)
```

---

## Other Memory Documents

| Document | Purpose |
|----------|---------|
| [Competitor Audit: Letta & Mem0](competitor-audit-letta-mem0.md) | Analysis of competitor memory architectures |
| [Competitor Audit: PRD Corrections Summary](competitor-audit-prd-corrections-summary-2026-03-19.md) | Summary of corrections applied from the competitor audit |
| [Single-User Temporal Memory v2](single-user-temporal-memory-v2.md) | Umbrella PRD for local-first temporal cognitive memory optimized for one long-lived user |
| [Inner Life v1](../presence/inner-life-v1.md) | Continuous affect state, drive-based push initiative, and dynamic memory processes (crystallization, distillation, reconsolidation, dream cycle) — extends F2, F5, F7 |
| [Social Memory Identity Discovery v1](social-memory-identity-discovery-v1.md) | Identity discovery, duplicate-name resolution, and audience-safe memory boundaries for future multi-person conversations |
| [Visual Memory Image Assets v1](visual-memory-image-assets-v1.md) | First-class local image assets with indexing, proactive visual recall, and deletion controls |
