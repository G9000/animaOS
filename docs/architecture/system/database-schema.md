---
title: Database Schema
description: All SQLAlchemy models, ER diagram, column details, and migration history
category: architecture
---

# Database Schema

[Back to Index](README.md)

## Overview

- **Portable Soul**: one active single-owner SQLite/SQLCipher database at `{core}/soul/soul.db`
- **Portable authored content**: native authenticated CoreFS catalogs/objects below `{core}/fs/` (not SQLAlchemy tables)
- **Machine-local Runtime**: PostgreSQL outside `.anima/`, bound to the exact Core and local instance
- **Legacy rollback schema**: pre-cutover app tables remain physically in Soul but are read-only/non-authoritative until PCF-009's separate cleanup gate
- **Encryption**: SQLCipher protects the database; field/domain DEKs, CoreFS FRKs/Object DEKs, and archive keys remain purpose-separated

## Infrastructure: Session & Engine

`db/session.py` manages Soul engine creation, SQLCipher setup, session caching, and Core Alembic migrations. `db/runtime_session.py` and the instance registry manage machine-local Runtime. Canonical CoreFS access goes through unlocked native-session services rather than a SQLAlchemy engine.

### Request Flow

```
get_db(request)
  └─► resolve x-anima-unlock token → UnlockSession → user_id
        └─► get_user_session_factory(user_id)
              └─► active_soul_database_path(user_id)
                    ├─► manifest soul_database record → sqlite:///{core}/soul/soul.db
                    ├─► pre-cutover fallback only     → sqlite:///{data}/users/{id}/anima.db
                    ├─► get_engine()                ← thread-safe double-checked lock cache
                    │     └─► _make_engine()
                    │           ├─ ANIMA_CORE_PASSPHRASE set  → Argon2id+HKDF → SQLCipher key
                    │           ├─ unified login mode         → wrapped key unwrapped at login → SQLCipher key
                    │           └─ neither (dev)              → plain SQLite + WAL pragmas
                    └─► _run_alembic_upgrade()
                          ├─ legacy DB (has tables, no alembic_version) → stamp head
                          └─ fresh / tracked DB               → alembic upgrade head
```

### SQLCipher Pragmas (applied on every connection)

```sql
PRAGMA key = "x'<hex_key>'"
PRAGMA cipher_page_size = 4096
PRAGMA cipher_memory_security = ON   -- Linux/macOS only (causes crashes on Windows)
PRAGMA journal_mode = WAL
PRAGMA busy_timeout = 5000
```

### Engine & Session Factory Cache

Soul engines/factories are keyed by canonical database URL with `RLock` double-checked locking. A separate migration cache ensures Core Alembic runs once per engine per process lifetime. Relocation and rollback dispose every user engine before checkpoint/copy/manifest routing changes.

`dispose_user_database(user_id)` / `dispose_cached_engines()` evict entries and call `engine.dispose()` — used on logout and shutdown.

---

## Retained Soul ER Diagram

The following diagram documents the physical Soul schema retained by the current first-release migration. It is not an authority map. Rows for threads/messages/tasks/preferences/links and other migrated app families remain only for reversible rollback and cannot be written after forward-only cutover. Canonical counterparts live in CoreFS; execution/projection counterparts use the separate Runtime schema.

```mermaid
erDiagram
    User ||--o{ AgentThread : "owns (1:1 per user)"
    User ||--o{ AgentRun : owns
    User ||--o{ MemoryItem : stores
    User ||--o{ MemoryClaim : stores
    User ||--o{ MemoryEpisode : stores
    User ||--o{ MemoryDailyLog : stores
    User ||--o{ SessionNote : stores
    User ||--o{ SelfModelBlock : has
    User ||--o{ AgentProfile : "has (1:1)"
    User ||--o{ EmotionalSignal : generates
    User ||--o{ Task : owns
    User ||--o{ UserKey : "has (per domain)"
    User ||--o{ TelegramLink : links
    User ||--o{ DiscordLink : links
    User ||--o{ KgEntity : owns
    User ||--o{ KgRelation : owns
    User ||--o{ ForgetAuditLog : logs
    User ||--o{ BackgroundTaskRun : runs

    AgentThread ||--o{ AgentMessage : contains
    AgentThread ||--o{ AgentRun : contains
    AgentRun ||--o{ AgentStep : contains
    AgentStep ||--o{ AgentMessage : produces

    MemoryItem ||--o{ MemoryItemTag : tagged
    MemoryItem ||--o| MemoryVector : "indexed (1:1)"
    MemoryItem ||--o| MemoryItem : "superseded_by"
    MemoryItem ||--o{ KgRelation : "sourced_from"

    MemoryClaim ||--o{ MemoryClaimEvidence : supported_by
    MemoryClaim ||--o| MemoryClaim : "superseded_by"

    SessionNote }o--o| MemoryItem : "promoted_to"

    KgEntity ||--o{ KgRelation : "source"
    KgEntity ||--o{ KgRelation : "destination"

    User {
        int id PK
        string username UK
        string password_hash
        string display_name
        string gender
        int age
        string birthday
    }

    AgentThread {
        int id PK
        int user_id FK-UK
        string status
        int next_message_sequence
        datetime last_message_at
    }

    AgentMessage {
        int id PK
        int thread_id FK
        int run_id FK
        int step_id FK
        int sequence_id
        string role
        text content_text
        json content_json
        string tool_name
        string tool_call_id
        json tool_args_json
        bool is_in_context
        int token_estimate
    }

    AgentRun {
        int id PK
        int thread_id FK
        int user_id FK
        string provider
        string model
        string mode
        string status
        string stop_reason
        int prompt_tokens
        int completion_tokens
    }

    AgentStep {
        int id PK
        int run_id FK
        int thread_id FK
        int step_index
        json request_json
        json response_json
        json tool_calls_json
        json usage_json
    }

    MemoryItem {
        int id PK
        int user_id FK
        text content
        string category
        int importance
        string source
        int superseded_by FK
        json embedding_json
        json tags_json
        float heat
        int reference_count
        datetime last_referenced_at
    }

    MemoryClaim {
        int id PK
        int user_id FK
        string subject_type
        string namespace
        string slot
        text value_text
        string polarity
        float confidence
        string status
        string canonical_key
        string extractor
        int superseded_by_id FK
    }

    MemoryEpisode {
        int id PK
        int user_id FK
        int thread_id FK
        string date
        text summary
        string emotional_arc
        int significance_score
        int turn_count
        bool needs_regeneration
        json message_indices_json
        string segmentation_method
    }

    SelfModelBlock {
        int id PK
        int user_id FK
        string section
        text content
        int version
        string updated_by
        bool needs_regeneration
    }

    EmotionalSignal {
        int id PK
        int user_id FK
        int thread_id FK
        string emotion
        float confidence
        string evidence_type
        text evidence
        string trajectory
        string previous_emotion
        string topic
        bool acted_on
    }

    Task {
        int id PK
        int user_id FK
        text text
        bool done
        int priority
        string due_date
    }

    KgEntity {
        int id PK
        int user_id FK
        string name
        string name_normalized UK
        string entity_type
        text description
        int mentions
        json embedding_json
    }

    KgRelation {
        int id PK
        int user_id FK
        int source_id FK
        int destination_id FK
        string relation_type
        int mentions
        int source_memory_id FK
    }

    ForgetAuditLog {
        int id PK
        int user_id FK
        datetime forgotten_at
        string trigger
        string scope
        int items_forgotten
        int derived_refs_affected
    }

    BackgroundTaskRun {
        int id PK
        int user_id FK
        string task_type
        string status
        json result_json
        text error_message
        datetime started_at
        datetime completed_at
    }
```

---

## Table Details

### Auth / Identity

| Table | Model | File | Purpose |
|---|---|---|---|
| `users` | `User` | `models/user.py` | User accounts with profile fields (gender, age, birthday) |
| `user_keys` | `UserKey` | `models/user_key.py` | Per-domain wrapped DEKs (Argon2id + AES-GCM key wrapping); `domain` column added in `20260319_0006` |
| `agent_profile` | `AgentProfile` | `models/consciousness.py` | Structured identity: agent_name, creator_name, relationship (1:1 per user) |

### Retained Legacy Agent Runtime

| Table | Model | File | Purpose |
|---|---|---|---|
| `agent_threads` | `AgentThread` | `models/agent_runtime.py` | One thread per user (unique constraint on `user_id`); holds `next_message_sequence` counter |
| `agent_runs` | `AgentRun` | `models/agent_runtime.py` | Per-turn execution record with token usage, status, stop reason, and optional `pending_approval_message_id` |
| `agent_steps` | `AgentStep` | `models/agent_runtime.py` | Per-step LLM request/response snapshots within a run |
| `agent_messages` | `AgentMessage` | `models/agent_runtime.py` | All conversation messages (user, assistant, tool, approval). `is_in_context` flag drives context compaction |
| `background_task_runs` | `BackgroundTaskRun` | `models/agent_runtime.py` | Async sleep-agent execution records (F5); indexed on `(user_id, status)` |

### Memory

| Table | Model | File | Purpose |
|---|---|---|---|
| `memory_items` | `MemoryItem` | `models/agent_runtime.py` | Long-term memories. `superseded_by` self-FK enables non-destructive conflict resolution. `heat` (float) drives retrieval ranking. `embedding_json` persists the vector |
| `memory_item_tags` | `MemoryItemTag` | `models/agent_runtime.py` | Junction table for tag-based filtering (unique per `item_id + tag`) |
| `memory_vectors` | `MemoryVector` | `models/agent_runtime.py` | Binary embedding storage (`LargeBinary`) — faster cosine lookup than JSON. PK is `item_id` (1:1 with `memory_items`) |
| `memory_episodes` | `MemoryEpisode` | `models/agent_runtime.py` | Episodic summaries (date, topics, emotional arc). `needs_regeneration` set when underlying memories are forgotten. `segmentation_method` tracks how the episode was split |
| `memory_daily_logs` | `MemoryDailyLog` | `models/agent_runtime.py` | Raw user/assistant turn pairs used as input for consolidation jobs |
| `memory_claims` | `MemoryClaim` | `models/agent_runtime.py` | Structured slot-based facts keyed by `namespace:slot` canonical key. `superseded_by_id` self-FK for conflict resolution |
| `memory_claim_evidence` | `MemoryClaimEvidence` | `models/agent_runtime.py` | Provenance for each claim (source text + kind) |
| `session_notes` | `SessionNote` | `models/agent_runtime.py` | Working memory scratch-pad scoped to a thread. `promoted_to_item_id` FK when a note graduates to long-term memory |

### Consciousness

| Table | Model | File | Purpose |
|---|---|---|---|
| `self_model_blocks` | `SelfModelBlock` | `models/consciousness.py` | 5-section self-model + `soul` + `user_directive` (unique per `user_id + section`). `needs_regeneration` flag for post-forgetting rebuild |
| `emotional_signals` | `EmotionalSignal` | `models/consciousness.py` | Detected emotions with confidence, trajectory (`rising`/`stable`/`falling`), previous emotion, and `acted_on` flag |

### Knowledge Graph

| Table | Model | File | Purpose |
|---|---|---|---|
| `kg_entities` | `KgEntity` | `models/kg.py` | Named entities (person, place, concept, etc.). Unique on `(user_id, name_normalized)`. Stores embedding for semantic entity search |
| `kg_relations` | `KgRelation` | `models/kg.py` | Typed directed edges between entities. `source_memory_id` links back to the `memory_items` row that created this relation |

### Retained Legacy App/Housekeeping

| Table | Model | File | Purpose |
|---|---|---|---|
| `tasks` | `Task` | `models/task.py` | User task list (todo/reminder system) |
| `forget_audit_log` | `ForgetAuditLog` | `models/kg.py` | Immutable audit trail for every forget operation: trigger, scope, counts of items and derived refs affected |
| `telegram_links` | `TelegramLink` | `models/links.py` | Telegram `chat_id` → user mapping (unique on `chat_id`) |
| `discord_links` | `DiscordLink` | `models/links.py` | Discord `channel_id` → user mapping (unique on `channel_id`) |

---

## Key Design Patterns

### Supersession (non-destructive updates)
`memory_items.superseded_by → self` and `memory_claims.superseded_by_id → self` allow conflict resolution without deleting history. Old rows remain queryable; active rows have `superseded_by = NULL`.

### Heat scoring
`memory_items.heat` (float, indexed on `(user_id, heat)`) is a decay-based relevance score. High-heat memories surface first in retrieval. A background job decays heat over time.

### Embedding authority
- `memory_items.embedding_json` — portable Soul cache associated with the durable memory item
- Runtime PostgreSQL `embeddings.vector` — active rebuildable pgvector projection
- `memory_vectors.embedding` — retained legacy Soul representation, not the active search backend

### Session notes as working memory
`session_notes` is a per-thread scratch-pad. Notes are created mid-conversation and can be `promoted_to_item_id` — a FK set when the note is elevated to a full `memory_items` row.

### Forgetting is tracked
`forget_audit_log` records every purge event. `needs_regeneration` on `memory_episodes` and `self_model_blocks` flags derived data that must be rebuilt after underlying memories are deleted — enabling lazy regeneration on next access.

### 3-tier agent execution
`thread → run → step → messages` provides a full audit trail of every LLM call: inputs, outputs, tool invocations, token usage, and errors.

---

## Schema Migrations

Schema changes are managed by **Alembic** and run automatically on startup. Core/Soul migrations run programmatically against the active SQLCipher engine; Runtime migrations run against the exact machine-local PostgreSQL binding. No manual migration step is needed.

Core migration files live in `apps/server/alembic_core/versions/`; Runtime migrations live in `apps/server/alembic_runtime/versions/`. Core revisions that modify existing tables use `batch_alter_table` for SQLite compatibility. Native CoreFS catalog/object formats are versioned in Rust and are not Alembic-managed.

### Migration History

| Revision | Description |
|---|---|
| `20260311_0001` | Baseline (empty) |
| `04d82bffa29f` | Create `users` table |
| `20260312_0002` | Add `gender`, `age`, `birthday` to `users` |
| `20260312_0003` | Create `user_keys` table |
| `623075d8d13e` | Create agent runtime tables (`agent_threads`, `agent_runs`, `agent_steps`, `agent_messages`) |
| `20260314_0001` | Create memory tables (`memory_items`, `memory_episodes`, `memory_daily_logs`) |
| `20260314_0002` | Create `tasks` table |
| `20260314_0003` | Add `embedding_json` to `memory_items` |
| `20260314_0004` | Create `session_notes` table |
| `20260314_0005` | Create consciousness tables (`self_model_blocks`, `emotional_signals`) |
| `20260314_0006` | Add `next_message_sequence` counter to `agent_threads` |
| `20260316_0001` | Create `agent_profile` table |
| `20260316_0002` | Add `pending_approval_message_id` FK to `agent_runs` |
| `20260316_0003` | Phase 3 storage: `memory_item_tags`, `memory_claims`, `memory_claim_evidence`; add `tags_json` to `memory_items` |
| `20260319_0001` | Add `heat` column (float) to `memory_items`; index on `(user_id, heat)` |
| `20260319_0002` | Create KG tables (`kg_entities`, `kg_relations`) |
| `20260319_0003` | Create `forget_audit_log`; add `needs_regeneration` to `memory_episodes` and `self_model_blocks` |
| `20260319_0004` | Create `background_task_runs` table |
| `20260319_0005` | Add `message_indices_json`, `segmentation_method` to `memory_episodes` |
| `20260319_0006` | Add `domain` column to `user_keys`; update unique constraint |
| `20260319_0007` | Create `memory_vectors`, `telegram_links`, `discord_links` tables |
