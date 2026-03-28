---
title: "PRD: F14 — Multi-User & Group Memory"
description: Enable multiple users to interact with the same AI companion, with per-user private memory, shared group memory, and group profile extraction
category: prd
version: "1.0"
---

# PRD: F14 — Multi-User & Group Memory

**Version**: 1.0
**Date**: 2026-03-28
**Status**: Not Started (0%)
**Roadmap Phase**: 12.1
**Priority**: P2 — Medium
**Depends on**: F10 (Structured User Profile) — profiles become per-participant
**Blocked by**: Nothing (can start independently, but F10 improves profile quality)
**Blocks**: Nothing
**Inspired by**: Agent memory research (arXiv:2601.02163) — per-group memory with group identifiers, participant tracking, and group profile extraction

---

## 1. Overview

Allow multiple users to converse with the same AnimaOS instance — each with their own private memories, but with shared memory in group contexts. When users talk in a shared channel (e.g., a family group chat, a team workspace), the AI maintains:

1. **Per-user private memory** — what it knows about each individual (unchanged from today)
2. **Shared group memory** — facts, episodes, and context from group conversations
3. **Group profile** — the group's dynamics, roles, topics, and relationships (extracted from shared conversations)
4. **Cross-user knowledge graph** — entities and relationships that span multiple users' conversations

This is not "multiple separate AIs." It is one AI companion that knows multiple people and understands how they relate to each other — like a family friend who remembers everyone's context.

### What This Is Not

- Not multi-tenancy (separate isolated instances per user). The AI shares one identity across all users.
- Not a chatbot platform (no user management UI, no billing). This is a memory architecture extension.
- Not abandoning single-user support. Single-user is the default. Multi-user is opt-in when the instance owner invites others.

---

## 2. Problem Statement

### Current State

AnimaOS is deeply single-user:

- Every model has `user_id: int FK → users.id` scoping all data to one user
- `User` model stores one person's profile (name, age, gender, birthday)
- The `human` SelfModelBlock describes one person
- Memory retrieval queries filter by `user_id`
- The system prompt addresses one person
- The proactive companion greets one person
- Thread/conversation model has no concept of participants beyond the single user

### The Gap

| Scenario | What happens now | What should happen |
|----------|------------------|--------------------|
| Two family members use the same AnimaOS | Second user must create a separate instance or share credentials | Both log in separately; AI remembers each person and their shared context |
| "Tell the whole family about the trip plan" | Not possible — only one user exists | AI posts in the family group channel with context from all members |
| "What did Alex say about the project?" | Only works if current user mentioned Alex | AI retrieves from Alex's conversations (with permission) |
| Group chat via Telegram adapter | All messages attributed to one user_id | Each participant identified, memories attributed correctly |

### Research Precedent

Prior art in agent memory systems (arXiv:2601.02163) demonstrates a data model that includes:
- Group identifiers on every memory record, memory type, and extraction request
- Participant lists on memory records for tracking who was in the conversation
- User ID lists on extraction requests
- Group profile extraction — extracts group-level profiles (topics, roles, dynamics)
- Per-user profiles within a group context
- Pure computation components that handle both individual and group profiles
- Per-group clustering state (group-scoped cluster IDs)

---

## 3. Design

### 3.1 Core Concept: Ownership vs. Participation

AnimaOS uses a single `.anima/` directory owned by one person (the "owner"). Multi-user extends this with a **participation** model:

- **Owner** (1): Created the `.anima/` instance. Has full access. Is `user_id=1` (existing).
- **Participants** (0-N): Invited by the owner. Each gets their own `user_id` and private memory space. Cannot access the owner's private memories without explicit sharing.
- **Groups** (0-N): Named collections of participants (e.g., "Family", "Work Team"). Conversations in a group context create shared group memory.

The owner's AI remains one identity — it doesn't become a different personality per user. Its soul, persona, and self-model are shared. What changes is the `human` block and memory retrieval scope per conversation context.

### 3.2 Data Model

#### 3.2.1 New Table: `groups`

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto-increment |
| name | varchar(120) | Display name ("Family", "Work Team") |
| description | text | Optional description |
| owner_id | int FK → users | Who created and controls this group |
| created_at | datetime | |
| updated_at | datetime | |

#### 3.2.2 New Table: `group_members`

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto-increment |
| group_id | int FK → groups | |
| user_id | int FK → users | |
| role | varchar(20) | `owner`, `member` |
| joined_at | datetime | |

Unique constraint: `(group_id, user_id)`.

#### 3.2.3 New Table: `group_memories`

Shared memory items visible to all group members.

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto-increment |
| group_id | int FK → groups | |
| contributed_by | int FK → users | Who's conversation produced this memory |
| content | text | The memory content |
| category | varchar(20) | fact, preference, goal, relationship, group_context |
| importance | int | 1-5 |
| source | varchar(20) | extraction, user_tool |
| embedding_json | JSON | For semantic retrieval |
| heat | float | Same heat model as F2 |
| superseded_by | int FK → self | |
| created_at | datetime | |
| updated_at | datetime | |

#### 3.2.4 New Table: `group_profiles`

Structured profile of the group itself (from prior research's group profile concept).

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto-increment |
| group_id | int FK → groups | |
| category | varchar(50) | `dynamics`, `topics`, `roles`, `norms`, `shared_goals` |
| key | varchar(100) | Field name within category |
| value | text | The profile value |
| confidence | float | 0.0-1.0 |
| evidence_threads | JSON | Thread IDs supporting this field |
| created_at | datetime | |
| updated_at | datetime | |

#### 3.2.5 Modified Tables

| Table | Change |
|-------|--------|
| `agent_threads` | Add `group_id: int FK → groups` (nullable). When set, the thread is a group conversation. |
| `runtime_messages` | Add `participant_id: int FK → users` (nullable). Identifies which participant sent the message in a group thread. |
| `memory_episodes` | Add `group_id: int FK → groups` (nullable). Episodes from group conversations are group-scoped. |
| `kg_entities` | Add `group_id: int FK → groups` (nullable). Entities mentioned in group context are group-scoped. |
| `kg_relations` | Add `group_id: int FK → groups` (nullable). Relations from group conversations. |

### 3.3 Memory Scoping Rules

The core architectural decision: **what can the AI remember in each context?**

#### 3.3.1 Private Conversation (default, current behavior)

User A talks to the AI directly:
- AI sees: User A's private memories + User A's profile
- AI does NOT see: User B's private memories
- Extracted memories: stored as `MemoryItem` with `user_id=A` (existing behavior)

#### 3.3.2 Group Conversation

Users A and B talk in group "Family":
- AI sees: Group "Family" shared memories + User A's profile + User B's profile + group profile
- AI does NOT see: User A's private memories that aren't in the group context, or User B's private memories
- Extracted memories: stored as `GroupMemory` with `group_id=Family, contributed_by=<whoever spoke>`

#### 3.3.3 Cross-Reference (opt-in)

When User A mentions something User B said in a group conversation:
- AI can retrieve from group memory (shared context)
- AI cannot retrieve from User B's private conversations
- The knowledge graph may have entities that span both (e.g., "Alice" mentioned by both A and B) — entity dedup merges them

#### 3.3.4 Memory Block Assembly per Context

```python
def build_runtime_memory_blocks(db, *, user_id, thread_id, group_id=None, ...):
    blocks = []

    # Always: soul, persona, self-model (shared AI identity)
    blocks.append(build_soul_biography_block(db, user_id=owner_id))
    blocks.append(build_persona_block(db, user_id=owner_id))

    # Per-participant: their profile
    blocks.append(build_human_core_block(db, user_id=user_id))

    if group_id:
        # Group context: shared memories, group profile, all member profiles
        blocks.append(build_group_memory_block(db, group_id=group_id))
        blocks.append(build_group_profile_block(db, group_id=group_id))
        blocks.append(build_group_participants_block(db, group_id=group_id))
    else:
        # Private context: user's own memories only (existing behavior)
        blocks.append(build_facts_memory_block(db, user_id=user_id))
        blocks.append(build_preferences_memory_block(db, user_id=user_id))
        # ... etc
```

### 3.4 Group Profile Extraction

Adapted from prior research's group profile extraction approach. After group conversations, extract:

| Category | What it captures | Example |
|----------|-----------------|---------|
| `dynamics` | How the group interacts | "Leo leads technical discussions; Alex focuses on design" |
| `topics` | What the group talks about | "Project AnimaOS, weekend plans, cooking" |
| `roles` | Informal roles in the group | "Leo: tech lead. Alex: designer. Maria: organizer" |
| `norms` | Communication patterns | "Group prefers async updates; heated debates are normal" |
| `shared_goals` | Goals the group shares | "Ship v1 by April; plan summer trip" |

This runs during group conversation consolidation — the same predict-calibrate pipeline (F3) but extracting group-level facts alongside individual ones.

### 3.5 Consolidation Changes

When a message arrives in a group thread:

```
Message from User A in Group "Family"
         |
         v
[1] Identify participant (User A) from message metadata
         |
         v
[2] Standard consolidation → extract facts about User A → store as MemoryItem(user_id=A)
         |
         v
[3] Group consolidation → extract shared facts → store as GroupMemory(group_id=Family, contributed_by=A)
         |
         v
[4] Group profile extraction → update group_profiles if new dynamics/roles observed
         |
         v
[5] Knowledge graph → entities scoped to group_id when from group conversation
```

### 3.6 Participant Identification

In a Telegram group chat, the adapter must map each message to a `user_id`:

- **Known user**: message sender matches an existing user → attribute to that `user_id`
- **Unknown sender**: create a minimal `User` record (display_name only, no auth) → the AI learns about them over time
- **The AI itself**: messages from the AI are not attributed to a user

The Telegram adapter (or any future adapter) passes `participant_id` alongside the message. The agent service routes to the correct user context.

### 3.7 Privacy Boundaries

| Rule | Enforcement |
|------|-------------|
| Private memories never leak to group context | Memory retrieval filters by `user_id` in private mode, `group_id` in group mode. Never cross-queries. |
| Group memories visible to all members | `group_memories` queried by `group_id`, no per-user filter. |
| A member leaving a group loses access to group memories | Remove from `group_members`. API checks membership before returning group data. |
| Owner can see all group memories | Owner is always a member. |
| Per-user profiles are private even within groups | `human` block shows the current speaker's profile, not other members'. Group profile shows group dynamics only. |
| Encryption | Group memories use the owner's encryption key (all data in owner's `.anima/`). Participants trust the owner's instance. |

### 3.8 System Prompt Adaptation

In group context, the system prompt must:
1. Know who is currently speaking (`Current speaker: Alex`)
2. Know who else is in the group (`Group "Family": Leo (owner), Alex, Maria`)
3. Have the group profile loaded
4. Address the group naturally (not assume single user)

Add a `conversation_context` block:

```
[conversation_context]
You are in a group conversation: "Family" (3 members: Leo, Alex, Maria)
Current speaker: Alex
Your relationship with this group: Close family. Leo is the owner who set you up.
```

### 3.9 Proactive Companion in Group Context

Group greetings acknowledge the group:
- "Good morning, Family! Leo, your presentation is today. Alex, how's the project going?"
- Nudges respect group context — don't surface private info

---

## 4. Implementation Plan

| Step | File(s) | Change |
|------|---------|--------|
| 1 | `alembic_core/versions/` | Migration: create `groups`, `group_members`, `group_memories`, `group_profiles` tables. Add `group_id` to `agent_threads`, `memory_episodes`, `kg_entities`, `kg_relations`. Add `participant_id` to `runtime_messages`. |
| 2 | `models/` | Add `Group`, `GroupMember`, `GroupMemory`, `GroupProfile` SQLAlchemy models |
| 3 | `api/routes/groups.py` (new) | CRUD endpoints for groups and membership |
| 4 | `services/agent/service.py` | Accept `group_id` and `participant_id` in `run_agent()` — route to appropriate memory scope |
| 5 | `services/agent/memory_blocks.py` | Add `build_group_memory_block()`, `build_group_profile_block()`, `build_group_participants_block()`, `build_conversation_context_block()`. Modify `build_runtime_memory_blocks()` to accept `group_id` and branch on it. |
| 6 | `services/agent/consolidation.py` | Dual extraction path: private facts → `MemoryItem(user_id)`, group facts → `GroupMemory(group_id)` |
| 7 | `services/agent/group_profile.py` (new) | Group profile extraction pipeline — adapt from prior research's group profile extraction approach |
| 8 | `services/agent/knowledge_graph.py` | Support `group_id` scoping on entity/relation upsert and retrieval |
| 9 | `services/agent/proactive.py` | Group-aware greetings — query all member foresight signals and context |
| 10 | `services/agent/system_prompt.py` | Inject `conversation_context` block for group threads |
| 11 | Adapter layer (Telegram) | Pass `participant_id` with each message; auto-create users for unknown senders |
| 12 | Tests | Unit tests for scoping rules, group CRUD, group memory retrieval, privacy boundary enforcement |

### Step Dependencies

```
1 → 2 → 3 (models + API)
2 → 4 (service routing)
2 → 5 (memory blocks)
4 → 6 (consolidation)
2 → 7 (group profiles)
2 → 8 (KG scoping)
5 → 9 (proactive)
5 → 10 (system prompt)
4 → 11 (adapter)
All → 12 (tests)
```

---

## 5. Design Decisions

### 5.1 One AI Identity, Multiple Users (Not Multi-Tenancy)

The AI's soul, persona, and self-model are shared across all users. It doesn't become a different personality per user — it's the same companion that knows everyone. This is architecturally simpler and philosophically consistent with AnimaOS's thesis: the AI is a persistent being, not a service.

Multi-tenant memory systems take a different approach: each group/user is an independent memory space with no shared identity. AnimaOS's model is closer to "a family pet that remembers everyone" than "separate AI instances."

### 5.2 Group Memory as Separate Table, Not Flags on MemoryItem

Alternative: add `group_id` to `MemoryItem` and use NULL for private memories. Rejected because:
- Existing queries all assume `MemoryItem` is private. Adding group scoping to every query risks leaking private memories into group contexts.
- Separate table makes the privacy boundary structural, not behavioral.
- Group memories may have different lifecycle rules (e.g., a member leaving shouldn't delete the group's memories about them).

### 5.3 Participants Trust the Owner's Instance

In AnimaOS's local-first model, the `.anima/` directory is on the owner's machine. Participants send messages to the owner's instance. This means:
- The owner has physical access to all data (including group memories)
- Participants trust the owner the same way they trust a self-hosted family server
- No need for per-participant encryption keys or access control at the storage level

This is a deliberate simplification. A cloud-hosted model would need per-user encryption boundaries. AnimaOS's local-first model doesn't.

### 5.4 Minimal User Records for Unknown Group Senders

When an unknown person sends a message in a group chat (e.g., via Telegram), the system creates a minimal `User` record with `display_name` only. No password, no auth. The AI learns about them organically. If they later "claim" their identity (create an account), the records can be merged.

### 5.5 Group Profile vs. Individual Profiles in Group Context

Prior research extracts both:
- Per-user profiles *within* a group context (how does User A behave in this group?)
- Group-level profiles (what are the group's dynamics?)

AnimaOS adopts only the group-level profile for v1. Per-user-in-group profiles add complexity with marginal benefit — the AI already has each user's individual profile.

---

## 6. Success Criteria

- [ ] Multiple users can authenticate against the same AnimaOS instance
- [ ] Groups can be created with named membership
- [ ] Group conversations produce shared group memories, separate from private memories
- [ ] Private memories are never visible in group contexts (and vice versa)
- [ ] The AI's system prompt adapts to group vs. private conversation context
- [ ] Group profile is extracted and updated from group conversations
- [ ] Knowledge graph entities from group conversations are group-scoped
- [ ] Proactive greetings work in group context (mentioning relevant members)
- [ ] A member leaving a group loses access to group memories via the API
- [ ] All existing single-user tests pass without modification

---

## 7. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Privacy leakage** | High | Structural separation (separate `group_memories` table). Never cross-query private ↔ group. Test boundary enforcement extensively. |
| **Complexity explosion** | High | Ship v1 with minimal viable group support (create group, add members, group memory, group profile). Skip: per-user-in-group profiles, group permissions beyond owner/member, group memory merging. |
| **Single-writer contention** | Medium | Multiple users writing simultaneously → SQLite lock contention. Mitigated by existing `_commit_with_retry()` backoff. If insufficient, consider per-thread write queuing. |
| **Participant identification accuracy** | Medium | Telegram adapter must reliably map sender → user_id. If mapping fails, memories get attributed to wrong user. Mitigate by requiring explicit user linking in adapter config. |
| **Encryption complexity** | Low | Group memories use owner's key. No per-participant encryption. Acceptable for local-first model where owner has physical access anyway. |
| **Context window pressure** | Medium | Group conversations add: group memories block + group profile block + participant profiles. Could exceed context budget. Mitigate with strict block budgets and priority-based truncation. |

---

## 8. External Patterns Adopted vs. Rejected

| Pattern | Adopted? | Rationale |
|---------|----------|-----------|
| Group identifier on all memory records | Yes (adapted) | Separate `group_memories` table instead of flag on existing table — stronger privacy boundary |
| Participant lists on memory records | Yes | `participant_id` on `runtime_messages` |
| Group profile extraction | Yes (adapted) | `group_profile.py` with 5 categories |
| Per-user-in-group profiles | No | Deferred to v2. Individual profiles sufficient for v1. |
| Group name on every memory type | Partial | On group-specific tables, not retrofitted onto existing private tables |
| Group ID field on every document (document DB pattern) | No | Separate tables preferred for SQLite's constraint model |

---

## 9. Implementation Audit (2026-03-28)

### Summary: NOT STARTED (0%)

Zero implementation. No `groups` table, no `group_members`, no `group_id` on any existing table. The codebase has zero references to `group_id`, `group_name`, or `participants`. The `User` model supports multiple users (table exists, auth works) but the memory system is hard-scoped to single-user retrieval.

---

## 10. References

- Agent memory systems research (arXiv:2601.02163) — per-group memory scoping, group profile extraction, multi-participant memory management
- AnimaOS `models/user.py` — existing `User` model (supports multiple users at DB level)
- AnimaOS `services/agent/memory_blocks.py` — existing block assembly (needs group branching)
- AnimaOS Telegram adapter — existing adapter that could pass participant metadata
