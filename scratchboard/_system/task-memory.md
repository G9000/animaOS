# Task Memory

## 2026-05-17: Memory And Recall Audit

- Static audit found three improvement phases:
  - Phase 1: reliability hardening for write, promotion, indexing, and health.
  - Phase 2: recall quality through explicit tools, better transcript/conversation recall, and regression probes.
  - Phase 3: structured provenance/event memory for latest/count/temporal recall.
- Highest-risk finding: `soul_writer.py` appears to pass a SQLAlchemy session across thread/event-loop boundaries during inline embedding.
- Architecture drift to resolve: project guidance says SQLite + SQLCipher, but runtime recall infrastructure uses PostgreSQL/pgvector.
- Tracking location: `scratchboard/v2-memory-recall-reliability/todo.md`.
- Durable plan: `docs/superpowers/plans/2026-05-17-memory-recall-improvements.md`.
