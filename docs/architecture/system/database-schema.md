---
title: Database Schema
description: All SQLAlchemy models, ER diagram, column details, and migration history
category: architecture
---

# Database Schema

[Back to Index](README.md)

## Overview

- **19 tables** across 6 model files
- **Per-user SQLite databases**: each user gets their own file at `.anima/dev/users/{id}/anima.db`
- **SQLCipher encryption**: full-database encryption when passphrase is configured
- **Field-level encryption**: sensitive text fields encrypted with per-domain AES-256-GCM DEKs

## ER Diagram

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

    AgentThread ||--o{ AgentMessage : contains
    AgentThread ||--o{ AgentRun : contains
    AgentRun ||--o{ AgentStep : contains

    MemoryItem ||--o{ MemoryItemTag : tagged
    MemoryItem ||--o| MemoryVector : "indexed (1:1)"
    MemoryItem ||--o| MemoryItem : "superseded_by"

    MemoryClaim ||--o{ MemoryClaimEvidence : "supported_by"
    MemoryClaim ||--o| MemoryClaim : "superseded_by"

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
    }

    SelfModelBlock {
        int id PK
        int user_id FK
        string section
        text content
        int version
        string updated_by
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
    }

    Task {
        int id PK
        int user_id FK
        text text
        bool done
        int priority
        string due_date
    }
```

## Table Details

| Table | Model | File | Purpose |
|-------|-------|------|---------|
| `users` | `User` | `models/user.py` | User accounts with profile fields |
| `user_keys` | `UserKey` | `models/user_key.py` | Per-domain wrapped DEKs (Argon2id + AES-GCM key wrapping) |
| `agent_threads` | `AgentThread` | `models/agent_runtime.py` | One thread per user (unique constraint on user_id) |
| `agent_messages` | `AgentMessage` | `models/agent_runtime.py` | All conversation messages (user, assistant, tool, approval). `is_in_context` flag for compaction |
| `agent_runs` | `AgentRun` | `models/agent_runtime.py` | Per-turn execution record with token usage, status, stop reason |
| `agent_steps` | `AgentStep` | `models/agent_runtime.py` | Per-step LLM request/response snapshots within a run |
| `memory_items` | `MemoryItem` | `models/agent_runtime.py` | Long-term memories with category, importance, embedding, supersession chain |
| `memory_item_tags` | `MemoryItemTag` | `models/agent_runtime.py` | Junction table for tag-based filtering |
| `memory_vectors` | `MemoryVector` | `models/agent_runtime.py` | Binary embedding storage for vector index |
| `memory_episodes` | `MemoryEpisode` | `models/agent_runtime.py` | Episodic summaries of conversations (date, topics, emotional arc) |
| `memory_daily_logs` | `MemoryDailyLog` | `models/agent_runtime.py` | Raw user/assistant turn pairs for consolidation |
| `memory_claims` | `MemoryClaim` | `models/agent_runtime.py` | Structured slot-based claims (subject:namespace:slot canonical key) |
| `memory_claim_evidence` | `MemoryClaimEvidence` | `models/agent_runtime.py` | Provenance for claims |
| `session_notes` | `SessionNote` | `models/agent_runtime.py` | Working memory notes (per-session scratch pad) |
| `self_model_blocks` | `SelfModelBlock` | `models/consciousness.py` | 5-section self-model + user_directive + soul (unique per user+section) |
| `agent_profile` | `AgentProfile` | `models/consciousness.py` | Structured identity attributes (agent_name, creator_name, relationship) |
| `emotional_signals` | `EmotionalSignal` | `models/consciousness.py` | Detected emotions with confidence, trajectory, evidence |
| `tasks` | `Task` | `models/task.py` | User task list (todo/reminder system) |
| `telegram_links` | `TelegramLink` | `models/links.py` | Telegram chat_id to user mapping |
| `discord_links` | `DiscordLink` | `models/links.py` | Discord channel_id to user mapping |

## Schema Migrations

Schema changes are managed by **Alembic** and run automatically on startup. When `ensure_user_database()` is called, it runs `alembic upgrade head` programmatically against the per-user SQLCipher engine. No manual migration step is needed.

Migration files live in `apps/server/alembic/versions/`. Migrations that modify existing tables must use `batch_alter_table` (SQLite does not support `ALTER` for constraints or foreign keys).

### Migration History

| Revision | Description |
|----------|-------------|
| `20260311_0001` | Baseline (empty) |
| `04d82bffa29f` | Create `users` table |
| `20260312_0002` | Add user profile fields |
| `20260312_0003` | Create `user_keys` table |
| `623075d8d13e` | Create agent runtime tables (`agent_threads`, `agent_messages`, `agent_runs`, `agent_steps`) |
| `20260314_0001` | Create memory tables (`memory_items`, `memory_episodes`, `memory_daily_logs`, `memory_vectors`) |
| `20260314_0002` | Create `tasks` table |
| `20260314_0003` | Add `embedding_json` to `memory_items` |
| `20260314_0004` | Create `session_notes` table |
| `20260314_0005` | Create consciousness tables (`self_model_blocks`, `emotional_signals`) |
| `20260314_0006` | Add sequence counter to `agent_threads` |
| `20260316_0001` | Create `agent_profile` table |
| `20260316_0002` | Add `pending_approval_message_id` FK to `agent_runs` |
| `20260316_0003` | Phase 3 storage: `memory_item_tags`, `memory_claims`, `memory_claim_evidence`, `tags_json` |
| `20260319_0001` | Add `heat` column to `memory_items` |
| `20260319_0002` | Create KG tables (`kg_entities`, `kg_relations`) |
| `20260319_0003` | Create `forget_audit_log`; add `needs_regeneration` to `memory_episodes` and `self_model_blocks` |
| `20260319_0004` | Create `background_task_runs` table |
| `20260319_0005` | Add `message_indices_json`, `segmentation_method` to `memory_episodes` |
| `20260319_0006` | Add `domain` column to `user_keys`; update unique constraint |
