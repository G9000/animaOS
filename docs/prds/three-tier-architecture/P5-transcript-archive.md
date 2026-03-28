---
title: "Phase 5: Transcript Archive"
description: Encrypted JSONL transcript export, sidecar indexes, recall_transcript tool, eager consolidation on thread close, and PostgreSQL message pruning
category: prd
version: "1.0"
---

# Phase 5: Transcript Archive

**Depends on**: P4 (Write Boundary)
**Date**: 2026-03-26
**Status**: Approved
**PR scope**: 1 PR

---

## Overview

The Archive tier stores full conversation transcripts as encrypted JSONL files in `.anima/transcripts/`. This is the third tier of the three-tier cognitive architecture, sitting alongside the Soul (SQLCipher) and Runtime (PostgreSQL).

Transcripts serve two purposes:

1. **Verbatim recall** -- The agent can search past conversations for exact wording or specific details that were not promoted to semantic memory during consolidation. Not everything worth remembering passes through the extraction pipeline; transcripts are the safety net.
2. **Portability** -- Transcript files live inside `.anima/` and travel with the identity. Unlike PostgreSQL runtime data (which is ephemeral and machine-local), transcripts are durable and portable.

The consolidation gateway (background, async) is the sole writer to the archive. Runtime code never writes transcript files directly. This preserves the write boundary established in P4.

### Why not just keep messages in PostgreSQL?

PostgreSQL messages are runtime state. They serve the active conversation window and are pruned after a configurable TTL. Keeping all messages forever in PostgreSQL would:

- Grow unboundedly, degrading query performance
- Couple verbatim recall to a machine-local, non-portable store
- Violate the tier separation (runtime is ephemeral by design)

Encrypted JSONL files are append-only, portable, and cheap to retain indefinitely.

---

## Scope

This phase implements:

1. **Transcript export** -- Serialize thread messages to encrypted JSONL on thread close
2. **Sidecar index** -- Unencrypted metadata file for fast filtering without decryption
3. **Eager consolidation** -- Thread close endpoint + inactivity fallback sweep
4. **`recall_transcript` tool** -- Agent tool for searching archived transcripts
5. **PostgreSQL message pruning** -- Background sweep to delete messages older than `message_ttl_days`
6. **Configuration** -- Two new settings: `transcript_retention_days`, `message_ttl_days`

This phase does NOT implement:

- Sidecar encryption (deferred -- see Open Questions in parent PRD)
- LLM-powered keyword extraction for sidecar (uses simple TF-IDF initially)
- UI for browsing transcripts (frontend work is out of scope)
- Changes to the consolidation extraction pipeline (knowledge extraction remains as-is from P4)

---

## Implementation Details

### Architecture Position

```
Runtime (PostgreSQL)
    |
    v  (thread close / inactivity timeout)
Consolidation Gateway
    |
    ├── Knowledge extraction --> Soul (SQLCipher)  [existing, from P4]
    ├── Transcript export --> Archive (.anima/transcripts/)  [this phase]
    └── Mark messages prunable --> PostgreSQL TTL sweep  [this phase]
```

The consolidation gateway already exists (P4 establishes it for pending memory ops). This phase extends it with two new responsibilities: transcript export and message prune-marking.

### Thread Close Semantics

A thread is "closed" when the user starts a new conversation or navigates away. This is the primary trigger for eager consolidation. The thread is not deleted -- it transitions to `status = "closed"` and a new thread is created for subsequent conversation.

Current behavior: `reset_agent_thread()` in `service.py` calls `reset_thread()` which deletes all messages and resets the thread. This phase changes that: instead of deleting, we close the thread (preserving messages for export) and create a new one.

---

## Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `services/agent/transcript_archive.py` | Export thread to encrypted JSONL, generate sidecar, manage transcript directory |
| `services/agent/transcript_search.py` | Sidecar-based search, decrypt matching files, extract snippets |
| `api/routes/threads.py` | `POST /api/threads/{thread_id}/close` endpoint |
| `services/agent/eager_consolidation.py` | Thread close handler, inactivity sweep, prune sweep |

### Modified Files

| File | Change |
|------|--------|
| `config.py` | Add `transcript_retention_days`, `message_ttl_days` settings |
| `services/agent/tools.py` | Register `recall_transcript` tool in `get_extension_tools()` |
| `services/agent/service.py` | Replace `reset_agent_thread()` with close-and-new-thread logic |
| `services/agent/persistence.py` | Add `close_thread()`, `create_new_thread()` helpers |
| `models/agent_runtime.py` | Add `closed_at` column to `AgentThread`, add `is_archived` boolean |
| `main.py` | Register inactivity sweep and prune sweep as periodic background tasks |
| `api/routes/chat.py` | Wire thread close on "new chat" action |

---

## Archive Format

### Encrypted JSONL (`.jsonl.enc`)

Each line in the plaintext JSONL is one message, serialized as a JSON object:

```jsonl
{"role":"user","content":"Hey, can we talk about the project deadline?","ts":"2026-03-26T10:00:00Z","seq":1}
{"role":"assistant","content":"Of course...","thinking":"User seems stressed about timeline...","ts":"2026-03-26T10:00:05Z","seq":2,"tool_calls":[]}
{"role":"tool","name":"recall_memory","content":"Project deadline: April 15","ts":"2026-03-26T10:00:06Z","seq":3,"tool_call_id":"tc-001"}
{"role":"assistant","content":"Based on what I remember...","ts":"2026-03-26T10:00:08Z","seq":4}
```

**Field definitions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | Yes | `"user"`, `"assistant"`, `"tool"`, or `"summary"` |
| `content` | string | Yes | Message text content |
| `ts` | string (ISO-8601) | Yes | Timestamp in UTC |
| `seq` | int | Yes | Original `sequence_id` from `AgentMessage` for ordering |
| `thinking` | string | No | Agent's inner reasoning (from `thinking` kwarg) |
| `tool_calls` | array | No | Tool calls made by assistant (same structure as `content_json.tool_calls`) |
| `tool_name` | string | No | For `role:"tool"`, the tool that produced this result |
| `tool_call_id` | string | No | For `role:"tool"`, the call ID this result corresponds to |
| `source` | string | No | Message source (e.g., `"api"`, `"proactive"`) |

**Excluded from archive:**

- Messages with `role = "approval"` (transient approval checkpoints)
- Messages where `is_in_context = False` AND `content_text` is empty (compacted-away stubs)
- System messages (the system prompt is not conversation content)

### Encryption

The plaintext JSONL is encrypted as a single blob using the existing DEK infrastructure:

1. Serialize all messages as JSONL (one JSON object per line, `\n`-separated)
2. Encode as UTF-8 bytes
3. Encrypt using AES-256-GCM with the user's active DEK from the `conversations` domain
4. AAD (Additional Authenticated Data): `transcript:{thread_id}:{date}` encoded as UTF-8
5. Output format: `IV (12 bytes) || ciphertext || auth_tag (16 bytes)`
6. Write to `.anima/transcripts/{date}_thread-{thread_id}.jsonl.enc`

**Rationale for whole-file encryption (not per-line):** Per-line encryption would allow line-level random access but leaks message count and individual message sizes. Whole-file encryption is simpler and reveals only total file size. The sidecar's `chunk_offsets` field is reserved for future chunk-level encryption if files grow very large.

**Atomic writes:**

```python
tmp_path = target_path.with_suffix(".tmp")
tmp_path.write_bytes(encrypted_data)
tmp_path.rename(target_path)  # atomic on POSIX; best-effort on Windows
```

On Windows, `os.replace()` is used instead of `Path.rename()` for atomicity guarantees when the target already exists.

---

## Sidecar Index

Each transcript gets a companion `.meta.json` file. Sidecars are unencrypted to enable fast filtering without DEK access. (Encryption of sidecars is deferred -- see parent PRD Open Questions.)

### Schema

```json
{
  "version": 1,
  "thread_id": 14,
  "user_id": 1,
  "date_start": "2026-03-26T09:55:00Z",
  "date_end": "2026-03-26T10:45:00Z",
  "message_count": 42,
  "roles": ["user", "assistant", "tool"],
  "keywords": ["project deadline", "scope changes", "April timeline"],
  "summary": "Discussed project deadline concerns and agreed to reduce scope.",
  "chunk_offsets": [0],
  "episodic_memory_ids": ["ep-2026-03-26-001"],
  "archived_at": "2026-03-26T10:46:00Z",
  "encryption": {
    "domain": "conversations",
    "aad_prefix": "transcript"
  }
}
```

**Field definitions:**

| Field | Type | Description |
|-------|------|-------------|
| `version` | int | Sidecar schema version (start at 1) |
| `thread_id` | int | Source thread ID in PostgreSQL |
| `user_id` | int | Owning user ID |
| `date_start` | string | ISO-8601 timestamp of first message |
| `date_end` | string | ISO-8601 timestamp of last message |
| `message_count` | int | Total messages archived |
| `roles` | array of string | Distinct roles present |
| `keywords` | array of string | Top-N keywords extracted via TF-IDF (max 10) |
| `summary` | string | One-sentence summary (from episodic memory if available, else first/last message heuristic) |
| `chunk_offsets` | array of int | Byte offsets for chunk-level seeking (initially `[0]` for single-chunk) |
| `episodic_memory_ids` | array of string | IDs of episodic memories generated from this thread |
| `archived_at` | string | ISO-8601 timestamp when archival completed |
| `encryption` | object | Encryption metadata (domain, AAD prefix) |

### Keyword Extraction

Initial implementation uses simple TF-IDF over user messages in the thread:

1. Tokenize all `role:"user"` messages into words
2. Remove stop words and words shorter than 3 characters
3. Compute term frequency within this thread
4. Rank by frequency, take top 10
5. Filter out words that appear in more than 80% of all threads (if enough history exists)

This is intentionally simple. LLM-powered keyword extraction is deferred to avoid blocking archival on LLM availability.

---

## Transcript Lifecycle

```
1. Conversation happens
   Messages written to PostgreSQL (agent_messages table)
       |
2. Thread closes (explicit or inactivity timeout)
   POST /api/threads/{thread_id}/close
       |
3. Eager consolidation fires (async background task)
   ├── Knowledge extraction: pending_memory_ops --> Soul  [P4, existing]
   ├── Episodic memory: maybe_generate_episode()  [existing]
   ├── Transcript export: messages --> encrypted JSONL + sidecar  [NEW]
   └── Mark thread as archived (is_archived = True)
       |
4. Episodic memory in Soul references transcript file
   MemoryEpisode.transcript_ref = "2026-03-26_thread-14.jsonl.enc"
       |
5. PostgreSQL messages pruned after message_ttl_days (default 30)
   Background sweep: DELETE WHERE created_at < now() - ttl AND is_archived = True
       |
6. Transcript files retained per transcript_retention_days (default -1 = forever)
   Background sweep: DELETE files WHERE archived_at < now() - retention
```

### Step 2: Thread Close

The thread close is triggered by:

- **Frontend: new chat** -- User clicks "New Chat" button. Frontend calls `POST /api/threads/{thread_id}/close`.
- **Frontend: navigate away** -- `beforeunload` event fires. Frontend sends close via `navigator.sendBeacon()` (fire-and-forget, survives page unload).
- **Frontend: window close** -- Same as navigate away.
- **Inactivity fallback** -- Server-side periodic sweep (every 60 seconds) checks for threads with `last_message_at` older than 5 minutes and `status = "active"`. Closes them automatically.

### Step 3: Eager Consolidation

```python
async def on_thread_close(thread_id: int, user_id: int, db_factory) -> None:
    """Runs as a background task after thread close."""
    # 1. Run existing consolidation pipeline (P4 pending ops)
    await consolidate_pending_ops(user_id=user_id, db_factory=db_factory)

    # 2. Generate episodic memory if enough turns
    await maybe_generate_episode(
        user_id=user_id,
        thread_id=thread_id,
        db_factory=db_factory,
    )

    # 3. Export transcript to archive
    episode_ids = get_episode_ids_for_thread(thread_id, db_factory=db_factory)
    await export_thread_transcript(
        thread_id=thread_id,
        user_id=user_id,
        episode_ids=episode_ids,
        db_factory=db_factory,
    )

    # 4. Mark thread as archived
    mark_thread_archived(thread_id, db_factory=db_factory)
```

### Step 5: Message Pruning

A background sweep runs on a configurable interval (default: every 6 hours):

```python
async def prune_expired_messages(db_factory) -> int:
    """Delete archived messages older than message_ttl_days. Returns count deleted."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.message_ttl_days)
    with db_factory() as db:
        result = db.execute(
            delete(AgentMessage).where(
                AgentMessage.created_at < cutoff,
                AgentMessage.thread_id.in_(
                    select(AgentThread.id).where(AgentThread.is_archived == True)
                ),
            )
        )
        db.commit()
        return result.rowcount
```

Only messages from archived threads are pruned. Active threads are never touched.

### Step 6: Transcript Retention

A background sweep runs daily:

- If `transcript_retention_days == -1`: no-op (keep forever)
- Otherwise: delete `.jsonl.enc` and `.meta.json` files where `archived_at` in the sidecar is older than the retention window

---

## Eager Consolidation

### `POST /api/threads/{thread_id}/close`

```python
@router.post("/api/threads/{thread_id}/close")
async def close_thread(
    thread_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Close a thread and trigger eager consolidation."""
    thread = db.get(AgentThread, thread_id)
    if thread is None or thread.user_id != current_user.id:
        raise HTTPException(404, "Thread not found")
    if thread.status == "closed":
        return {"status": "already_closed"}

    thread.status = "closed"
    thread.closed_at = datetime.now(UTC)
    db.commit()

    # Fire background consolidation (non-blocking)
    safe_create_task(
        on_thread_close(
            thread_id=thread_id,
            user_id=current_user.id,
            db_factory=build_db_factory(db),
        )
    )

    return {"status": "closed", "thread_id": thread_id}
```

**Design decisions:**

- The endpoint returns immediately after marking the thread closed. Consolidation runs asynchronously.
- The response does NOT wait for the transcript to be written. The frontend does not need to know when archival completes.
- `sendBeacon` compatibility: the endpoint accepts POST with no body (thread_id is in the URL path). No JSON parsing required.

### Inactivity Sweep

Registered as a periodic task in `main.py` lifespan:

```python
async def inactivity_sweep():
    """Close threads that have been idle for more than 5 minutes."""
    cutoff = datetime.now(UTC) - timedelta(minutes=5)
    with SessionLocal() as db:
        stale_threads = db.scalars(
            select(AgentThread).where(
                AgentThread.status == "active",
                AgentThread.last_message_at < cutoff,
                AgentThread.last_message_at.is_not(None),
            )
        ).all()
        for thread in stale_threads:
            thread.status = "closed"
            thread.closed_at = datetime.now(UTC)
        db.commit()

        for thread in stale_threads:
            safe_create_task(
                on_thread_close(
                    thread_id=thread.id,
                    user_id=thread.user_id,
                    db_factory=sessionmaker(bind=db.get_bind()),
                )
            )
```

Sweep interval: 60 seconds. Inactivity threshold: 5 minutes (hardcoded initially, configurable later if needed).

---

## `recall_transcript` Tool

### Tool Definition

```python
@tool
def recall_transcript(query: str, days_back: int = 30) -> str:
    """Search past conversation transcripts for specific details.
    Use this when you need exact wording or verbatim recall from
    past conversations, not just general memory of what happened.
    Returns relevant snippets, not full conversations."""
```

### Search Algorithm

```
1. List sidecar files in .anima/transcripts/
2. Filter by date range (now - days_back)
3. Score sidecars by keyword overlap with query
4. Rank and take top 5 candidate transcripts
5. Decrypt candidate JSONL files
6. Scan messages for query match (text overlap scoring)
7. Extract surrounding context (2 messages before/after each hit)
8. Format and return top 10 snippets (truncated to fit budget)
```

### Implementation Detail

```python
async def search_transcripts(
    *,
    user_id: int,
    query: str,
    days_back: int = 30,
    max_transcripts: int = 5,
    max_snippets: int = 10,
    snippet_context: int = 2,
    budget_chars: int = 3000,
) -> list[TranscriptSnippet]:
```

**Sidecar filtering (step 2-4):**

- Parse `date_start` from each `.meta.json`
- Reject files outside the `days_back` window
- Score remaining sidecars: `keyword_overlap(query, sidecar.keywords) + date_recency_bonus`
- Take top `max_transcripts` by score

**Message scanning (step 5-7):**

- Decrypt the `.jsonl.enc` file using the user's `conversations` domain DEK
- Parse each line as JSON
- Score each message using `_text_overlap_score()` (reuse from `conversation_search.py`)
- For each hit, include `snippet_context` messages before and after
- Deduplicate overlapping context windows

**Output format:**

```
[2026-03-26, thread 14]
User: Hey, can we talk about the project deadline?
Assistant: Of course. Based on what I remember, the deadline is April 15...
User: Right, but the scope has changed since then.

[2026-03-25, thread 13]
User: The client wants to add three new features.
Assistant: That could push the deadline. Let me note this...
```

**Budget enforcement:** Snippets are concatenated until `budget_chars` is reached. Remaining snippets are dropped with a note: `"(N more matches found, use a more specific query to narrow results)"`.

### Tool Registration

`recall_transcript` is added to `get_extension_tools()` in `tools.py`, alongside the existing extension tools. It requires `ToolContext` to access `user_id` for DEK resolution.

### System Prompt Integration

The system prompt's memory tier description is updated to include transcript recall:

```
You have different levels of memory:
- Your core memories and feelings are always with you (you just know them)
- For recent conversations, use recall_conversation to search what was discussed
- For exact wording from past conversations, use recall_transcript
  Think of this like finding a specific page in a diary -- you check the
  dates and topics first, then read the exact passage you need
```

---

## Test Plan

### Unit Tests

| Test | Module | What it verifies |
|------|--------|------------------|
| `test_export_thread_to_jsonl` | `transcript_archive.py` | Serializes messages to correct JSONL format |
| `test_export_excludes_system_and_approval` | `transcript_archive.py` | System messages, approval checkpoints excluded |
| `test_export_includes_thinking` | `transcript_archive.py` | Inner thoughts from `thinking` kwarg preserved |
| `test_encrypt_transcript` | `transcript_archive.py` | JSONL encrypted with correct DEK and AAD |
| `test_decrypt_transcript` | `transcript_archive.py` | Encrypted file decrypts back to original JSONL |
| `test_atomic_write_creates_file` | `transcript_archive.py` | File written via tmp+rename pattern |
| `test_atomic_write_no_partial_on_error` | `transcript_archive.py` | On write failure, no `.enc` file left behind |
| `test_sidecar_schema` | `transcript_archive.py` | Sidecar JSON matches expected schema |
| `test_sidecar_keyword_extraction` | `transcript_archive.py` | TF-IDF extracts reasonable keywords |
| `test_sidecar_date_range` | `transcript_archive.py` | `date_start` and `date_end` match first/last message |
| `test_search_by_date_range` | `transcript_search.py` | Only transcripts within `days_back` are searched |
| `test_search_by_keyword_overlap` | `transcript_search.py` | Sidecar keyword matching filters candidates |
| `test_search_returns_snippets` | `transcript_search.py` | Returns context-windowed snippets, not full transcript |
| `test_search_respects_budget` | `transcript_search.py` | Output truncated to `budget_chars` |
| `test_search_empty_query` | `transcript_search.py` | Returns recent messages as browse mode |
| `test_search_no_transcripts` | `transcript_search.py` | Returns empty list gracefully |
| `test_close_thread_endpoint` | `routes/threads.py` | Thread status transitions to "closed" |
| `test_close_thread_idempotent` | `routes/threads.py` | Closing already-closed thread returns 200, no error |
| `test_close_thread_wrong_user` | `routes/threads.py` | Returns 404 for threads owned by other users |
| `test_inactivity_sweep` | `eager_consolidation.py` | Threads idle > 5 min are closed |
| `test_inactivity_sweep_skips_active` | `eager_consolidation.py` | Recently active threads are not closed |
| `test_prune_only_archived` | `eager_consolidation.py` | Only messages from archived threads are deleted |
| `test_prune_respects_ttl` | `eager_consolidation.py` | Messages younger than TTL are preserved |
| `test_transcript_retention_forever` | `eager_consolidation.py` | With `-1`, no files are deleted |
| `test_transcript_retention_deletes_old` | `eager_consolidation.py` | Files older than retention window are deleted |

### Integration Tests

| Test | What it verifies |
|------|------------------|
| `test_full_lifecycle` | Chat -> close thread -> consolidation -> transcript exists -> prune messages -> recall_transcript finds it |
| `test_recall_transcript_tool` | Agent can invoke `recall_transcript` and get formatted snippets |
| `test_portability` | Copy `.anima/` (minus `runtime/`), start fresh server, `recall_transcript` still works |
| `test_no_dek_graceful` | Without active DEK, export and search degrade gracefully (skip or error message, no crash) |

### Test Conventions

- Tests use `scaffold` provider (no real LLM calls)
- Transcript files written to a `tmp_path` fixture directory, not the real `.anima/`
- Encryption tests use a test DEK, not user session DEKs
- Sidecar tests validate JSON schema structure, not content semantics

---

## Acceptance Criteria

1. **Thread close creates transcript**: When `POST /api/threads/{thread_id}/close` is called, an encrypted `.jsonl.enc` file and `.meta.json` sidecar appear in `.anima/transcripts/` within 60 seconds.

2. **Transcript contains all conversation messages**: Every user, assistant, and tool message from the thread is present in the JSONL (minus system/approval). Message order matches `sequence_id`.

3. **Encryption uses existing key infrastructure**: Transcripts are encrypted with the user's `conversations` domain DEK via AES-256-GCM. No new key derivation or storage.

4. **Sidecar enables date and keyword filtering**: `recall_transcript` with `days_back` correctly filters by sidecar `date_start`. Keyword scoring narrows candidates before decryption.

5. **`recall_transcript` returns relevant snippets**: Given a query matching a past conversation, the tool returns formatted snippets with surrounding context. Output respects `budget_chars`.

6. **Messages pruned after TTL**: Archived thread messages older than `message_ttl_days` are deleted from PostgreSQL. Active thread messages are never pruned.

7. **Inactivity fallback works**: Threads with no messages for 5+ minutes are automatically closed and consolidated.

8. **Atomic writes prevent corruption**: If the server crashes mid-export, no partial `.enc` file is left behind. Only fully-written files exist.

9. **No regression**: All existing tests (846+) continue to pass. Thread reset (`reset_agent_thread`) now closes and archives the thread before creating a fresh one — this is an intentional improvement over destructive deletion, ensuring no conversation data is lost. The "New Chat" / "Reset" actions trigger the same close-and-archive pipeline as explicit thread close.

10. **Graceful degradation without DEK**: If no encryption key is active (pre-login or passphrase not set), transcript export and search skip encryption and work with plaintext JSONL. The system does not crash.

---

## Out of Scope

- **Sidecar encryption** -- Sidecars are unencrypted in this phase. They contain keywords and timestamps but not message content. Decision on encrypting sidecars is deferred to avoid coupling this phase to an unresolved architectural question (see parent PRD, Open Questions #3).

- **LLM-powered keyword extraction** -- Sidecar keywords use TF-IDF, not LLM extraction. This avoids blocking archival on LLM availability and keeps the export pipeline fast and deterministic.

- **Transcript UI** -- No frontend for browsing or playing back transcripts. The archive is accessed exclusively through the `recall_transcript` tool (agent-facing) in this phase.

- **Chunk-level encryption** -- The `chunk_offsets` sidecar field is reserved but only contains `[0]` (single chunk). Chunk-level encryption for large transcripts is a future optimization.

- **Cross-user transcript search** -- Transcripts are scoped to a single user. Multi-user search is not a use case for AnimaOS.

- **Transcript editing or deletion by the agent** -- The agent can read transcripts but cannot modify or delete them. Transcript lifecycle is managed by retention policy only.

- **Streaming export for very long threads** -- Initial implementation loads all thread messages into memory before serializing. For threads exceeding 10,000 messages, a streaming serializer would be needed. This is deferred because typical threads are much smaller (compaction keeps active context under a few hundred messages, and threads are closed regularly).

- **Migration of existing messages** -- Messages from threads that existed before P5 are not retroactively archived. Archival only applies to threads closed after this phase ships. A one-time migration script could be written separately if needed.
