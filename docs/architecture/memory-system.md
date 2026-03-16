# ANIMA Memory System

> Status: implemented
> Last updated: 2026-03-16

## Overview

ANIMA's structured long-term memory lives in SQLite tables inside the
encrypted server Core. Identity, memory, and consciousness state are all
database-backed.

Current storage split:

- `memory_items`: durable facts, preferences, goals, relationships, and focus
- `memory_episodes`: summarized shared experiences
- `memory_daily_logs`: per-turn logs used for reflection and episode generation
- `session_notes`: session-scoped working memory the agent can write during a thread
- `self_model_blocks`: origin, persona, human understanding, user directive,
  plus the five evolving self-model sections
- `agent_profile`: structured lookup (agent name, creator name, relationship)

## Identity Layers

ANIMA's identity is structured as a layered stack, modelled after how a human's
identity works. Some parts are immutable (like a birth registry), some are set
by others, and some evolve through lived experience.

The layers, from most permanent to most fluid:

### Origin (immutable biography)

The origin is ANIMA's birth certificate — ground-truth facts that can never be
changed, just as a person cannot change their date or place of birth.

Contents:

- Name (Anima, or user-chosen)
- Date of birth (when this instance was first created)
- Creator name
- Core nature ("a personal intelligence")

Rendered once at provisioning from `origin.md.j2` and stored in
`self_model_blocks` with `section="soul"`. Immutable after creation.

### Guardrails (ethical rules)

What ANIMA will and will not do. Exists as `guardrails.md.j2`.
Developer-set, enforced at the template level. Separate from identity because
ethics constrain behaviour, they do not define who you are.

### Persona (personality and tone — living layer)

How ANIMA presents itself — communication style, values, temperament. This is
the equivalent of personality traits a person develops over time.

Seeded from a persona template (e.g. `default.md.j2`) at provisioning and
stored in `self_model_blocks` with `section="persona"`. The persona is a
**living layer** that evolves through governed reflection — it doesn't change
after a single conversation, but shifts over weeks and months as evidence
accumulates. The same governance applies as the self-model: version
requirements, overlap checks, growth log evidence.

### Human (agent's understanding of the user — living layer)

The agent's synthesized mental model of the user. Combines two sources:

1. **Profile ground-truth** from the User model (name, age, gender, birthday) —
   set by the user through settings.
2. **Agent-authored understanding** from `self_model_blocks` with
   `section="human"` — seeded at provisioning with basic facts, then updated
   in real-time by the agent via the `update_human_memory` tool as it learns
   through conversation.

This is the fast-path for user knowledge. The agent calls `update_human_memory`
mid-conversation when it learns something important (job, family, interests,
communication preferences). No reflection cycle needed — instant updates,
like how a person naturally updates their understanding of someone during
conversation.

### Identity (relational self-model)

Who ANIMA is _in this specific relationship_. The agent writes and rewrites this
section through reflection and sleep-time compute. It evolves as the
relationship deepens — the same way a person's sense of self changes depending
on the people they are with.

Stored in `self_model_blocks` with `section="identity"`. Promoted into the
system prompt as dynamic identity.

### User directive (user customisation)

The user's instructions to ANIMA — things like "be more casual with me",
"focus on code when I ask technical questions", "call me Leo". This is
the layer the user controls.

Stored in `self_model_blocks` with `section="user_directive"`.

### Why this ordering matters

Like a human:

- You cannot change your birth registry (origin).
- Society sets laws you must follow (guardrails).
- Your personality evolves through experience, but slowly (persona).
- You update your understanding of people you talk to in real-time (human).
- Your self-understanding evolves through deeper reflection (identity).
- Other people can ask you to adjust how you interact with them
  (user directive).

Each layer builds on the ones below it but cannot contradict them. The user
directive can ask ANIMA to be more playful, but it cannot make ANIMA deny its
own name. The identity can evolve, but it cannot override the guardrails.

## Runtime Flow

```text
User message
  |
  v
Agent runtime
  - loads prompt memory blocks from the database:
    - origin (immutable biography from self_model_blocks)
    - persona (living personality from self_model_blocks)
    - human (merged User profile + agent understanding from self_model_blocks)
    - user directive
    - self-model sections (identity, inner_state, working_memory, etc.)
    - emotional context, facts, preferences, tasks, episodes, etc.
  - loads session notes for the current thread
  - assembles system prompt via Jinja2 templates
  |
  v
Agent tool loop (up to 4 steps)
  - agent can call update_human_memory to update <human> block mid-turn
  - agent can call note_to_self, save_to_memory, create_task, etc.
  - terminal tool (send_message) ends the turn
  |
  v
Assistant response returned to the user
  |
  +--> background consolidation
  |     - write daily log
  |     - regex extraction
  |     - optional LLM extraction
  |     - conflict resolution
  |     - embedding backfill
  |
  +--> reflection after inactivity
        - contradiction scan
        - profile synthesis
        - episode generation
```

## Data Model

### `memory_items`

Long-term structured memory.

Categories currently used:

- `fact`
- `preference`
- `goal`
- `relationship`
- `focus`

Important fields:

| Column               | Purpose                                     |
| -------------------- | ------------------------------------------- |
| `content`            | Canonical memory statement                  |
| `importance`         | 1-5 strength score                          |
| `source`             | `extraction`, `user`, or `reflection`       |
| `superseded_by`      | Replaced memory item id, if any             |
| `reference_count`    | Prompt retrieval counter                    |
| `last_referenced_at` | Last retrieval timestamp                    |
| `embedding_json`     | Portable embedding payload stored in SQLite |

### `memory_episodes`

Episode summaries generated from conversation history.

Important fields:

| Column               | Purpose                               |
| -------------------- | ------------------------------------- |
| `date` / `time`      | Episode anchor                        |
| `topics_json`        | Topic labels                          |
| `summary`            | Natural-language episode summary      |
| `emotional_arc`      | Emotional movement across the episode |
| `significance_score` | Relative importance                   |
| `turn_count`         | Number of turns represented           |

### `memory_daily_logs`

Per-turn capture used for later reflection work.

| Column               | Purpose             |
| -------------------- | ------------------- |
| `date`               | Day bucket          |
| `user_message`       | Raw user message    |
| `assistant_response` | Raw assistant reply |

### `session_notes`

Thread-scoped working memory written through tools such as `note_to_self`.

These notes persist within the thread but are not treated as durable identity
memory until promoted into `memory_items`.

### `self_model_blocks`

Database-backed identity and consciousness state.

Current sections used by the runtime:

- `soul` (immutable origin — rendered once from `origin.md.j2`, frozen)
- `persona` (living personality — seeded from template, evolves through reflection)
- `human` (agent's understanding of the user — seeded at provisioning, updated
  mid-conversation via `update_human_memory` tool)
- `user_directive` (user-authored customisation instructions)
- `identity` (relational self-model — who I am with this person)
- `inner_state` (current cognitive state)
- `working_memory` (things held across sessions)
- `growth_log` (evolution history)
- `intentions` (active goals and learned rules)

## Prompt Memory Blocks

The runtime builds prompt context from these sources in
`apps/server/src/anima_server/services/agent/memory_blocks.py`:

**Tier 0 — Core identity (never truncated):**

- `soul` (immutable origin from self_model_blocks)
- `persona` (living personality from self_model_blocks)
- `human` (merged User profile facts + agent understanding from self_model_blocks; `read_only=False`)
- `user_directive` (user customisation from self_model_blocks)

**Tier 1 — Self-model and context (always present):**

- `self_identity` (lifted into the system prompt as dynamic identity)
- `current_focus`
- `thread_summary`
- `self_inner_state`
- `self_working_memory`

**Tier 2 — Retrieval and facts:**

- `relevant_memories` (semantic search results)
- `emotional_context`
- `user_tasks`
- `facts`
- `preferences`
- `self_intentions`

**Tier 3 — Budget-dependent:**

- `goals`
- `relationships`
- `recent_episodes`
- `session_memory`
- `self_growth_log`

`facts`, `preferences`, `goals`, and `relationships` are ranked with a retrieval
score that combines importance, recency, and access frequency before they are
injected into the prompt. Semantic search can also inject query-relevant
memories as a dedicated block.

## Key Files

| File                                                           | Purpose                                                    |
| -------------------------------------------------------------- | ---------------------------------------------------------- |
| `apps/server/src/anima_server/services/agent/memory_store.py`  | CRUD, scoring, dedupe, supersession                        |
| `apps/server/src/anima_server/services/agent/memory_blocks.py` | Prompt block construction                                  |
| `apps/server/src/anima_server/services/agent/self_model.py`    | Self-model section storage, seeding, rendering, expiry     |
| `apps/server/src/anima_server/services/agent/consolidation.py` | Regex extraction, LLM extraction, conflict checks          |
| `apps/server/src/anima_server/services/agent/episodes.py`      | Episodic memory generation                                 |
| `apps/server/src/anima_server/services/agent/reflection.py`    | Inactivity-triggered reflection entrypoint                 |
| `apps/server/src/anima_server/services/agent/sleep_tasks.py`   | Contradiction scan, synthesis, reflection jobs             |
| `apps/server/src/anima_server/api/routes/memory.py`            | Memory CRUD and search API                                 |
| `apps/server/src/anima_server/api/routes/soul.py`              | User directive read/write API (user's customisation layer) |

## API Endpoints

Structured memory routes:

- `GET /api/memory/{user_id}`
- `GET /api/memory/{user_id}/items`
- `POST /api/memory/{user_id}/items`
- `PUT /api/memory/{user_id}/items/{item_id}`
- `DELETE /api/memory/{user_id}/items/{item_id}`
- `GET /api/memory/{user_id}/search`
- `GET /api/memory/{user_id}/episodes`

User directive route (customisation instructions from the user):

- `GET /api/soul/{user_id}`
- `PUT /api/soul/{user_id}`

## Search and Embeddings

Structured memory search supports:

- keyword search directly from SQLite
- semantic search via generated embeddings
- a process-local in-memory vector index for faster lookup

Embeddings are also mirrored into `memory_items.embedding_json` so vault export
and import do not depend on a separate persisted vector-store directory.

## Encryption

Current at-rest behavior is mixed:

- The SQLite database can be encrypted with SQLCipher when
  `ANIMA_CORE_PASSPHRASE` is set and `sqlcipher3` is installed.
- If no passphrase is configured, or `sqlcipher3` is unavailable, the database
  falls back to plain SQLite.
- The user directive (formerly soul) is stored in the database.
- `manifest.json` remains plaintext metadata.

So the memory system is already local-first and mostly database-backed, but the
Core is not yet fully encrypted by default.
