# Memory Integrity Hardening

Cross-cutting soul-store integrity tickets surfaced by the Inner Life v1
review cycle (PRs #108 IL-004, #112 IL-005). These are systemic root causes
that each generated multiple individual review findings — better fixed once
here than patched per-query in feature PRs.

Child ticket prefix: `MIH`

- `MIH-001` — Enforce SQLite foreign keys (PRAGMA foreign_keys = ON)
- `MIH-002` — Single "active memory item" query definition
- `MIH-003` — Triage the pre-existing test-failure baseline (47 → 54 drift)
