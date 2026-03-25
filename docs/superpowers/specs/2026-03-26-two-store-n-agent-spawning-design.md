# Two-Store Architecture + N-Agent Spawning

**Date:** 2026-03-26
**Status:** Final draft — awaiting approval

---

## Problem

AnimaOS's current architecture uses a single SQLite/SQLCipher database for everything — identity, memory, runtime state, messages. This creates three blockers for N-agent spawning:

1. **SQLite single-writer** — WAL mode allows concurrent reads but only one writer at a time
2. **Per-user turn lock** — `turn_coordinator.py` serializes all turns per user, blocking concurrent agents
3. **Singleton ToolExecutor** — shared mutable delegation state on a process-wide runtime instance

## Solution: Two-Store Architecture

Split the database into two stores based on purpose:

### Store 1: SQLCipher — The Soul (portable, encrypted)

The `.anima/` directory. This is the AI's identity — what you put on a USB stick.

**Contains:**
- `manifest.json` — crypto metadata, recovery-wrapped keys
- `anima.db` (SQLCipher) — all tables below:

| Table | Purpose |
|-------|---------|
| `self_model_blocks` | Identity, inner state, working memory, growth log, intentions |
| `memory_items` | Extracted semantic memories with embeddings |
| `episodic_memories` | Consolidated episodes |
| `emotional_signals` | Emotional signal history |
| `soul_directives` | Soul/persona blocks |
| `user_keys` | Encryption keys (DEKs, recovery-wrapped) |
| `user_facts` | Extracted user facts |
| `knowledge_graph_*` | Knowledge graph nodes/edges |

**Access pattern:** Mostly reads during agent turns. Writes happen during consolidation (background, infrequent). Single-writer is fine because consolidation is the only writer and it runs sequentially.

### Store 2: PostgreSQL — The Runtime (concurrent, ephemeral)

Local PostgreSQL instance. Handles all hot-path concurrent writes.

**Contains:**

| Table | Purpose |
|-------|---------|
| `agent_threads` | Conversation threads |
| `agent_messages` | Chat messages (all roles) |
| `agent_runs` | Turn tracking (main + spawned) |
| `spawn_runs` | Spawned agent tracking (goal, status, result, parent) |
| `spawn_steps` | Per-step metrics for spawns |
| `agent_compaction_summaries` | Compaction results |
| `bm25_tokens` | Full-text search index |
| `embeddings` | pgvector embeddings for semantic search |

**Access pattern:** High-frequency reads and writes from N concurrent agents. PostgreSQL handles this natively with connection pooling and row-level locking.

### Write Boundary Rule

**Runtime (agents, spawns, tools) NEVER writes to SQLCipher directly.**

Only one process writes to the soul store: **Consolidation**. It is the sole gateway between runtime and core. This is a hard architectural boundary, not a convention.

```
┌─────────────────────────────────────────────────────┐
│  Runtime Layer (PostgreSQL)                         │
│                                                     │
│  Agent turns, spawns, messages, runs, steps         │
│  ALL writes go here                                 │
│                                                     │
│  Reads from SQLCipher: YES (memory blocks, soul)    │
│  Writes to SQLCipher: NEVER                         │
└──────────────────────┬──────────────────────────────┘
                       │ (one-way)
                       │
              ┌────────▼────────┐
              │  Consolidation  │
              │    Gateway      │
              │                 │
              │  Reads: PG      │
              │  Writes: Soul   │
              └────────┬────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│  Soul Store (SQLCipher)                             │
│                                                     │
│  Memories, episodes, emotions, identity, facts      │
│  ONLY consolidation writes here                     │
│  Everyone reads                                     │
└─────────────────────────────────────────────────────┘
```

This means:
- `core_memory_append` / `core_memory_replace` → write to PostgreSQL as pending memory operations, consolidated later
- `save_to_memory` → writes to PostgreSQL
- `recall_memory` → reads from SQLCipher
- `recall_conversation` → reads from PostgreSQL
- Background consolidation → reads PostgreSQL messages/pending ops, extracts knowledge, writes to SQLCipher

### Messages

**Active messages live in PostgreSQL.** Messages are runtime data during conversation.

**Full transcripts are archived as encrypted JSONL files in `.anima/transcripts/`.** After consolidation processes a conversation, the full transcript is exported and encrypted with the same DEK used for SQLCipher. These files serve two purposes:

1. **UI playback** — the frontend can render full conversation history
2. **Deep recall** — the agent can search archived transcripts when it needs verbatim detail (rare, on-demand)

The agent does NOT routinely read from transcripts. It uses them like a human uses a library — the soul (catalog) tells it which transcript (book) is relevant, then it opens that specific file and searches for the detail it needs.

#### Tiered Retrieval Model

| Tier | What | Source | When |
|------|------|--------|------|
| 0 | Identity, emotions, self-model | SQLCipher (always in context) | Every turn |
| 1 | Semantic memories, facts, episodes | SQLCipher (recall_memory) | Default recall |
| 2 | Recent/active messages | PostgreSQL (recall_conversation) | Current conversation |
| 3 | Verbatim past transcripts | `.anima/transcripts/*.jsonl.enc` (recall_transcript) | On-demand, rare |

The system prompt teaches the agent when to use each tier:

```
You have different levels of memory:
- Your core memories and feelings are always with you (you just know them)
- For recent conversations, you can search what was discussed
- For exact wording from past conversations, use recall_transcript
  Think of this as finding a book in a library — you don't browse every shelf,
  you check the catalog first, then pull the specific book you need
```

#### Transcript Archive Format

Files stored as encrypted JSONL in `.anima/transcripts/`:

```
.anima/
├── anima.db                              (soul)
├── manifest.json
└── transcripts/
    ├── 2026-03-26_thread-14.jsonl.enc    (encrypted JSONL)
    ├── 2026-03-25_thread-13.jsonl.enc
    └── 2026-03-20_thread-10.jsonl.enc
```

Each line in the JSONL is one message:
```jsonl
{"role":"user","content":"...","ts":"2026-03-26T10:00:00Z"}
{"role":"assistant","content":"...","thinking":"...","ts":"2026-03-26T10:00:05Z","tool_calls":[...]}
{"role":"tool","name":"recall_memory","content":"...","ts":"2026-03-26T10:00:06Z"}
```

Files are encrypted using the existing DEK (data encryption key) from the vault — same key infrastructure as SQLCipher, consistent security boundary.

#### Transcript Lifecycle

1. Conversation happens → messages written to PostgreSQL (active)
2. Consolidation runs:
   - Extracts knowledge → writes to SQLCipher (soul)
   - Exports full transcript → writes encrypted JSONL to `.anima/transcripts/`
   - Episodic memory in the soul references the transcript file for deep recall
3. PostgreSQL messages pruned after configurable TTL (e.g., 7-30 days)
4. Transcript files retained based on user preference (forever, or N months)

#### recall_transcript Tool

```python
@tool
def recall_transcript(query: str, days_back: int = 30) -> str:
    """Search past conversation transcripts for specific details.
    Use this when you need exact wording or verbatim recall,
    not just the general memory of what happened.
    Returns relevant snippets, not full conversations."""
```

The tool:
1. Searches episodic memories in the soul to identify which transcript files are relevant
2. Decrypts and scans only those files
3. Returns matching snippets that fit the context window
4. Never loads entire transcripts into context

| Data | Store | Why |
|------|-------|-----|
| Active chat messages | PostgreSQL | Hot path, concurrent writes from N agents |
| Archived transcripts | `.anima/transcripts/*.jsonl.enc` | Portable, encrypted, UI playback + deep recall |
| Extracted semantic memories | SQLCipher | Permanent knowledge, portable |
| Episodic memories | SQLCipher | Consolidated, references transcript files |
| Emotional signals | SQLCipher | Extracted during consolidation |
| User facts | SQLCipher | Extracted during consolidation |
| Self-model updates | SQLCipher | Written by consolidation only |
| Spawn results | PostgreSQL | Runtime tracking |
| Compaction summaries | PostgreSQL | Runtime optimization |

### Data Flow

```
User message
    |
    v
PostgreSQL: create run, append message
    |
    v
Agent turn
    reads: soul from SQLCipher, history from PostgreSQL
    reads (rare): transcripts from .anima/transcripts/ (Tier 3 deep recall)
    writes: ONLY to PostgreSQL
    |
    ├──> Spawn A (asyncio.Task) ──> PostgreSQL writes only
    ├──> Spawn B (asyncio.Task) ──> PostgreSQL writes only
    └──> Spawn C (asyncio.Task) ──> PostgreSQL writes only
    |
    v
Consolidation gateway (background, async)
    |
    ├── reads: messages, spawn results from PostgreSQL
    ├── writes: memories, episodes, emotions to SQLCipher (soul)
    └── writes: full transcript to .anima/transcripts/*.jsonl.enc (archive)
    |
    v
PostgreSQL messages pruned after TTL
```

The flow is strictly one-directional: PostgreSQL (live) → Consolidation → SQLCipher (soul) + encrypted transcripts (archive). Runtime never writes to soul or transcripts. This eliminates consistency issues entirely.

---

## N-Agent Spawning Architecture

### Spawn Lifecycle

1. Main agent calls `spawn_task(goal, context)` tool mid-turn
2. `SpawnManager` creates a `SpawnRun` record in PostgreSQL
3. `safe_create_task()` fires an asyncio task (not awaited, GC-protected)
4. Spawned agent gets:
   - Read-only snapshot of memory blocks (from SQLCipher)
   - Its own `AgentThread` in PostgreSQL
   - Its own `ToolExecutor` instance (no shared mutable state)
   - Shared `OpenAICompatibleChatClient` (httpx is concurrent-safe)
   - Semaphore-gated LLM access
5. Spawned agent runs its step loop, writes to PostgreSQL
6. On completion, updates `SpawnRun.status` and `SpawnRun.result`
7. Main agent sees results via `check_spawns()` tool or on next turn

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
| `recall_memory` | Yes | Read-only from SQLCipher |
| `recall_conversation` | Yes | Read from PostgreSQL (own thread) |
| `core_memory_append` | Main only | Writes pending op to PostgreSQL, consolidated later |
| `core_memory_replace` | Main only | Writes pending op to PostgreSQL, consolidated later |
| `save_to_memory` | Yes | Writes to PostgreSQL (spawn's thread) |
| `recall_transcript` | Yes | Read-only, searches encrypted JSONL in .anima/transcripts/ |
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

### consolidation.py
- Reads messages from PostgreSQL
- Writes extracted memories to SQLCipher
- One-directional flow, no consistency issues

### embeddings.py
- Migrate from in-memory vector store to pgvector
- Embeddings persisted in PostgreSQL, survive restarts
- Remove `embedding_json` column from SQLCipher `memory_items` (or keep as cache)

---

## Configuration

```python
# New settings
agent_max_concurrent_spawns: int = 10           # LLM semaphore size
agent_spawn_timeout: float = 300.0              # per-spawn timeout (seconds)
agent_spawn_max_steps: int = 4                  # max steps per spawn
agent_spawn_recursive: bool = False             # allow spawns to spawn (future)
runtime_database_url: str = "postgresql+asyncpg://localhost/anima_runtime"
```

## Migration Path

1. Add PostgreSQL dependency and async engine
2. Create runtime tables in PostgreSQL (messages, threads, runs, spawns)
3. Move message read/write to PostgreSQL
4. Keep SQLCipher for soul data (no changes to encryption layer)
5. Add SpawnManager + spawn tools
6. Change turn_coordinator to per-thread locking
7. Fix ToolExecutor shared state (per-invocation or pass delegation as args)
8. Add LLM semaphore
9. Migrate embeddings to pgvector

## What Stays the Same

- SQLCipher encryption for soul data
- `.anima/` directory structure (manifest.json + anima.db)
- Recovery phrase system (BIP39)
- All consciousness features (self-model, emotions, inner monologue)
- Tool surface (6 core + 9 extension, plus 3 new spawn tools)
- LLM provider abstraction (OpenAI-compatible client)
- System prompt architecture
- Consolidation logic (reads from different source, same extraction)

## Pending Memory Ops (Cross-Conversation Continuity)

Within a conversation, the agent sees its own `core_memory_append`/`core_memory_replace` calls in the context window (Tier 2 — active messages in PostgreSQL). No special handling needed.

Across conversations — if the user starts a new chat before consolidation runs — pending ops are loaded into the system prompt alongside soul data:

```python
# At turn start, load both soul + pending ops
soul_blocks = load_from_sqlcipher(user_id)           # permanent knowledge
pending_ops = load_from_postgres(user_id, consolidated=False)  # not yet promoted
# Both rendered into system prompt memory blocks
```

Consolidation promotes pending ops to the soul store and marks them `consolidated=true`. Simple, no special architecture needed.

---

## Consolidation Gateway Robustness

In this architecture, consolidation is load-bearing — it's the only way data enters the soul. Requirements:

- **Health monitoring**: track last successful run, alert if consolidation falls behind
- **Retry logic**: failed consolidation retries with exponential backoff
- **Idempotent writes**: consolidation can be re-run safely (upsert semantics)
- **Ordered processing**: pending ops applied in creation order
- **Transcript export**: runs after knowledge extraction, exports encrypted JSONL to `.anima/transcripts/`

Consolidation does NOT need to be real-time. A delay of seconds to minutes is acceptable — the agent has the context window for immediate continuity, and pending ops for cross-conversation continuity. The soul is the long-term store, not the hot path.

---

## Known Limitations

1. **Transcript search is linear**: `recall_transcript` decrypts and scans JSONL files sequentially. For years of conversations this could be slow. Mitigated by episodic memory pointing to specific transcript files (only relevant files are opened). Acceptable for a rare, on-demand operation.

2. **PostgreSQL dependency**: requires a running PostgreSQL instance. For development: standard install. For distribution: Docker Compose or embedded option needed. Not a design limitation, but an operational one.

---

## Open Questions

1. **Spawn recursion** — Should spawns be able to spawn? Disabled initially to prevent runaway resource usage. Could enable later with a depth limit.
2. **Spawn memory access** — Read-only snapshot vs live read? Snapshot is simpler and avoids races. Live read is more current but needs care.
3. **Result merging** — How does the main agent incorporate spawn results? Current design: spawn results appear as a memory block in the next turn's context. Alternative: inject as a system message.
4. **Spawn UI** — How does the frontend show spawn progress? WebSocket events for spawn lifecycle (created, running, completed, failed).
5. **PostgreSQL deployment** — Bundle with the app? Docker compose? Require user install? Depends on deployment target.
