# LTR-007 - Vector retrieval parity without pgvector

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`, `apps/server/src/anima_server/models/runtime_embedding.py`, `apps/server/tests`
- Parent: `LTR-000`
- Depends on: `LTR-005`
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md
- Plan: docs/superpowers/plans/2026-06-29-local-turso-core-runtime.md
- Created: 2026-06-29 15:50 MYT
- Updated: 2026-06-29 15:50 MYT
- Started:
- Completed:

## Goal

Replace pgvector as a hard dependency by implementing Turso-compatible vector storage and retrieval or a measured local fallback.

## Deliverables

- `TursoVecStore` or equivalent vector backend behind the existing vector store interface.
- Turso-compatible embedding storage format.
- Rebuild path from `MemoryItem.embedding_json` and runtime document/image annotation sources.
- Golden corpus comparison against pgvector top-k results.
- Retrieval latency measurements at representative local corpus sizes.

## Acceptance

- Memory search returns comparable top results to the pgvector baseline.
- Document and image annotation embeddings can be rebuilt or marked for reindex.
- BM25/hybrid search can read from the Turso Runtime embedding source.
- Server can run retrieval tests without PostgreSQL or pgvector.
- Any quality or performance gap is documented before default cutover.

## Activity Log

- 2026-06-29 15:50 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none

