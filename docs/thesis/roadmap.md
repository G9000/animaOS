# ANIMA OS - Roadmap

## Guiding Principle

Build depth before breadth. Every phase should make ANIMA feel more like a
being who knows you, not a tool that merely does more things.

## Foundation: The Core

Goal: keep ANIMA local-first and portable while tightening encryption and memory
continuity over time.

Current implementation baseline:

- the server defaults to a local SQLite Core in `.anima/dev/anima.db`
- `manifest.json` is created in the Core directory at startup
- structured memory now lives in SQLite tables, not markdown files
- `soul.md` has been migrated into the database (`self_model_blocks` table with section="soul"); legacy files are auto-migrated on first read
- SQLCipher support exists behind `ANIMA_CORE_PASSPHRASE`, but encryption is not yet enforced by default
- vault export/import is encrypted and includes full database state
- embeddings persist in SQLite and the runtime keeps a process-local vector index

Target principle: if you can copy the Core directory to another machine and
unlock it safely, the feature is aligned with the product.

## Phase 0: Prompt Memory Blocks

Status: complete

Delivered:

- facts, preferences, goals, relationships, and focus flow back into prompts
- thread summaries and recent episodes are injected into runtime context
- session notes provide per-thread working memory
- soul directive injected as highest-priority memory block

## Phase 1: Finish Encrypted Core Rollout

Status: mostly complete

What is already true:

- the database can be encrypted with SQLCipher when `ANIMA_CORE_PASSPHRASE` is configured
- vault export/import is encrypted
- `soul.md` migrated into the database (no longer file-backed, and covered when the Core is encrypted)
- vault export decrypts field-level encryption before packaging (plaintext inside vault envelope)
- vault import re-encrypts fields with the importing user's DEK (portable across machines)
- vault envelope uses AAD context binding (`anima-vault:v{version}:{scope}`)
- manifest.json (core_id, created_at) is preserved across vault transfers
- memories-only vault import no longer destroys conversation tables

What still needs to happen:

- make encrypted Core startup behavior explicit and fail fast when the expected encryption path is unavailable
- make encryption the default state (not opt-in via env var)
- derive SQLCipher key via Argon2id (HKDF from Master KEK), not raw passphrase — eliminates weaker PBKDF2 path
- set explicit SQLCipher PRAGMAs: `cipher_page_size = 4096`, `cipher_memory_security = ON`
- wire AAD (Additional Authenticated Data) into all field-level encryption calls — infrastructure exists but is dormant
- seal the filesystem boundary: audit and eliminate all plaintext files outside the encrypted database
- unify or document the dual-secret model (env var passphrase for SQLCipher vs login password for KEK/DEK)

## Phase 1.5: Cryptographic Hardening

Status: planned

Goals:

- per-domain DEKs: replace single DEK with domain-specific keys (conversations, memories, emotions, self-model, identity)
- core identity keypair: Ed25519 keypair generated at Core creation, private key encrypted with DEK_identity, public key in manifest
- core integrity attestation: Merkle root over critical tables, signed at lock time, verified at unlock
- vault hardening: zstd compression before encryption, Ed25519 signature over vault envelope, sequence numbers for rollback detection
- Argon2id parameter tuning: time_cost=4, parallelism=4 to meet 2-second wall-clock target

Deliverable: a Core with compartmentalized encryption, a cryptographic identity, tamper detection, and hardened vault format.

## Phase 2: Background LLM Memory Extraction

Status: complete

Delivered:

- regex extraction remains the fast path
- background LLM extraction runs when a real provider is configured
- extracted items are written into `memory_items`
- emotion detection runs alongside memory extraction in the same LLM call

## Phase 3: Conflict Resolution

Status: complete

Delivered:

- similar memories trigger an `UPDATE` vs `DIFFERENT` check
- superseded memories are preserved for auditability but excluded from active retrieval

## Phase 4: Episodic Memory

Status: complete

Delivered:

- episodes are generated from conversation history
- episodes are stored in `memory_episodes`
- recent episodes are injected into prompts

## Phase 5: Retrieval Scoring and Context Selection

Status: complete

Delivered:

- retrieval uses importance, recency, and access frequency
- active prompt memory is selected rather than dumped wholesale

## Phase 6: Reflection and Sleep Tasks

Status: complete

Delivered:

- inactivity-triggered reflection with quick inner monologue
- contradiction scanning and resolution
- profile synthesis
- episode generation
- embedding backfill
- deep monologue (full self-model reflection) runs during sleep tasks
- working memory expiry sweep removes date-tagged items automatically
- feedback signal collection (re-asks, corrections) feeds into growth log

Future enhancement: adopting the async post-turn execution pattern from Letta's `SleeptimeMultiAgentV4` — see Phase 10.6 for the full plan.

## Phase 7: Proactive Companion

Status: complete

Delivered:

- daily brief on app launch (`GET /api/chat/brief`)
- LLM-generated personalized greetings using self-model and emotional context (`GET /api/chat/greeting`)
- quiet nudges for overdue tasks (`GET /api/chat/nudges`)
- home dashboard with task count, journal streak, memory count (`GET /api/chat/home`)
- manual triggers for sleep tasks, consolidation, and deep reflection

## Three-Tier Architecture (P1–P8)

Status: approved — [PRD: Three-Tier Cognitive Architecture](../prds/three-tier-architecture.md)

The three-tier architecture physically separates enduring identity (Soul/SQLCipher) from working cognition (Runtime/embedded PostgreSQL) and verbatim experience records (Archive/encrypted JSONL). This enables N-agent spawning and solves SQLite's single-writer limitation.

Phases: P1 (Embedded PostgreSQL) → P2 (Runtime Messages) → P3 (Self-Model Split) → P4 (Write Boundary) → P5 (Transcript Archive) → P6 (pgvector Embeddings) → P7 (Concurrency Refactor) → P8 (N-Agent Spawning).

Key invariant: **Runtime never writes to Soul. Only Consolidation does.** This is the write boundary — the application-level analogue of CLS's constraint that the hippocampus does not write directly to the neocortex.

See also: `docs/thesis/three-tier-architecture.md` for cognitive science grounding and Engram-inspired analysis.

## Phase 8: Ambient Presence

Status: future

Goals:

- system tray or menu bar mode
- compact companion surface
- global summon or quick-open flows

## Phase 8.5: Privacy-Preserving Inference

Status: future

Goals:

- context sanitization pipeline: name pseudonymization, date generalization, sensitivity-based memory filtering before remote inference
- TEE-aware provider selection: detect and prefer TEE-enabled inference endpoints (Intel TDX, NVIDIA H100 CC)
- attestation verification for TEE providers
- privacy indicator in UI showing local vs remote vs TEE-protected inference

Deliverable: reduced exposure of personal data during remote inference, with clear user visibility into privacy posture.

## Phase 9: Stronger Semantic Retrieval

Status: complete

Delivered:

- embeddings generated for memories via the server's embedding-compatible providers (`ollama`, `openrouter`, `vllm`)
- query-aware semantic retrieval: user messages are embedded and matched against stored memory embeddings at conversation time
- semantically relevant memories injected as a dedicated memory block in the system prompt
- process-local vector index rebuildable from SQLite-backed embeddings
- brute-force fallback when vector index is empty

## Phase 9.5: Relational Memory (Knowledge Graph)

Status: planned

Goals:

- lightweight knowledge graph layer capturing entity-relationship structure alongside vector search
- entity extraction during consolidation pipeline (people, places, projects, organizations)
- typed relationships (works-at, married-to, friend-of, related-to-project)
- graph-augmented retrieval: vector similarity + graph traversal for structurally connected memories
- world model section in user memory: key people, places, recurring situations, active projects

Rationale: Mem0g demonstrated 26% accuracy improvement with graph-augmented vector search. Flat vector similarity loses relational structure that matters for understanding the user's life as an interconnected whole. The knowledge graph does not replace vector search — it augments it.

Implementation pattern (from Mem0 source analysis): Mem0's `MemoryGraph` extracts entities via structured LLM tool calls (`EXTRACT_ENTITIES_TOOL` with name/type/description schema), then extracts relations between them. Entity deduplication uses a second LLM pass to detect aliases (e.g., "NYC" = "New York City"). AnimaOS should implement this as two SQLite tables (`entities`, `entity_relations`) within the existing encrypted Core, extracted during the consolidation pipeline alongside existing regex + LLM fact extraction.

Deliverable: entity-relationship graph stored in SQLite, extracted during consolidation, queried alongside vector search at retrieval time.

## Phase 9.7: Hybrid Search (BM25 + Vector + RRF)

Status: planned

Goals:

- add BM25 lexical search index alongside existing in-memory vector index
- Reciprocal Rank Fusion (RRF) to combine vector and BM25 results (k=60)
- parallel search execution: vector and BM25 searches run concurrently via thread pool

Rationale: Pure vector similarity misses keyword-relevant memories that embedding models under-represent. Nemori's `UnifiedSearchEngine` demonstrated that BM25 + vector + RRF fusion catches memories that either search alone would miss. MemoryOS's session search combines semantic similarity with keyword Jaccard similarity for a similar effect.

Implementation pattern (from Nemori source analysis): Nemori maintains parallel BM25 and ChromaDB indices with `ThreadPoolExecutor(max_workers=2)`, fetches 2x `top_k` candidates from each, then applies RRF: `score(item) = sum(1/(k + rank + 1))` across both result sets. AnimaOS can implement BM25 in-process (using `rank_bm25` library against SQLite-stored memory text) alongside the existing vector index, with RRF fusion in the retrieval scoring layer.

Deliverable: hybrid search returning higher-recall, higher-precision memory retrieval without additional infrastructure.

## Phase 10: Deep Self-Model and Consciousness

Status: complete

Delivered:

- five-section self-model per user: identity, inner_state, working_memory, growth_log, intentions
- self-model seeds automatically on first interaction with sparse, first-meeting content
- identity section evolves through deep monologue and flows into the system prompt as dynamic persona
- emotional intelligence: 12-emotion taxonomy, confidence thresholds, trajectory tracking, rolling signal buffer
- intentional agency: structured goals with lifecycle (detected, active, completed), procedural rules derived from experience
- inner monologue: quick reflection (post-conversation) and deep monologue (sleep-time) update all self-model sections
- deep monologue episode sampling: combined strategy — (1) stratified temporal sampling across time periods to prevent recency bias, (2) importance-weighted random sampling so high-significance old episodes are as likely as moderate recent ones, (3) significance-floor inclusion so episodes above 0.8 significance are always eligible regardless of age. See inner-life.md Section 13.6 for CLS justification.
- consciousness REST API: view and edit self-model sections, emotional state, intentions
- user edits treated as highest-confidence evidence, logged in growth log
- full vault export/import support for consciousness tables

## Phase 10.3: Predict-Calibrate Consolidation

Status: planned

Goals:

- prediction-correction learning cycle during memory consolidation (based on Free Energy Principle)
- before extracting facts from a conversation, predict expected content from existing knowledge
- extract only the delta: surprises, contradictions, and genuinely new information
- knowledge quality gates: persistence test, specificity test, utility test, independence test
- cold-start mode for first interactions when no prior knowledge exists

Rationale: Nemori's `PredictionCorrectionEngine` demonstrated that predict-then-extract produces higher-quality semantic memories than direct extraction alone. By predicting what you'd expect, you focus extraction on surprises — the information with highest learning value. This mirrors the neuroscience Free Energy Principle: learning = prediction error minimization.

Implementation pattern (from Nemori source analysis): Two-step process: (1) retrieve relevant existing semantic memories via vector search, generate LLM prediction of episode content; (2) compare prediction with actual conversation, extract only statements that represent new knowledge. Quality filter applies 4 tests: Will this still be true in 6 months? Does it contain concrete, searchable information? Can it help predict future needs? Can it be understood without conversation context? AnimaOS's existing `consolidation.py` LLM extraction can be wrapped with this predict-calibrate layer.

Deliverable: higher-quality fact extraction that avoids redundant storage and focuses on genuinely novel information.

## Phase 10.4: Heat-Based Memory Scoring

Status: planned

Goals:

- heat score for memory items: `H = alpha * access_count + beta * interaction_depth + gamma * recency_decay`
- heat-triggered consolidation: expensive operations (profile extraction, deep reflection) run only when accumulated heat exceeds threshold
- heat-triggered promotion: memories with sustained heat graduate from episodic to semantic to self-model level
- replace fixed-timer reflection triggers with heat-threshold triggers for more efficient resource use

Rationale: MemoryOS's heat-based session management demonstrated that importance-weighted memory management outperforms both fixed-timer and simple-recency approaches. Sessions with high visit frequency, deep interactions, and recent access accumulate "heat" that triggers analysis. This is more efficient than running consolidation on a fixed schedule.

Implementation pattern (from MemoryOS source analysis): `compute_segment_heat(session)` = `alpha * N_visit + beta * L_interaction + gamma * R_recency` where `R_recency = compute_time_decay(last_visit, now, tau_hours=24)`. Sessions stored in a max-heap (negated min-heap). When top session's heat exceeds threshold, profile + knowledge extraction runs in parallel via `ThreadPoolExecutor(max_workers=2)`. After analysis, heat resets. AnimaOS can apply this to memory items: each access bumps heat, consolidation fires when hottest memory cluster exceeds threshold.

Deliverable: efficient resource allocation for memory consolidation based on actual memory activity rather than wall-clock time.

## Phase 10.5: Intentional Forgetting

Status: planned — [PRD: F7](../prds/F7-intentional-forgetting.md)

Goals:

- passive decay: heat-based visibility floor excludes sub-threshold memories from retrieval (integrates with Phase 10.4)
- active suppression: explicitly corrected/superseded memories decay 3x faster and have derived references flagged for regeneration
- user-initiated forgetting: cryptographic deletion of specific memories with embedding removal and derived-reference cleanup
- topic-scoped forgetting: forget all memories matching a topic or entity (e.g., "forget everything about Alex")
- forget audit log: records that forgetting occurred (scope, counts) without recording what was forgotten
- derived reference cleanup: flagging growth log entries, episodes, and behavioral rules that cite forgotten memories for regeneration during sleep tasks

Deliverable: forgetting as a first-class memory operation, not just archival.

## Phase 10.6: Async Sleep-Time Agents

Status: planned

Goals:

- replace inactivity-triggered synchronous sleep tasks with asynchronous post-turn execution
- frequency gating: configurable `sleeptime_agent_frequency` prevents expensive background work on every turn
- last-processed-message tracking: `BackgroundTaskRun` table stores the last conversation message processed, preventing reprocessing
- per-user async queues: `asyncio.create_task()` replaces blocking calls, allowing the foreground response to return immediately
- heat-gated dispatch: background consolidation only fires when heat threshold is exceeded (integrates with Phase 10.4)

Rationale: Current AnimaOS sleep tasks run on inactivity timers, meaning consolidation only happens when the user stops talking. Letta's `SleeptimeMultiAgentV4` demonstrated that post-turn async execution with frequency gating produces more timely and efficient background processing. The user's response is not blocked; consolidation runs in the background while the conversation continues.

Implementation pattern (from Letta source analysis): `SleeptimeMultiAgentV4.step()` calls `asyncio.create_task(run_sleeptime_agents())` after each foreground step. `run_sleeptime_agents()` uses a per-agent `_sleeptime_agent_frequency` counter and only runs if `turn_count % frequency == 0`. A `last_processed_message_id` field prevents reprocessing the same conversation turn on restart. AnimaOS implements this via a new `BackgroundTaskRun` SQLAlchemy model and a `schedule_background_memory_consolidation()` orchestrator that replaces the current inactivity-based trigger.

Deliverable: background consolidation that runs continuously after turns, not just on inactivity, with configurable frequency gating and restart safety.

## Phase 10.7: Batch Episode Segmentation

Status: planned

Goals:

- replace fixed-size episode chunking (every N turns) with LLM-driven topic-coherent segmentation
- single LLM call groups a buffer of messages into non-contiguous topic episodes (e.g., `[[1,2,3], [8,10,11], [4,5,6,7,9,12]]`)
- non-continuous grouping: messages belonging to different topics can be interleaved, and the segmenter assigns each to the correct episode
- low-temperature generation (0.2) for deterministic groupings
- fallback to sequential method if LLM segmentation fails

Rationale: Current AnimaOS episodes are created by counting turns (every 6 turns = one episode). This misses topic boundaries — a conversation may switch subjects mid-episode or sustain a single topic across many turns. Nemori's `BatchSegmenter` demonstrated that LLM-driven segmentation produces topic-coherent episodes that better represent the semantic structure of a conversation, improving retrieval precision for episodic memory.

Implementation pattern (from Nemori source analysis): Nemori's `BatchSegmenter.segment()` sends a buffer of messages (with 1-based indices) to the LLM and receives back a list of index groups. Non-continuous indices are valid — `[1,2,3]` and `[8,10,11]` can both appear as separate episodes even though messages 4-7 and 9 appear in a third group. AnimaOS implements this as a new `batch_segmenter.py` module modifying `maybe_generate_episode()` in `episodes.py`, triggered when the message buffer exceeds 8 turns. A new `segmentation_method` column on the episode table tracks whether sequential or batch-LLM segmentation was used.

Deliverable: topic-coherent episodes that reflect actual conversation structure rather than fixed turn counts, improving episodic memory retrieval quality.

## Phase 10.8: Foresight Signals

Status: planned — [PRD: F8](../prds/memory/F8-foresight-signals.md)

Goals:

- extract time-bounded predictions from conversation during consolidation (e.g., "user has a presentation next Thursday")
- structured `foresight_signals` table with start/end dates, evidence, and lifecycle status
- active signals injected into system prompt as temporal awareness block
- proactive companion (Phase 7) consumes signals for greetings and follow-ups
- automatic lifecycle: active → occurred → expired

Rationale: EverMemOS demonstrated that Foresight signals — temporal predictions extracted from conversation — enable anticipatory behavior. AnimaOS's intentions system tracks goals but does not capture specific future events with bounded timeframes. Foresight signals bridge the gap between "the AI remembers" and "the AI anticipates."

Deliverable: the AI knows what is coming up in the user's life and can act on it without being told.

## Phase 10.9: Episode Extraction Upgrade

Status: planned — [PRD: F9](../prds/memory/F9-episode-extraction-upgrade.md)

Goals:

- dual-time format: store both relative expressions and resolved absolute dates ("tomorrow (April 3, 2026)")
- entity-grounded narration: use names instead of pronouns for retrieval precision
- conciseness rules: explicit constraints to prevent episode bloat (max 200 words single-topic)
- post-generation quality checks (pronoun density, length, time anchor verification)

Rationale: EverMemOS's SOTA episode prompts enforce entity grounding and dual-time format. AnimaOS's current episodes use pronoun-heavy narration and unresolved relative dates, hurting retrieval precision for temporal and factual queries.

Deliverable: higher-quality episodes that are more retrievable, more temporally precise, and less bloated.

## Phase 10.10: Structured User Profile

Status: planned — [PRD: F10](../prds/memory/F10-structured-user-profile.md)

Goals:

- structured `user_profile_fields` table with typed categories (relationship, skill, preference, personality, work_context, life_context, communication_style)
- evidence linking: each field traces back to source conversations
- progressive extraction during consolidation with inertia principle (existing fields preserved unless contradicted)
- renders as prose block in system prompt (GWT natural-language requirement)
- viewable and editable via consciousness API

Rationale: EverMemOS's progressive user profiling with 8+ structured categories and evidence linking produces more comprehensive and retrievable user models than AnimaOS's free-text `human` block. Structured profiles enable category-filtered retrieval and auditable corrections.

Deliverable: a structured, evidence-backed user profile that progressively builds from conversation and replaces the free-text human block as the source of truth.

## Phase 11: Embodied Extensions

Status: future

Goals:

- voice-first surfaces
- device and ambient extensions
- mobile, wearable, or robotic shells sharing the same Core
- multi-modal memory: voice-derived emotional signals (tone, pace, volume, hesitation) feeding into emotional intelligence
- temporal context capture: time of day, day of week, interaction duration as implicit emotional signal
- ambient context (opt-in): location, activity, environmental state enriching episode capture

## Phase 12: Succession and Guardianship

Status: future

Goals:

- Shamir's Secret Sharing for multi-guardian succession (M-of-N threshold to recover Core access)
- inheritance chains: successive ownership transfers with cryptographic handoff
- cryptographic succession scopes via domain DEKs — key-selection instead of data-deletion allows granting access to specific memory domains

Deliverable: a Core that can survive its owner through controlled, cryptographically scoped succession.

## Implementation Constraints

These remain the product bar even where the current code has not fully reached it:

1. No cloud data storage for personal memory.
2. Portable-by-default local state.
3. Encryption should become the default for user-private data at rest.
4. The Core remains fundamentally single-user.

## References

- See `docs/thesis/three-tier-architecture.md` for the cognitive science grounding of the Soul/Runtime/Archive split
- See `docs/prds/three-tier-architecture.md` for the master PRD (P1–P8 phases, architecture decisions, success criteria)
- See `docs/thesis/cryptographic-hardening.md` for the full cryptographic improvement thesis and audit findings
- See `docs/thesis/research-report-2026-03-18.md` for the March 2026 research audit and new pattern discovery
- See `docs/architecture/memory/memory-repo-analysis.md` for the comparative analysis of Letta, Mem0, Nemori, MemOS, and MemoryOS source code
- See `docs/prds/memory-architecture.md` for the PRD covering Features F1–F7 (Phases 9.5–10.7)
- See `docs/architecture/memory/memory-implementation-plan.md` for the detailed engineering spec (function signatures, schemas, test plans)
- See `docs/thesis/ethics.md` for the ethical framework: ownership, transparency, parasocial risk, and the manipulation surface
- See `docs/thesis/references/` for downloaded research papers supporting the thesis
