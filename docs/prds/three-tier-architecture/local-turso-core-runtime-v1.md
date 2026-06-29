# Local Turso Core and Runtime v1

**Status:** Draft
**Date:** 2026-06-29
**Owner:** AnimaOS Engineering
**Related plan:** [2026-06-29 Local Turso Core and Runtime](../../superpowers/plans/2026-06-29-local-turso-core-runtime.md)

## Summary

AnimaOS should investigate replacing the current SQLCipher Soul database and embedded PostgreSQL Runtime database with local Turso Database files, without using Turso Cloud and without weakening the three-tier identity boundary.

This version defines a local-first migration path:

1. Keep Soul and Runtime physically separate.
2. Move the Soul DB only after local encryption, unlock, migration, rollback, and concurrency behavior are proven.
3. Move Runtime after schema, transaction, vector, and rebuild parity are proven.
4. Preserve the encrypted transcript Archive unchanged.

The goal is not to collapse ANIMA into one database. The goal is to make the local database substrate simpler and more concurrent while keeping the Core portable, user-owned, encrypted, and mortal.

## Context

Current architecture:

- Soul: per-user SQLCipher database under `.anima/`, holding durable identity, memory, claims, episodes, emotional patterns, user keys, and self-model state.
- Runtime: embedded PostgreSQL through `pgserver`, holding active messages, runs, pending memory work, session notes, document chunks, workflow checkpoints, and pgvector embeddings.
- Archive: encrypted JSONL transcripts under `.anima/transcripts/`.

PostgreSQL was introduced because SQLite/SQLCipher has a single-writer ceiling and because runtime vector search needed pgvector. Turso Database may change the local tradeoff: it is SQLite-compatible, local-first, and offers an MVCC mode with concurrent write support through `BEGIN CONCURRENT`.

The migration must treat that as an engineering hypothesis, not as a settled fact. Turso local encryption and vector support are promising, but they do not behave exactly like SQLCipher plus PostgreSQL. The product requirement is therefore a gated, measurable migration path.

## Product Goals

1. Remove the need for embedded PostgreSQL if Turso Runtime reaches feature and performance parity.
2. Preserve the Soul/Runtime/Archive architecture as a cognitive boundary, even if Soul and Runtime use the same database engine.
3. Preserve local-only operation. No Turso Cloud account, remote sync, or hosted database is required for this version.
4. Preserve Core portability: copying `.anima/` to another machine and unlocking with the passphrase must preserve identity.
5. Preserve cryptographic mortality: no readable personal data may exist outside the user-owned encrypted Core boundary.
6. Improve local write concurrency for runtime queues, threads, messages, and background cognitive work.
7. Keep rollback possible until Turso becomes the default local engine.

## What This Version Delivers

### Target Layout

```text
.anima/
  manifest.json
  users/<user_id>/
    soul.db          # local encrypted Turso Database, durable authority
    runtime.db       # local Turso Database, operational state
  transcripts/
    *.jsonl.enc      # encrypted archive, unchanged
```

The filenames may change during implementation, but the physical separation must not.

### Soul Migration

The Soul migration creates a fresh encrypted Turso Soul DB while the existing SQLCipher Soul DB remains intact. Existing Soul rows are copied table-by-table with IDs preserved. The migration flips the manifest to Turso only after verification passes.

Required verification:

- all expected tables exist,
- row counts match,
- foreign key integrity passes,
- selected content hashes match,
- unlock and reopen works after process restart,
- no plaintext personal content is visible through direct file inspection,
- rollback to the original SQLCipher DB is still possible.

### Runtime Replacement

Runtime replacement moves PostgreSQL runtime tables to a local Turso runtime DB. This work may choose either:

- a fresh runtime rebuild from Soul plus transcripts, or
- an import of active PostgreSQL runtime state.

Fresh rebuild is the default product path because Runtime is operational and rebuildable by design.

### Vector Replacement

Runtime embeddings currently depend on pgvector. Turso Runtime must provide retrieval parity before PostgreSQL can be removed. The first version may use Turso vector functions or a bounded local fallback, but the retrieval quality and latency must be measured against the current pgvector path.

### User-Visible Behavior

- Users still unlock ANIMA with the Core passphrase.
- Existing memories, profile, self-model, tasks, claims, and episodes remain present after migration.
- Chat, memory search, document RAG, and settings continue to work.
- The desktop app no longer needs an embedded PostgreSQL process once the runtime cutover is complete.

## Rules And Constraints

1. Turso Cloud is out of scope for v1.
2. The Soul and Runtime tiers must remain separate database files.
3. The Archive tier remains encrypted JSONL and is not migrated into Turso in v1.
4. SQLCipher remains a supported rollback source until the migration is proven and explicitly retired.
5. The migration must not overwrite the existing Soul DB in place.
6. The manifest must record the active database engine and schema version.
7. Soul encryption must be derived from the existing Core passphrase flow using a separate domain key, for example `HKDF("turso-soul-v1")`.
8. Runtime writes using Turso MVCC must use a transaction helper with retry behavior for write conflicts.
9. Runtime-to-Soul write boundary remains intact: runtime queues may request promotion, but only Soul Writer/consolidation writes durable identity.
10. PostgreSQL removal is not complete until health checks, tests, docs, and startup lifecycle no longer require `pgserver`.

## Success Metrics

| Metric | Target | How to measure |
| --- | --- | --- |
| Soul migration integrity | 100% row count match for migrated tables plus sampled content hash match | Migration verifier test |
| Soul encryption | No readable memory/profile/user text in raw DB bytes | Fixture inspection test |
| Portability | Copied Turso Core unlocks on a clean data dir | End-to-end portability test |
| Runtime concurrency | Concurrent thread/message/candidate writes complete with retry and no lost rows | Stress test |
| Retrieval parity | Memory/document/image retrieval returns comparable top results to pgvector baseline | Golden corpus test |
| Startup simplification | Server boots without embedded PostgreSQL after runtime cutover | Health/startup smoke test |
| Rollback | A failed Soul migration leaves SQLCipher as the active manifest engine | Migration failure test |

## Out Of Scope

- Turso Cloud, remote replicas, hosted sync, or account-based storage.
- Merging Soul and Runtime into one database file.
- Removing the Archive tier.
- Replacing the Core passphrase with Turso-managed passphrase derivation.
- Multi-device conflict resolution.
- A full database admin UI.
- Retiring SQLCipher before Turso Soul has passed migration, encryption, portability, and rollback gates.

## Open Questions

1. Which Python driver and SQLAlchemy integration should become the production path for local Turso Database?
2. Can SQLAlchemy sessions reliably emit `BEGIN CONCURRENT`, or do write paths need an explicit transaction wrapper outside normal session begin behavior?
3. Is Turso local encryption mature enough for the Soul DB, or should the first production path use app-level field encryption plus SQLCipher fallback?
4. Should Runtime be encrypted with a separate runtime key, or treated as rebuildable but still encrypted for defense in depth?
5. Does Turso vector search meet ANIMA's expected memory and document retrieval latency at realistic local corpus sizes?

## References

- [Three-Tier Cognitive Architecture](../../thesis/three-tier-architecture.md)
- [Portable Core](../../thesis/portable-core.md)
- [Memory System](../../architecture/memory/memory-system.md)
- [Encrypted Core v1](../crypto/encrypted-core-v1.md)
- [Turso concurrent writes](https://docs.turso.tech/tursodb/concurrent-writes)
- [Turso local encryption](https://docs.turso.tech/tursodb/encryption)
- [Turso Python reference](https://docs.turso.tech/sdk/python/reference)
- [Turso AI and embeddings](https://docs.turso.tech/features/ai-and-embeddings)
