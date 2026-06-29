# Local Turso Core and Runtime Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or the repository ticket workflow before implementation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate and, if validated, migrate ANIMA's local database substrate from SQLCipher Soul plus embedded PostgreSQL Runtime to encrypted local Turso Soul plus local Turso Runtime, while preserving the three-tier architecture and avoiding Turso Cloud.

**Architecture:** Keep Soul and Runtime as separate physical database files. Turso becomes an engine candidate, not a reason to collapse identity and working cognition. The Archive tier remains encrypted JSONL. Soul migration is copy-verify-flip with rollback; Runtime migration is rebuild-first unless active runtime import is explicitly required.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, SQLCipher, local Turso Database Python bindings, existing Core passphrase/KDF services, existing RuntimeBase/Soul Base models, existing pgvector/PgVecStore baseline, pytest, Bun/Nx scripts.

---

## Scope

This plan covers the local-only migration path. It does not use Turso Cloud, hosted replicas, remote sync, or account-based database storage.

The plan has two independent but related tracks:

1. **Soul track:** prove local Turso can safely replace SQLCipher for durable identity.
2. **Runtime track:** prove local Turso can safely replace embedded PostgreSQL for operational state and retrieval caches.

The Soul track may ship as an optional backend before Runtime moves. PostgreSQL removal only happens after Runtime and vector parity are validated.

## Target Layout

```text
.anima/
  manifest.json
  users/<user_id>/
    soul.db
    runtime.db
  transcripts/
    *.jsonl.enc
```

The active manifest must record:

```json
{
  "database_engine": "sqlcipher|turso",
  "soul_database_path": "users/<id>/soul.db",
  "runtime_database_engine": "postgres|turso",
  "runtime_database_path": "users/<id>/runtime.db"
}
```

Exact field names can change during implementation, but engine choice and rollback metadata must be explicit.

## Key Invariants

1. Soul and Runtime remain separate physical stores.
2. Runtime never writes directly to Soul outside Soul Writer/consolidation.
3. Existing SQLCipher Soul DB is never modified in place during migration.
4. Turso Soul encryption uses an ANIMA-derived raw key domain, not a separate unmanaged passphrase.
5. Turso write paths use conflict retry for MVCC/`BEGIN CONCURRENT` transactions.
6. PostgreSQL is removed only after health checks, startup, tests, and retrieval no longer depend on it.

## Planning Inputs

- PRD: `docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md`
- Soul engine: `apps/server/src/anima_server/db/session.py`
- Runtime engine: `apps/server/src/anima_server/db/runtime.py`
- PG lifecycle: `apps/server/src/anima_server/db/pg_lifecycle.py`
- Core crypto: `apps/server/src/anima_server/services/crypto.py`, `core.py`, `sessions.py`
- User store and unlock: `apps/server/src/anima_server/db/user_store.py`
- Runtime models: `apps/server/src/anima_server/models/runtime.py`, `runtime_memory.py`, `runtime_embedding.py`, `pending_memory_op.py`
- Vector store: `apps/server/src/anima_server/services/agent/pgvec_store.py`, `vector_store.py`, `embeddings.py`
- Runtime migrations: `apps/server/alembic_runtime/versions/`
- Soul migrations: `apps/server/alembic_core/versions/`
- Health checks: `apps/server/src/anima_server/services/health/checks.py`

## Execution Order

### Task 1: Compatibility And Risk Spike

**Ticket:** `LTR-001`

- [ ] Verify local Turso Python driver support, packaging, and SQLAlchemy integration.
- [ ] Verify how to set encryption, `journal_mode = mvcc`, `foreign_keys`, and busy timeout.
- [ ] Verify whether SQLAlchemy can emit `BEGIN CONCURRENT` cleanly.
- [ ] Build a scratch fixture that performs concurrent writes with retry.
- [ ] Build a scratch fixture that encrypts, closes, reopens, and inspects raw bytes for plaintext.
- [ ] Record unsupported SQL or PRAGMA differences against current SQLCipher/PostgreSQL usage.

Exit gate:

- A written compatibility matrix exists with a go/no-go recommendation for Soul spike and Runtime spike.

### Task 2: Database Engine Abstraction

**Ticket:** `LTR-002`

- [ ] Add internal engine selection types for Soul and Runtime.
- [ ] Extend manifest/config parsing for active engine and rollback metadata.
- [ ] Add key derivation helper for Turso Soul and optional Turso Runtime keys.
- [ ] Add a transaction helper for retryable Turso writes.
- [ ] Keep existing SQLCipher and PostgreSQL paths as defaults.

Exit gate:

- Existing tests still pass with default SQLCipher/PostgreSQL behavior.

### Task 3: Encrypted Turso Soul Prototype

**Ticket:** `LTR-003`

- [ ] Create a Turso Soul session factory behind a feature flag or manifest setting.
- [ ] Run the current Soul Alembic schema against a fresh Turso Soul DB or create an equivalent bootstrap path.
- [ ] Add tests for unlock, reopen, field reads, user auth, and basic memory CRUD.
- [ ] Add direct file inspection tests for known plaintext fixtures.

Exit gate:

- A fresh Turso Soul DB can support core auth and memory operations without touching PostgreSQL removal.

### Task 4: Soul Migration Copy-Verify-Flip

**Ticket:** `LTR-004`

- [ ] Implement migration command/service from SQLCipher Soul to Turso Soul.
- [ ] Copy tables in dependency order with IDs preserved.
- [ ] Verify row counts, selected hashes, FK integrity, schema version, and reopen.
- [ ] Write manifest flip only after verification.
- [ ] Keep SQLCipher backup metadata for rollback.
- [ ] Add failure-injection tests that prove rollback keeps SQLCipher active.

Exit gate:

- Existing Core can migrate to Turso Soul and roll back from failed migration attempts.

### Task 5: Turso Runtime Schema And Transactions

**Ticket:** `LTR-005`

- [ ] Convert runtime PostgreSQL-only types to SQLite/Turso-compatible types where needed.
- [ ] Replace PostgreSQL `ARRAY`, `JSON`, `TIMESTAMPTZ`, pgvector, and dialect-specific SQL in the runtime model layer.
- [ ] Build runtime Alembic/bootstrap path for Turso.
- [ ] Add concurrent tests for runtime messages, runs, candidates, pending ops, session notes, and workflow checkpoints.

Exit gate:

- Runtime tables can be created and written under Turso with concurrent write retry behavior.

### Task 6: Runtime Cutover And PostgreSQL Bypass

**Ticket:** `LTR-006`

- [ ] Add `ANIMA_RUNTIME_DATABASE_ENGINE=postgres|turso` or manifest equivalent.
- [ ] Teach startup to skip `pgserver` when Runtime engine is Turso.
- [ ] Provide a rebuild-first Runtime initialization path from Soul plus transcripts.
- [ ] Preserve optional active runtime import only if the compatibility spike shows it is worth the complexity.
- [ ] Update health checks so Turso Runtime reports readiness.

Exit gate:

- Server boots and chat smoke tests pass with Turso Runtime and no embedded PostgreSQL process.

### Task 7: Vector Retrieval Parity

**Ticket:** `LTR-007`

- [ ] Implement `TursoVecStore` or a bounded local fallback for `RuntimeEmbedding`.
- [ ] Store embeddings in a Turso-compatible format.
- [ ] Rebuild embeddings from `MemoryItem.embedding_json`, document chunks, and supported runtime sources.
- [ ] Compare top-k results against pgvector on a golden corpus.
- [ ] Update BM25/indexing code to read from Turso Runtime rows.

Exit gate:

- Memory/document retrieval quality and latency are acceptable without pgvector.

### Task 8: Documentation, Cleanup, And Default Decision

**Ticket:** `LTR-008`

- [ ] Update architecture docs to describe Turso as the local engine.
- [ ] Update setup/config docs and health diagnostics.
- [ ] Decide whether Turso becomes default or remains experimental.
- [ ] Remove PostgreSQL startup requirement only if Task 6 and Task 7 gates pass.
- [ ] Record migration notes and rollback instructions.

Exit gate:

- Maintainers can choose and operate SQLCipher/PostgreSQL or Turso paths with documented tradeoffs.

## Data Migration Notes

### Soul Table Copy

Use explicit table ordering rather than generic reflection-first copying for the first implementation. This makes it easier to preserve foreign key order and handle encrypted fields carefully.

Suggested broad order:

1. users
2. user_keys and auth/profile tables
3. self-model and identity tables
4. memory items, claims, evidence, tags, vectors, KG rows
5. tasks, diary/presence/config tables
6. audit and background maintenance tables

After copy, run:

- table row count comparison,
- foreign key check,
- selected deterministic content hashes,
- schema version check,
- close/reopen read check.

### Runtime Rebuild

Default rebuild order:

1. Create Turso runtime schema.
2. Recreate active thread shell if needed.
3. Rebuild runtime embeddings from Soul `MemoryItem.embedding_json`.
4. Mark document chunks needing reindex if embeddings are missing.
5. Let active runtime queues start empty unless explicitly imported.

This keeps Runtime disposable as designed.

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Turso local encryption is not mature enough for Soul | Blocks Soul default | Keep SQLCipher default; use Turso only behind experimental gate |
| SQLAlchemy cannot cleanly use `BEGIN CONCURRENT` | Lower concurrency benefit | Add explicit transaction helper for known hot paths |
| Vector search underperforms pgvector | Blocks PostgreSQL removal | Keep pgvector/PostgreSQL runtime until parity passes |
| Runtime/Soul collapse by convenience | Architecture regression | Require separate DB files and tests for write-boundary behavior |
| Failed migration corrupts identity | Critical | Never modify SQLCipher in place; copy-verify-flip only |

## Validation Commands

Expected full validation before default cutover:

```bash
bun run test
bun run build
bun run lint
curl -sS http://localhost:3031/health
```

Focused suites should be added as implementation proceeds:

```bash
bun run test -- apps/server/tests/test_turso_soul.py
bun run test -- apps/server/tests/test_turso_soul_migration.py
bun run test -- apps/server/tests/test_turso_runtime.py
bun run test -- apps/server/tests/test_turso_vector_store.py
```

## References

- `docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md`
- `docs/thesis/three-tier-architecture.md`
- `docs/thesis/portable-core.md`
- `docs/architecture/memory/memory-system.md`
- `docs/prds/crypto/encrypted-core-v1.md`
