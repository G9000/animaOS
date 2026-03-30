# Multi-Thread Chat Design

**Date:** 2026-03-31
**Status:** Approved

## Overview

Add ChatGPT-style thread history to AnimaOS — multiple conversation threads per user, each with its own message history, all sharing the same soul DB (memory, self-model, emotional arc). Threads are chapters in a single relationship, not separate identities.

---

## Architecture Principles

- **One identity, many conversations.** All threads share the same soul DB, memory consolidation pipeline, and self-model. The AI is always the same entity.
- **PG is the live runtime.** Active thread messages live in `runtime_messages`. Archived thread messages live in JSONL (encrypted on disk).
- **UI always shows full history.** Rehydrated messages are always visible to the user. Only the agent context is summarized.
- **Agent context stays lean.** When continuing an archived thread, the agent gets a summary of the old conversation, not all raw messages. New messages accumulate normally.

---

## Data Model Changes

### `RuntimeThread`

Remove the partial unique index `uq_runtime_threads_active_user` that enforces one active thread per user. Users now have many threads.

Add columns:
- `title: str | None` — already exists, used for display in sidebar
- No other schema changes needed on `RuntimeThread`

Migration: drop `uq_runtime_threads_active_user` index.

### `RuntimeMessage`

Add column:
```
is_archived_history: bool  default=False
```

Marks messages rehydrated from a JSONL archive when a user continues an old thread. These messages are shown in the UI but excluded from agent context.

Index: `ix_runtime_messages_thread_context` already exists on `(thread_id, is_in_context)` — add a covering index on `(thread_id, is_archived_history)` for efficient filtering.

---

## Thread Lifecycle

```
NEW          ACTIVE           CLOSED              PRUNED (TTL)
 │             │                │                     │
 │  create     │  /close        │  JSONL written       │
 │────────────>│────────────────>│  is_archived=true    │
                                │  PG messages remain   │
                                │  (until TTL expires)  │
                                │─────────────────────>│
                                │  PG messages deleted  │
                                │  JSONL is only copy   │
```

**Closed threads** have JSONL written but PG messages may still be present until `message_ttl_days` expires. After TTL, only JSONL remains.

Reactivation (user sends message to a closed/archived thread):

1. Check if thread has PG messages (within TTL window) — if yes, skip JSONL read
2. If PG messages are gone: read JSONL → `decrypt_transcript()` (needs DEK from vault/unlock session)
3. Use summary from meta sidecar (generated at close time); no extra LLM call needed
4. If loading from JSONL: bulk-insert old messages with `is_archived_history=True`
5. Insert one `role="system"` message: `"[Previous conversation summary]: {summary}"` with `is_archived_history=False`
6. Set `thread.status = "active"`, `thread.is_archived = False`, clear `closed_at`
7. Agent runs. It sees: `[summary message] + [new user message]`
8. New messages written normally with `is_archived_history=False`

---

## API Changes

### Existing endpoint — modified

**`POST /api/chat`**

Add optional body field:
```json
{ "thread_id": 42 }
```

- If omitted: create a new thread (current behavior)
- If provided: use that thread. If archived, trigger reactivation flow before running agent.

### New endpoints

**`GET /api/threads`**

List all threads for the authenticated user, sorted by `last_message_at` DESC.

Response:
```json
{
  "threads": [
    {
      "id": 42,
      "title": "Planning the trip",
      "status": "active",
      "is_archived": false,
      "last_message_at": "2026-03-31T10:00:00Z",
      "created_at": "2026-03-30T09:00:00Z"
    }
  ]
}
```

**`POST /api/threads`**

Create a new thread explicitly (without sending a message).

Response: `{ "thread_id": 43, "status": "active" }`

**`GET /api/threads/{id}/messages`**

Return all messages for a thread in chronological order.

- Active thread: query `runtime_messages` (all rows, including `is_archived_history=True`)
- Archived thread: `decrypt_transcript()` from JSONL, return as message list
- Reactivated thread (active but has `is_archived_history` rows): already in PG, return all

Response:
```json
{
  "thread_id": 42,
  "messages": [
    { "role": "user", "content": "...", "ts": "...", "is_archived_history": false }
  ]
}
```

---

## Agent Context Building

In `persistence.py` / wherever messages are loaded for agent context:

```python
# Current: loads all in-context messages
messages = db.query(RuntimeMessage).filter(
    RuntimeMessage.thread_id == thread_id,
    RuntimeMessage.is_in_context == True,
)

# New: also exclude archived history rows
messages = db.query(RuntimeMessage).filter(
    RuntimeMessage.thread_id == thread_id,
    RuntimeMessage.is_in_context == True,
    RuntimeMessage.is_archived_history == False,
)
```

The summary system message (inserted at reactivation) has `is_archived_history=False`, so it IS included. Agent sees: `[summary] + [new messages]`.

---

## Thread Title Generation

Threads need display titles for the sidebar.

- On first user message: extract up to 60 characters of the message as the title (no LLM call)
- Title stored in `RuntimeThread.title` (column already exists)
- Archived threads: fall back to meta sidecar `summary` field if `title` is null
- User can rename threads later (future: `PATCH /api/threads/{id}`)

Title setting: in `_resolve_thread_id()` after the first message is written, update `thread.title` if currently null.

---

## Frontend Changes

### State

```typescript
const [threads, setThreads] = useState<Thread[]>([])
const [currentThreadId, setCurrentThreadId] = useState<number | null>(null)
```

### Thread sidebar

- Left sidebar (collapsible): list of threads, sorted by `last_message_at`
- Each item: title, relative timestamp ("2 hours ago"), active indicator
- "New chat" button at top → `POST /api/threads` → switch to new thread
- Click thread → load messages via `GET /api/threads/{id}/messages`, set `currentThreadId`

### Message loading

On thread switch:
1. `GET /api/threads/{id}/messages` → set messages in state
2. Clear streaming state
3. New messages sent with `thread_id` in body

### Chat send

```typescript
await api.chat.send({ message, threadId: currentThreadId })
```

---

## What Does NOT Change

- Soul DB (SQLCipher): unchanged. All threads write to the same memory tables.
- Consolidation pipeline: unchanged. `on_thread_close` runs per thread, writes to shared soul DB.
- Encryption: unchanged. JSONL archives encrypted with same DEK.
- Agent identity: unchanged. Same persona, soul, self-model across all threads.
- Recovery system: unchanged.

---

## Migration Plan

1. Alembic migration:
   - Drop `uq_runtime_threads_active_user` partial unique index
   - Add `runtime_messages.is_archived_history` column (default `false`)
   - Add index on `(thread_id, is_archived_history)`

2. Backend:
   - `_resolve_thread_id()` in `service.py`: accept explicit `thread_id`, handle reactivation
   - Add `GET /api/threads`, `POST /api/threads`, `GET /api/threads/{id}/messages`
   - Agent message loader: add `is_archived_history=False` filter
   - Title generation on first message

3. API client (`packages/api-client`):
   - `threads.list()`, `threads.create()`, `threads.messages(id)`
   - `chat.send()` accepts `threadId`

4. Desktop frontend:
   - Thread sidebar component
   - Thread switching logic
   - Message loading on switch
