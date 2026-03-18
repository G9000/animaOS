---
title: Cross-Cutting Concerns
description: Context window management, background tasks, gotchas, and test coverage
category: architecture
---

# Cross-Cutting Concerns

[Back to Index](README.md)

## Context Window Management

The system actively manages the LLM context window through 5 mechanisms:

1. **Token budget planning** (`prompt_budget.py`): Allocates tokens across system prompt, memory blocks, and conversation history.
2. **Memory pressure warning**: Injected into the system prompt at 80% of context capacity.
3. **Proactive compaction** (`service.py:693`): Compacts before LLM call if estimated tokens exceed threshold.
4. **Reactive compaction** (`compaction.py`): After each turn, marks old messages `is_in_context=False`.
5. **Emergency compaction** (`service.py:750`): On `ContextWindowOverflowError`, aggressively compacts (half the normal `keep_last_messages`) and retries once.
6. **LLM-powered summarization** (`compaction.py`): Attempts rich summary before falling back to text-based compaction.

## Background Tasks

Two async background systems run post-turn:

### Memory Consolidation (`consolidation.py`)

Fires immediately after each turn:
- Regex extraction of facts, preferences, focus
- LLM extraction via `EXTRACTION_PROMPT`
- Claim upsert with conflict resolution
- Emotional signal recording
- Embedding generation and vector store update
- Episode creation
- Daily log recording

### Reflection (`reflection.py`)

Fires after 5 minutes of inactivity:
- Deep inner monologue (LLM-driven self-reflection)
- Self-model section updates (identity, inner_state, etc.)
- Growth log entries

### Sleep Tasks (`sleep_tasks.py`)

Manually triggered via `POST /api/chat/sleep`:
- Contradiction scanning between memory items
- Profile synthesis (merging related facts)
- Episode generation
- Embedding backfill

## Gotchas and Edge Cases

1. **Windows stack overflow guard** (`crypto.py:40`): Argon2id's C library can overflow the default 1MiB Windows thread stack. `_run_with_large_stack()` spawns a dedicated 8MiB thread for KDF operations on Windows.

2. **SQLCipher cipher_memory_security** (`session.py:125`): Disabled on Windows because it causes `STATUS_GUARD_PAGE_VIOLATION`. SQLCipher still zeroes memory on deallocation without it.

3. **DEK memory zeroing** (`sessions.py:171`): On session revoke, `ctypes.memset` attempts to zero DEK bytes. Defense-in-depth -- Python `bytes` are immutable, so the original buffer may already be copied.

4. **Sidecar nonce binding** (`main.py:36`): The nonce is delivered to the Tauri frontend via IPC, not HTTP, preventing interception.

5. **Context overflow retry** (`service.py:743`): On `ContextWindowOverflowError`, the system compacts aggressively and retries once. If the retry also fails, the error propagates.

6. **Encrypted field search** (`routes/memory.py:243`): Since encrypted fields cannot be matched with SQL `ILIKE`, keyword search decrypts all items in Python and filters in-memory. This is O(n) in the number of memory items.

7. **Background task drain on shutdown** (`main.py:128`): The shutdown hook cancels pending reflections and drains background memory consolidation tasks to prevent data loss.

## Test Coverage

- **50 test files** under `apps/server/tests/`
- **602 tests**, all passing
- Tests use the `scaffold` provider (no real LLM calls) for deterministic behavior

## Known Gaps

1. **No formal OpenAPI spec file** -- FastAPI auto-generates it, but there is no checked-in `openapi.json`.
2. **No rate limiting** -- the server relies on sidecar nonce for access control but has no per-endpoint rate limits.
3. **No multi-user concurrent access testing** -- the per-user lock design is documented but not stress-tested.
4. **Alembic migrations not fully traced** -- the current schema is documented but migration history in `alembic/` is not audited here.
5. **Legacy TypeScript backend** (`apps/api/`) -- still present in the repo but being superseded. No migration path documented.
6. **Embedding model configuration** -- the embedding model used by `embeddings.py` is not surfaced in the `Settings` class.
