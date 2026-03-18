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

- embeddings generated for memories via the server's OpenAI-compatible providers (`ollama`, `openrouter`, `vllm`)
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

Deliverable: entity-relationship graph stored in SQLite, extracted during consolidation, queried alongside vector search at retrieval time.

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

## Phase 10.5: Intentional Forgetting

Status: planned

Goals:

- passive decay: recency-weighted retrieval priority reduction (already implemented via 30-day half-life)
- active suppression: explicitly corrected/superseded memories have associative connections weakened
- user-initiated forgetting: cryptographic deletion of specific memories, episodes, or conversation segments
- derived reference cleanup: flagging growth log entries and self-model sections that cite forgotten memories for regeneration
- GDPR-aligned third-party memory governance

Deliverable: forgetting as a first-class memory operation, not just archival.

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

- See `docs/thesis/cryptographic-hardening.md` for the full cryptographic improvement thesis and audit findings
- See `docs/thesis/research-report-2026-03-18.md` for the March 2026 research audit and new pattern discovery
- See `docs/thesis/references/` for downloaded research papers supporting the thesis
