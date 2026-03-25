# Three-Tier Architecture + N-Agent Spawning

**Date:** 2026-03-26
**Status:** Approved direction — implementation details pending

---

## Problem

AnimaOS's current architecture uses a single SQLite/SQLCipher database for everything — identity, memory, runtime state, messages. This creates three blockers for N-agent spawning:

1. **SQLite single-writer** — WAL mode allows concurrent reads but only one writer at a time
2. **Per-user turn lock** — `turn_coordinator.py` serializes all turns per user, blocking concurrent agents
3. **Singleton ToolExecutor** — shared mutable delegation state on a process-wide runtime instance

## Solution: Three-Tier Architecture

The system has three conceptually distinct tiers, not two. While "two-store" is technically accurate for databases, the architecture is really **Runtime + Archive + Soul**:

| Tier | Store | Purpose | Durability |
|------|-------|---------|------------|
| **Runtime** | PostgreSQL | Active conversations, spawns, runs, in-flight state | Ephemeral (TTL-pruned) |
| **Archive** | Encrypted JSONL files in `.anima/transcripts/` | Full conversation transcripts for UI playback + deep recall | Retained (user-configurable) |
| **Soul** | SQLCipher (`anima.db`) in `.anima/` | Enduring identity, distilled knowledge, emotional patterns | Permanent, portable |

The key question for placing data: **Does this define enduring identity, or is it just useful data?** Only enduring identity belongs in the soul.

---

## Tier 1: Soul Store — SQLCipher (portable, encrypted)

The `.anima/` directory. This is the AI's identity — what you put on a USB stick.

**Contains:**
- `manifest.json` — crypto metadata, recovery-wrapped keys
- `anima.db` (SQLCipher) — all tables below:

| Table | Purpose | Why it's soul |
|-------|---------|---------------|
| `identity_blocks` | Stable self-narrative, persona, values | Defines who the AI is |
| `growth_log` | How the AI has evolved over time | Long-term character development |
| `memory_items` | Extracted semantic memories | Distilled knowledge about the world and user |
| `episodic_memories` | Consolidated episodes | What happened and how it felt (compressed, emotional) |
| `core_emotional_patterns` | Enduring emotional tendencies | Part of personality, not momentary state |
| `soul_directives` | Soul/persona blocks | Foundational behavioral guidance |
| `user_keys` | Encryption keys (DEKs, recovery-wrapped) | Security infrastructure |
| `user_facts` | Extracted user facts | Long-term knowledge about the user |

**What does NOT belong in soul:**

| Data | Why not | Where instead |
|------|---------|---------------|
| Working memory / working context | Temporary, per-session cognition | PostgreSQL (`working_context`) |
| Active intentions / commitments | In-flight goals, may change any turn | PostgreSQL (`active_intentions`) |
| Current emotional state | Momentary, not enduring pattern | PostgreSQL (`current_emotions`) |
| Knowledge graph nodes/edges | Indexed useful data, not identity | PostgreSQL (or defer decision) |

**Access pattern:** Mostly reads during agent turns. Writes happen ONLY through the consolidation gateway (background, infrequent). Single-writer is fine because consolidation is the sole writer and runs sequentially.

---

## Tier 2: Runtime Store — PostgreSQL (concurrent, ephemeral)

Local PostgreSQL instance. Handles all hot-path concurrent writes.

**Contains:**

| Table | Purpose |
|-------|---------|
| `agent_threads` | Conversation threads |
| `agent_messages` | Chat messages (all roles) |
| `agent_runs` | Turn tracking (main + spawned) |
| `spawn_runs` | Spawned agent tracking (goal, status, result, parent) |
| `spawn_steps` | Per-step metrics for spawns |
| `pending_memory_ops` | Memory writes awaiting consolidation (see schema below) |
| `working_context` | Hot working memory (per-session, temporary) |
| `active_intentions` | In-flight goals and commitments |
| `current_emotions` | Momentary emotional state |
| `agent_compaction_summaries` | Compaction results |
| `embeddings` | pgvector embeddings for semantic search |

**Access pattern:** High-frequency reads and writes from N concurrent agents. PostgreSQL handles this natively with connection pooling and row-level locking.

---

## Tier 3: Archive — Encrypted JSONL (portable, retained)

Full conversation transcripts archived as encrypted files in `.anima/transcripts/`.

```
.anima/
├── anima.db                              (soul)
├── manifest.json
└── transcripts/
    ├── 2026-03-26_thread-14.jsonl.enc    (encrypted JSONL)
    ├── 2026-03-26_thread-14.meta.json    (sidecar index — see below)
    ├── 2026-03-25_thread-13.jsonl.enc
    ├── 2026-03-25_thread-13.meta.json
    └── ...
```

Each line in the JSONL is one message:
```jsonl
{"role":"user","content":"...","ts":"2026-03-26T10:00:00Z"}
{"role":"assistant","content":"...","thinking":"...","ts":"2026-03-26T10:00:05Z","tool_calls":[...]}
{"role":"tool","name":"recall_memory","content":"...","ts":"2026-03-26T10:00:06Z"}
```

Files encrypted using the existing DEK from the vault — same key infrastructure as SQLCipher.

### Transcript Sidecar Index

Each transcript gets a small unencrypted metadata file to avoid decrypt-and-scan on every search:

```json
{
  "thread_id": 14,
  "date_start": "2026-03-26T09:55:00Z",
  "date_end": "2026-03-26T10:45:00Z",
  "message_count": 42,
  "roles": ["user", "assistant", "tool"],
  "keywords": ["project deadline", "scope changes", "frustration"],
  "chunk_offsets": [0, 4096, 8192],
  "episodic_memory_ids": ["ep-2026-03-26-001"]
}
```

The sidecar enables:
- Fast filtering by date range, keywords, thread_id without decrypting
- Chunk-level seeking into large transcripts
- Cross-referencing with episodic memories in the soul

**Note:** Keywords in the sidecar are derived from the conversation content. If keyword exposure is a concern, the sidecar can also be encrypted (at the cost of requiring decryption for filtering). Implementation decision deferred.

### Transcript Lifecycle

1. Conversation happens → messages written to PostgreSQL (active)
2. Consolidation runs:
   - Extracts knowledge → writes to SQLCipher (soul)
   - Exports full transcript → writes encrypted JSONL + sidecar to `.anima/transcripts/`
   - Episodic memory in the soul references the transcript file
3. PostgreSQL messages pruned after configurable TTL (e.g., 7-30 days)
4. Transcript files retained based on user preference (forever, or N months)

---

## Write Boundary Rule

**Runtime (agents, spawns, tools) NEVER writes to SQLCipher directly.**

Only one process writes to the soul store: **Consolidation**. This is a hard architectural boundary, not a convention. Any shortcut that has runtime writing "just this once" to the soul violates the invariant and must be rejected.

```
┌─────────────────────────────────────────────────────┐
│  Runtime Layer (PostgreSQL)                         │
│                                                     │
│  Agent turns, spawns, messages, runs, steps         │
│  ALL writes go here                                 │
│                                                     │
│  Reads from Soul: YES                               │
│  Writes to Soul: NEVER                              │
└──────────────────────┬──────────────────────────────┘
                       │ (one-way)
                       │
              ┌────────▼────────┐
              │  Consolidation  │
              │    Gateway      │
              │                 │
              │  Reads: PG      │
              │  Writes: Soul   │
              │  Writes: Archive│
              └──────┬───┬──────┘
                     │   │
        ┌────────────┘   └────────────┐
        ▼                             ▼
┌───────────────────┐   ┌─────────────────────────┐
│  Soul (SQLCipher) │   │  Archive (.jsonl.enc)   │
│                   │   │                         │
│  Identity,        │   │  Full transcripts,      │
│  knowledge,       │   │  sidecar indexes        │
│  emotions         │   │                         │
└───────────────────┘   └─────────────────────────┘
```

This means:
- `core_memory_append` / `core_memory_replace` → write to `pending_memory_ops` in PostgreSQL
- `save_to_memory` → writes to PostgreSQL
- `recall_memory` → reads from SQLCipher
- `recall_conversation` → reads from PostgreSQL
- `recall_transcript` → reads from `.anima/transcripts/` (Tier 3, rare)
- Background consolidation → reads PostgreSQL, writes to soul + archive

---

## Tiered Retrieval Model

| Tier | What | Source | When |
|------|------|--------|------|
| 0 | Identity, enduring emotions, self-narrative | SQLCipher (always in context) | Every turn |
| 1 | Semantic memories, facts, episodes | SQLCipher (`recall_memory`) | Default recall |
| 2 | Recent/active messages, working context | PostgreSQL (`recall_conversation`) | Current conversation |
| 3 | Verbatim past transcripts | `.anima/transcripts/` (`recall_transcript`) | On-demand, rare |

The system prompt teaches the agent when to use each tier:

```
You have different levels of memory:
- Your core memories and feelings are always with you (you just know them)
- For recent conversations, you can search what was discussed
- For exact wording from past conversations, use recall_transcript
  Think of this as finding a book in a library — you don't browse every shelf,
  you check the catalog first, then pull the specific book you need
```

### recall_transcript Tool

```python
@tool
def recall_transcript(query: str, days_back: int = 30) -> str:
    """Search past conversation transcripts for specific details.
    Use this when you need exact wording or verbatim recall,
    not just the general memory of what happened.
    Returns relevant snippets, not full conversations."""
```

The tool:
1. Reads sidecar indexes to find relevant transcript files (by date, keywords, thread_id)
2. Decrypts and scans only those files
3. Returns matching snippets that fit the context window
4. Never loads entire transcripts into context

---

## Pending Memory Ops

### The Problem

`core_memory_append`/`core_memory_replace` can't write to SQLCipher directly (write boundary rule). They write to PostgreSQL as pending operations. These need strict semantics or they become a swamp.

### Schema

```python
class PendingMemoryOp(Base):
    __tablename__ = "pending_memory_ops"

    id: int                                 # primary key, auto-increment (defines causal order)
    user_id: int                            # FK to users
    op_type: str                            # "append" | "replace"
    target_block: str                       # which memory block (e.g., "human", "persona", "user_facts")
    content: str                            # new content to append, or replacement content
    old_content: str | None                 # for replace: the text being replaced (match key)
    source_run_id: int | None               # which agent run created this op
    source_tool_call_id: str | None         # which tool call created this op
    created_at: datetime                    # timestamp
    consolidated: bool                      # false = pending, true = applied to soul
    consolidated_at: datetime | None        # when consolidation applied it
    failed: bool                            # true if consolidation couldn't apply
    failure_reason: str | None              # why it failed (e.g., old_content not found)
```

### Ordering and Conflict Resolution

- **Causal order**: ops are applied in `id` order (auto-increment guarantees creation order)
- **Append ops**: always succeed — content is appended to the target block
- **Replace ops**: succeed if `old_content` is found in the target block; fail if not (content may have already been modified by a prior op)
- **Failed ops**: marked `failed=true` with `failure_reason`. NOT retried automatically — consolidation logs a warning. The knowledge was already captured in the conversation context; it's not lost, just not promoted.
- **Dedupe**: `source_tool_call_id` prevents duplicate ops if consolidation is re-run (idempotent)

### Cross-Conversation Continuity

Within a conversation, the agent sees its own `core_memory_append`/`core_memory_replace` calls in the context window (Tier 2). No special handling needed.

Across conversations — if the user starts a new chat before consolidation runs — pending ops are loaded into the system prompt alongside soul data:

```python
# At turn start, load both soul + pending ops
soul_blocks = load_from_sqlcipher(user_id)
pending_ops = load_from_postgres(user_id, consolidated=False, failed=False)
# Pending ops rendered as a supplementary memory block
```

Consolidation promotes pending ops to the soul store and marks them `consolidated=true`.

---

## N-Agent Spawning Architecture

### Spawn Lifecycle

1. Main agent calls `spawn_task(goal, context)` tool mid-turn
2. `SpawnManager` creates a `SpawnRun` record in PostgreSQL
3. `safe_create_task()` fires an asyncio task (not awaited, GC-protected)
4. Spawned agent gets:
   - **Read-only snapshot** of memory blocks from SQLCipher (frozen at spawn time)
   - Its own `AgentThread` in PostgreSQL
   - Its own `ToolExecutor` instance (no shared mutable state)
   - Shared `OpenAICompatibleChatClient` (httpx is concurrent-safe)
   - Semaphore-gated LLM access
5. Spawned agent runs its step loop, writes to PostgreSQL
6. On completion, updates `SpawnRun.status` and `SpawnRun.result`
7. Main agent sees results via `check_spawns()` tool or on next turn

**Snapshot vs live read (resolved):** Spawns get a **snapshot** of soul data frozen at spawn time. This avoids races where consolidation updates the soul mid-spawn. The snapshot may be slightly stale, but spawns are short-lived tasks — staleness of seconds to minutes is acceptable.

### Concurrency Model

```
Per-Thread Locking (replaces per-user locking):

Main Thread Lock ─── protects main conversation
Spawn-1 Lock ─────── protects spawn 1's thread
Spawn-2 Lock ─────── protects spawn 2's thread
...
Spawn-N Lock ─────── protects spawn N's thread

All run concurrently. No cross-lock dependencies.
```

**LLM concurrency control:**
```python
_llm_semaphore = asyncio.Semaphore(settings.agent_max_concurrent_spawns)

async def _gated_llm_call(adapter, request):
    async with _llm_semaphore:
        return await adapter.invoke(request)
```

### New Components

#### 1. SpawnManager

```python
class SpawnManager:
    _active_tasks: set[asyncio.Task]    # GC protection
    _semaphore: asyncio.Semaphore       # LLM concurrency gate

    async def spawn(self, goal: str, context: str, parent_thread_id: int, ...) -> str:
        """Create a spawn, fire asyncio task, return spawn_run_id."""

    async def check(self, spawn_run_id: str) -> SpawnStatus:
        """Check spawn status and result."""

    async def cancel(self, spawn_run_id: str) -> bool:
        """Cancel a running spawn."""

    async def list_active(self, parent_thread_id: int) -> list[SpawnStatus]:
        """List all active spawns for a thread."""
```

#### 2. SpawnRun Model (PostgreSQL)

```python
class SpawnRun(Base):
    __tablename__ = "spawn_runs"

    id: int                             # primary key
    parent_thread_id: int               # FK to agent_threads
    spawn_thread_id: int                # FK to agent_threads (spawn's own thread)
    goal: str                           # what the spawn is trying to do
    context: str                        # relevant context passed from parent
    status: str                         # pending | running | completed | failed | cancelled
    result: str | None                  # final output from the spawn
    error: str | None                   # error message if failed
    created_at: datetime
    completed_at: datetime | None
    steps_completed: int                # counter
    token_usage: dict | None            # JSON usage stats
```

#### 3. New Tools

```python
@tool
def spawn_task(goal: str, context: str = "") -> str:
    """Spawn a background agent to work on a task in parallel.
    Returns a spawn ID that can be checked later."""

@tool
def check_spawns() -> str:
    """Check the status of all active spawned tasks.
    Returns a summary of each spawn's progress and any completed results."""

@tool
def cancel_spawn(spawn_id: str) -> str:
    """Cancel a running spawned task."""
```

#### 4. safe_create_task (from Letta pattern)

```python
_background_tasks: set[asyncio.Task] = set()

def safe_create_task(coro, *, label: str = "background") -> asyncio.Task:
    """Fire-and-forget asyncio task with GC protection."""
    async def wrapper():
        try:
            await coro
        except asyncio.CancelledError:
            logger.info(f"Spawn {label} cancelled")
        except Exception:
            logger.exception(f"Spawn {label} failed")

    task = asyncio.create_task(wrapper(), name=f"spawn:{label}")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
```

### What Spawned Agents Can Do

Spawned agents get a **subset** of tools:

| Tool | Available to Spawns? | Notes |
|------|---------------------|-------|
| `send_message` | No | Spawns don't talk to user directly |
| `recall_memory` | Yes | Read-only snapshot from SQLCipher |
| `recall_conversation` | Yes | Read from PostgreSQL (own thread) |
| `recall_transcript` | Yes | Read-only, searches encrypted JSONL |
| `core_memory_append` | Main only | Writes pending op to PostgreSQL |
| `core_memory_replace` | Main only | Writes pending op to PostgreSQL |
| `save_to_memory` | Yes | Writes to PostgreSQL (spawn's thread) |
| `spawn_task` | No | Spawns don't spawn (prevent recursion for now) |
| `report_result` | Yes | Write result back to SpawnRun |

**Important:** `core_memory_append` and `core_memory_replace` do NOT write to SQLCipher directly. They write a pending memory operation to PostgreSQL. The consolidation gateway picks these up and applies them to the soul store. From the agent's perspective, the tools behave the same — but the write goes through the gateway.

The `report_result` tool is spawn-only:

```python
@tool
def report_result(result: str) -> str:
    """Report your findings back to the main agent. This ends your task."""
```

### Spawn-Aware System Prompt

Spawned agents get a modified system prompt:

```
You are a background worker for ANIMA. You have been spawned to complete a specific task.

Your goal: {goal}

Context from the main agent: {context}

You have access to memory and conversation search but cannot modify core memory
or send messages to the user. When you have completed your task, use the
report_result tool to send your findings back.
```

---

## Changes to Existing Code

### turn_coordinator.py
- Change from per-user locks to per-thread locks
- `get_thread_lock(thread_id)` replaces `get_user_lock(user_id)`
- Main conversation thread and spawn threads lock independently

### executor.py
- Remove mutable `_tool_delegate` / `_delegated_tool_names` from `ToolExecutor`
- Pass delegation as arguments through the invoke chain
- OR create `ToolExecutor` per-invocation (lightweight — it's just a dict of tool references)

### service.py
- `get_or_build_runner()` returns a base runtime; spawns create lightweight copies
- `_execute_agent_turn()` acquires thread lock instead of user lock
- New `_execute_spawn_turn()` function for spawn lifecycle

### llm.py
- `create_llm()` stays as singleton (httpx client is concurrent-safe)
- Add semaphore gating in the adapter layer

### db/session.py
- Add async PostgreSQL engine (asyncpg + SQLAlchemy async)
- Keep SQLCipher engine for soul store
- New `get_runtime_session()` for PostgreSQL
- Existing `get_db()` continues to serve SQLCipher for soul data

### self_model.py
- Split current `self_model_blocks` into:
  - `identity_blocks` (soul) — stable self-narrative, values
  - `working_context` (runtime) — temporary per-session cognition
  - `active_intentions` (runtime) — in-flight goals
  - `growth_log` (soul) — long-term character development

### consolidation.py
- Reads messages + pending ops from PostgreSQL
- Writes extracted memories to SQLCipher
- Exports transcripts to `.anima/transcripts/`
- Generates sidecar index metadata
- Marks pending ops as consolidated
- One-directional flow, no consistency issues

### embeddings.py
- Migrate from in-memory vector store to pgvector
- Embeddings persisted in PostgreSQL, survive restarts
- Remove `embedding_json` column from SQLCipher `memory_items` (or keep as cache)

---

## Consolidation Gateway Robustness

In this architecture, consolidation is load-bearing — it's the only way data enters the soul. Requirements:

- **Health monitoring**: track last successful run per user, alert if consolidation falls behind a configurable threshold
- **Retry logic**: failed consolidation retries with exponential backoff
- **Idempotent writes**: `source_tool_call_id` on pending ops prevents duplicate application on re-run
- **Ordered processing**: pending ops applied in `id` order (auto-increment = causal order)
- **Partial failure tolerance**: if one pending op fails (e.g., replace can't find old_content), mark it failed and continue processing remaining ops
- **Transcript export**: runs after knowledge extraction, exports encrypted JSONL + sidecar to `.anima/transcripts/`

Consolidation does NOT need to be real-time. A delay of seconds to minutes is acceptable — the agent has the context window for immediate continuity, and pending ops for cross-conversation continuity. The soul is the long-term store, not the hot path.

### Edge Cases

- **Long consolidation delay**: if consolidation is delayed for hours, pending ops accumulate in PostgreSQL. The agent still functions — soul data is supplemented by pending ops loaded at turn start. No data loss, just delayed promotion.
- **Consolidation crash mid-run**: idempotent ops (dedupe by `source_tool_call_id`) mean re-running is safe. Partially exported transcripts should be atomic (write to `.tmp`, rename on success).
- **Concurrent consolidation runs**: prevented by a PostgreSQL advisory lock per user. Only one consolidation run per user at a time.

---

## Configuration

```python
# New settings
agent_max_concurrent_spawns: int = 10           # LLM semaphore size
agent_spawn_timeout: float = 300.0              # per-spawn timeout (seconds)
agent_spawn_max_steps: int = 4                  # max steps per spawn
agent_spawn_recursive: bool = False             # allow spawns to spawn (future)
runtime_database_url: str = "postgresql+asyncpg://localhost/anima_runtime"
transcript_retention_days: int = -1             # -1 = forever
message_ttl_days: int = 30                      # prune PostgreSQL messages after this
consolidation_health_threshold_minutes: int = 30 # alert if no consolidation in this window
```

## Data Flow Summary

```
User message
    |
    v
PostgreSQL: create run, append message
    |
    v
Agent turn
    reads: soul from SQLCipher + pending ops from PostgreSQL
    reads: active messages from PostgreSQL
    reads (rare): transcripts from .anima/transcripts/ (Tier 3)
    writes: ONLY to PostgreSQL
    |
    ├──> Spawn A (asyncio.Task, snapshot of soul) ──> PostgreSQL writes only
    ├──> Spawn B (asyncio.Task, snapshot of soul) ──> PostgreSQL writes only
    └──> Spawn C (asyncio.Task, snapshot of soul) ──> PostgreSQL writes only
    |
    v
Consolidation gateway (background, async, advisory-locked per user)
    |
    ├── reads: messages, pending ops, spawn results from PostgreSQL
    ├── writes: memories, episodes, emotions to SQLCipher (soul)
    ├── writes: transcript + sidecar to .anima/transcripts/ (archive)
    └── marks: pending ops as consolidated, messages eligible for TTL pruning
    |
    v
PostgreSQL messages pruned after TTL
```

## Migration Path

1. Add PostgreSQL dependency and async engine
2. Split `self_model_blocks` into identity (soul) vs working context/intentions (runtime)
3. Create runtime tables in PostgreSQL (messages, threads, runs, spawns, pending ops, working context)
4. Move message read/write to PostgreSQL
5. Keep SQLCipher for soul data (no changes to encryption layer)
6. Implement `PendingMemoryOp` schema and consolidation integration
7. Add SpawnManager + spawn tools
8. Change turn_coordinator to per-thread locking
9. Fix ToolExecutor shared state (per-invocation or pass delegation as args)
10. Add LLM semaphore
11. Migrate embeddings to pgvector
12. Add transcript export with sidecar indexes

## What Stays the Same

- SQLCipher encryption for soul data
- `.anima/` directory as the portable core (manifest.json + anima.db + transcripts/)
- Recovery phrase system (BIP39)
- All consciousness features (self-model, emotions, inner monologue)
- LLM provider abstraction (OpenAI-compatible client)
- System prompt architecture
- Consolidation logic (reads from different source, same extraction pipeline)

## Open Questions

1. **Spawn recursion** — Should spawns be able to spawn? Disabled initially to prevent runaway resource usage. Could enable later with a depth limit.
2. **Result merging** — How does the main agent incorporate spawn results? Current design: spawn results appear as a memory block in the next turn's context. Alternative: inject as a system message.
3. **Spawn UI** — How does the frontend show spawn progress? WebSocket events for spawn lifecycle (created, running, completed, failed).
4. **PostgreSQL deployment** — Bundle with the app? Docker compose? Require user install? Depends on deployment target.
5. **Sidecar encryption** — Should transcript sidecar indexes be encrypted? Unencrypted enables fast filtering but leaks keywords. Encrypted is secure but requires decryption for every search. Decision deferred.
6. **Knowledge graph placement** — Currently listed in soul store. May be better as runtime data (indexed useful data, not identity). Needs the identity filter applied.
