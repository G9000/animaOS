# Single-User Temporal Memory v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Anima memory into a single-user temporal cognitive memory engine with evidence-backed profile, graph evolution, retrieval routing, salience-aware decay, foresight, and procedural learning.

**Architecture:** Keep SQLCipher soul storage canonical and treat runtime retrieval stores as rebuildable indexes. Add cognitive semantics above the existing memory substrate: evidence, temporal graph relations, structured profile fields, retrieval query plans, salience classes, recurring pattern synthesis, foresight signals, and experience/skill memory. Heavy synthesis runs in sleep-time tasks so the main conversation path remains fast.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, SQLCipher SQLite, embedded runtime PostgreSQL/pgvector where already used, Rust retrieval bindings, Jinja2 prompts, pytest, Alembic, Bun/Nx scripts.

---

## Scope

This is an umbrella implementation plan. It intentionally optimizes for one primary user and one long-lived agent identity. It does not implement multi-user/group memory and does not make Weaviate, Graphiti, Neo4j, or any hosted vector service mandatory.

## Planning Inputs

- PRD: `docs/prds/memory/single-user-temporal-memory-v2.md`
- Existing feature PRDs: F8, F9, F10, F11, F12, F13, and F15 under `docs/prds/memory/`
- Existing architecture docs: `docs/architecture/memory/memory-system.md`
- Existing memory services under `apps/server/src/anima_server/services/agent/`
- Existing durable models in `apps/server/src/anima_server/models/agent_runtime.py`

## File Map

| Area | Main files |
| --- | --- |
| Durable models | `apps/server/src/anima_server/models/agent_runtime.py`, `apps/server/src/anima_server/models/__init__.py` |
| Migrations | `apps/server/alembic/versions/` |
| Evidence | `apps/server/src/anima_server/services/agent/provenance.py`, `evidence_retrieval.py`, `memory_store.py`, `soul_writer.py` |
| Episodes | `apps/server/src/anima_server/services/agent/episodes.py`, `batch_segmenter.py`, `templates/episode_generation.md.j2` |
| Knowledge graph | `apps/server/src/anima_server/services/agent/knowledge_graph.py`, `graph_triplets.py` |
| Profile | `apps/server/src/anima_server/services/agent/claims.py`, `memory_blocks.py`, `sleep_tasks.py`, new `user_profile.py` |
| Retrieval | `apps/server/src/anima_server/services/agent/embeddings.py`, `evidence_retrieval.py`, `tools.py`, new `retrieval_router.py` |
| Heat/salience | `apps/server/src/anima_server/services/agent/heat_scoring.py`, `retrieval_feedback.py`, new `memory_salience.py` |
| Sleep orchestration | `apps/server/src/anima_server/services/agent/sleep_agent.py`, `sleep_tasks.py`, `reflection.py` |
| Foresight | new `foresight.py`, `memory_blocks.py`, proactive services if present |
| Experience learning | new `agent_experience.py`, `experience_clustering.py`, `skill_distillation.py` |
| Tests | `apps/server/tests/test_*memory*.py`, new focused tests named by feature |

## Execution Order

### Phase 0: Reality Baseline And Guardrails

Purpose: make sure the plan starts from the code that actually runs.

- [ ] Audit docs/code drift for predict-calibrate, retrieval routing, evidence coverage, KG export/import, and sleep tasks.
- [ ] Add a small memory eval fixture suite covering factual recall, emotional recall, profile recall, temporal recall, and pattern recall.
- [ ] Fix tiny/high-impact existing gaps before adding new subsystems: heat pool ordering, KG vault export/import, KG entity embeddings if still unused, sleep cursor if still unwired.
- [ ] Update memory docs to reflect actual runtime boundaries.
- [ ] Validation: focused tests plus `bun run test`.

### Phase 1: Evidence And Episode Quality

Purpose: make source-grounded memory the default.

- [ ] Finish F15 operational gaps: backfill existing user vault evidence and expose evidence audit helpers.
- [ ] Upgrade F9 episode generation: dual-time format, named participants, concrete details, concise summaries, and validation.
- [ ] Add deterministic tests for episode prompt context and evidence-preserving summaries.
- [ ] Ensure new profile, graph, foresight, and experience rows can link to evidence.
- [ ] Validation: evidence coverage audit, episode generation tests, provenance tests.

### Phase 2: Temporal Knowledge Graph v2

Purpose: make Graphiti-like temporal semantics native to Anima.

- [ ] Extend KG relations with temporal fields: `observed_at`, `valid_from`, `valid_to`, `confidence`, `status`, and evidence linkage.
- [ ] Add alias and embedding-backed entity deduplication.
- [ ] Add soft evolution relations: `evolves_from`, `supersedes`, or equivalent chaining without deleting useful history.
- [ ] Add graph retrieval helpers for entity neighborhood, relationship history, and "latest belief" resolution.
- [ ] Add migration and portability/export-import support.
- [ ] Validation: migration tests, graph relation lifecycle tests, graph retrieval tests.

### Phase 3: Structured User Profile

Purpose: replace freeform user model drift with typed, evidence-backed profile fields.

- [ ] Implement `UserProfileField` or adapt `MemoryClaim` if it can satisfy F10 without duplicated storage.
- [ ] Define profile categories: identity, relationships, work, preferences, goals, values, constraints, emotional patterns, active projects.
- [ ] Add extraction output for profile updates during consolidation.
- [ ] Add sleep-time profile reconciliation and stale field review.
- [ ] Render a compact profile block in `memory_blocks.py`.
- [ ] Add API endpoints for Open Mind inspection and correction.
- [ ] Validation: profile extraction tests, prompt rendering tests, evidence linkage tests.

### Phase 4: Retrieval Router And Query Plans

Purpose: stop using the same retrieval strategy for every turn.

- [ ] Add `retrieval_router.py` with deterministic intent labels and rule-first classification.
- [ ] Support query plans for factual recall, emotional support, relationship context, project continuity, preference lookup, foresight, contradiction/evolution, and procedural skill recall.
- [ ] Combine sources with a plan-specific strategy: profile, graph, memory items, episodes, transcripts, foresight, experiences, skills.
- [ ] Add trace output so the UI can show which route ran and why.
- [ ] Update `search_long_memory` guidance so tool-driven recall and automatic retrieval do not fight each other.
- [ ] Validation: route classifier tests, retrieval plan tests, recall regression probes.

### Phase 5: Salience-Aware Decay And Soft Contradictions

Purpose: make emotional and identity memories decay differently from casual facts.

- [ ] Add salience fields or sidecar rows: emotional salience, stability class, decay class, relationship proximity, and evidence strength.
- [ ] Update extraction prompts to emit salience signals for life events, grief, identity, relationships, repeated stress, and transient state.
- [ ] Update heat scoring to use decay class rather than one uniform recency curve.
- [ ] Add a soft evolution detector for preference drift and emotional relationship changes.
- [ ] Add sleep-time contradiction/evolution surface reports.
- [ ] Validation: decay math tests, salience extraction tests with mocked LLM output, evolution chain tests.

### Phase 6: Cross-Episode Pattern Synthesis

Purpose: turn episodes into wisdom instead of only searchable summaries.

- [ ] Add a `pattern_synthesis.py` sleep-time task.
- [ ] Query recent and older episodes by time window and topic.
- [ ] Synthesize recurring emotional patterns, repeated goals, avoidance loops, unresolved decisions, and changing preferences.
- [ ] Store patterns as evidence-backed memory/profile/graph updates depending on type.
- [ ] Render only high-confidence active patterns in prompt blocks.
- [ ] Validation: golden fixture tests where patterns require repeated evidence across episodes.

### Phase 7: Foresight Signals

Purpose: give Anima temporal awareness of future commitments and expected outcomes.

- [ ] Implement F8 `ForesightSignal` model and migration.
- [ ] Add extraction during consolidation without adding another LLM call where possible.
- [ ] Resolve relative dates against conversation timestamp.
- [ ] Add lifecycle sweep for active, due, occurred, stale, and cancelled signals.
- [ ] Add retrieval and proactive memory block integration.
- [ ] Validation: date resolution tests, lifecycle tests, prompt block tests.

### Phase 8: Procedural Experience And Skills

Purpose: let Anima learn how to work with this user.

- [ ] Implement F11 agent experience extraction for tool-heavy, corrective, high-feedback, or emotionally delicate turns.
- [ ] Implement F12 stable experience clustering.
- [ ] Implement F13 incremental skill distillation for clusters with enough evidence.
- [ ] Add retrieval blocks where skills outrank individual experiences when confidence is high.
- [ ] Log growth entries for meaningful learned procedures.
- [ ] Validation: mocked extraction tests, clustering tests, distillation tests, prompt rendering tests.

### Phase 9: Optional External Adapters

Purpose: add scale escape hatches without making them required.

- [ ] Define a retrieval backend interface for native, pgvector, and optional external vector engines.
- [ ] Add adapter documentation for Weaviate/Qdrant/LanceDB if needed later.
- [ ] Define a graph backend interface only after native temporal KG semantics are stable.
- [ ] Add rebuild/import/export commands so external indexes can be recreated from SQLCipher.
- [ ] Validation: adapter contract tests with native backend as the reference implementation.

## Milestones

| Milestone | Delivers | Stop condition |
| --- | --- | --- |
| M1 | Evidence baseline and episode quality | Evidence and episode tests pass |
| M2 | Temporal graph and profile | Graph/profile data is evidence-backed and prompt-visible |
| M3 | Retrieval router and salience | Intent-specific retrieval and class-based decay work |
| M4 | Pattern synthesis and foresight | Sleep-time synthesis creates useful pattern and future-event memory |
| M5 | Experience and skill learning | Agent can retrieve reusable learned procedures |
| M6 | Optional adapters | External indexes are pluggable and rebuildable |

## Test Strategy

- Unit tests for pure scoring, routing, date resolution, clustering, and graph lifecycle.
- Migration tests for every new table or column.
- Prompt-rendering tests for profile, pattern, foresight, experience, and skill blocks.
- Mocked LLM tests for extraction parsing and fallback behavior.
- Regression probes for personal recall scenarios.
- Integration smoke for chat, memory save, recall tools, settings, and `GET /health`.

## Verification Commands

Run focused commands during each phase:

```powershell
bun run test
bun run lint
bun run build
bun run db:server:current
```

For backend-only focused work:

```powershell
cd apps/server
python -m pytest tests/<focused-test-file>.py -v
```

## Commit Strategy

Use one commit per completed ticket or tightly related group:

- `docs: plan single-user temporal memory v2`
- `memory: complete evidence backfill operations`
- `memory: add temporal graph relation lifecycle`
- `memory: add structured user profile fields`
- `memory: add retrieval intent router`
- `memory: add salience-aware decay`
- `memory: synthesize cross-episode patterns`
- `memory: add foresight signals`
- `memory: add procedural skill memory`

## Risks

| Risk | Mitigation |
| --- | --- |
| Too much work in one branch | Execute tickets in order and keep phase commits small |
| Prompt blocks become too large | Render compact summaries and route detailed recall through tools |
| LLM extraction becomes noisy | Use strict schemas, quality gates, and evidence-backed tests |
| SQLite write contention | Keep expensive writes in existing sleep-time/sequential paths |
| External adapter distraction | Defer until native semantics are working |

## Execution Handoff

Recommended execution mode: subagent-driven, one child ticket at a time, with review after each ticket. Start with `SUM-001` because it creates the truth baseline and prevents designing against stale assumptions.
