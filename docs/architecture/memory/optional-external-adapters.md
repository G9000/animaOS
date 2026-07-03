# Optional External Memory Adapter Seams

Anima keeps SQLCipher soul storage as the canonical memory authority. Retrieval
indexes are derived caches. They can improve search speed or scale, but they
must never become the only place a memory exists.

## Native Default

The default retrieval backend is `NativeMemoryRetrievalBackend` in
`apps/server/src/anima_server/services/agent/retrieval_backends.py`. It wraps the
local `anima_core_retrieval` memory index and remains the reference
implementation for contract tests.

Callers use the backend through the `MemoryRetrievalBackend` protocol:

- `upsert_memory_document`
- `delete_memory_document`
- `delete_user_memory_documents`
- `search_memory`
- `search_memory_by_vector`
- `memory_documents_exist`
- `memory_index_is_dirty`
- `mark_memory_index_dirty`
- `clear_memory_index_dirty`

The live keyword and semantic retrieval paths still fall back to the existing
Python/pgvector paths when the native backend is unavailable or empty.

## Rebuild Contract

`load_canonical_memory_retrieval_documents(db, user_id=...)` loads active
`MemoryItem` rows from SQLCipher and converts them into
`MemoryRetrievalDocument` values. `rebuild_memory_retrieval_index(...)` then:

1. Deletes the user's derived memory documents from the selected backend.
2. Reloads active canonical memory rows from SQLCipher.
3. Upserts each canonical document into the backend.
4. Clears the dirty marker only when every upsert succeeds.

Superseded memories are excluded from the derived active index, but remain in
SQLCipher for audit and history.

This means an external index can be dropped, corrupted, or migrated without
data loss. The recovery path is always a rebuild from canonical SQLCipher rows.

## External Vector Adapters

Future Weaviate, Qdrant, LanceDB, or similar adapters should implement the same
`MemoryRetrievalBackend` contract. They may store embeddings, lexical payloads,
metadata, or hybrid index structures, but must follow these rules:

- No external adapter is required for normal local use.
- Adapter configuration must be opt-in.
- The adapter cannot write canonical memories.
- Rebuild must work from `MemoryRetrievalDocument` rows only.
- Search failures must degrade to local retrieval instead of blocking chat.
- User deletion or memory forgetting must delete matching derived documents.
- Dirty-state tracking must make failed incremental writes recoverable by a
  later rebuild.

The backend contract is intentionally about active memory retrieval only. It is
not a general persistence API.

## Deferred Graph Adapter

Graph adapters remain deferred. The native temporal knowledge graph now owns
relation lifecycle semantics, alias-aware traversal, valid-time/current-belief
resolution, evidence linkage, and vault portability. An external graph backend
should not be added until those native semantics are stable enough to express as
a small, contract-tested protocol without weakening SQLCipher as canonical
storage.
