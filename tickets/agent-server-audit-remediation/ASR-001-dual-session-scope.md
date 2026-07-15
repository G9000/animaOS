# ASR-001 - Shared session_scope/dual_session_scope helpers (audit A-6)

- Status: backlog
- Priority: P2
- Scope: `apps/server/src/anima_server/db`, `services/agent/soul_writer.py`, `services/agent/eager_consolidation.py`, `services/agent/sleep_agent.py`
- Parent: none
- Depends on: docs/audit/2026-06-11-agent-server-audit.md (finding A-6)
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/P2-runtime-messages.md (§ dual-session pattern)
- Plan: docs/superpowers/plans/2026-07-14-dual-session-scope-a6.md
- Created: 2026-07-14
- Updated: 2026-07-14

## Goal

Fix audit finding A-6: copy-pasted dual-DB commit ordering is a latent
consistency bug. Add `anima_server.db.helpers` with `session_scope()` and
`dual_session_scope()` (soul commits first, runtime second — at-least-once
promotion relying on Soul Writer content-hash idempotency) and migrate the
mechanical call sites (9 in this pass).

## Out of scope / follow-up

- `inner_monologue.py` quick/deep reflection blocks (interleaved
  SoulBlockConflict handling; needs its own careful pass)
- `consolidation.py` Phase A/B/C (sessions deliberately released around the
  LLM call; restructuring risks re-pinning pool connections)
- `service.py` runtime commits (high-churn file; single-store only)
- Remaining `sleep_agent.py` tasks with mixed factories

## Validation

- `bun run test -- tests/test_db_helpers.py tests/test_soul_writer.py -v`
- Full backend suite: `bun run test`
