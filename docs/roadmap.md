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

What still needs to happen:

- make encrypted Core startup behavior explicit and fail fast when the expected encryption path is unavailable
- decide whether `manifest.json` should remain plaintext metadata or move under stronger protection

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

## Phase 7: Proactive Companion

Status: complete

Delivered:

- daily brief on app launch (`GET /api/chat/brief`)
- LLM-generated personalized greetings using self-model and emotional context (`GET /api/chat/greeting`)
- quiet nudges for overdue tasks (`GET /api/chat/nudges`)
- home dashboard with task count, journal streak, memory count (`GET /api/chat/home`)
- manual triggers for sleep tasks, consolidation, and deep reflection

## Phase 8: Ambient Presence

Status: future

Goals:

- system tray or menu bar mode
- compact companion surface
- global summon or quick-open flows

## Phase 9: Stronger Semantic Retrieval

Status: complete

Delivered:

- embeddings generated for memories via the server's OpenAI-compatible providers (`ollama`, `openrouter`, `vllm`)
- query-aware semantic retrieval: user messages are embedded and matched against stored memory embeddings at conversation time
- semantically relevant memories injected as a dedicated memory block in the system prompt
- process-local vector index rebuildable from SQLite-backed embeddings
- brute-force fallback when vector index is empty

## Phase 10: Deep Self-Model and Consciousness

Status: complete

Delivered:

- five-section self-model per user: identity, inner_state, working_memory, growth_log, intentions
- self-model seeds automatically on first interaction with sparse, first-meeting content
- identity section evolves through deep monologue and flows into the system prompt as dynamic persona
- emotional intelligence: 12-emotion taxonomy, confidence thresholds, trajectory tracking, rolling signal buffer
- intentional agency: structured goals with lifecycle (detected, active, completed), procedural rules derived from experience
- inner monologue: quick reflection (post-conversation) and deep monologue (sleep-time) update all self-model sections
- consciousness REST API: view and edit self-model sections, emotional state, intentions
- user edits treated as highest-confidence evidence, logged in growth log
- full vault export/import support for consciousness tables

## Phase 11: Embodied Extensions

Status: future

Goals:

- voice-first surfaces
- device and ambient extensions
- mobile, wearable, or robotic shells sharing the same Core

## Implementation Constraints

These remain the product bar even where the current code has not fully reached it:

1. No cloud data storage for personal memory.
2. Portable-by-default local state.
3. Encryption should become the default for user-private data at rest.
4. The Core remains fundamentally single-user.
