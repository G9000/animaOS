# RWF-007 - CI gate for the server test suite + enforced Inner Life lifecycle test

- Status: done
- Priority: P1
- Scope: `.github/workflows`, `apps/server/tests`
- Parent: none
- Depends on: none
- Owner: Claude
- PRD: none
- Spec: none
- Plan: none
- Created: 2026-07-30 15:50 MYT
- Updated: 2026-07-30 15:50 MYT
- Started: 2026-07-30 15:50 MYT
- Completed: 2026-07-30 15:50 MYT

Standalone follow-up beyond the closed repo-workflow initiative — registered
in `RWF-000`'s follow-ups, not as a child of that `done` parent.

## Goal

The Python server suite (~3,200 tests) never runs in CI — the only PR gates
are ruff lint and the CoreFS provenance jobs. Every green baseline is
therefore a snapshot, not a guarantee, and this is exactly how the previous
54-failure baseline rot formed (see MIH-003). Two deliverables close it:

1. A `Server Tests` workflow running `pytest` on every PR that touches
   `apps/server`, `packages/anima-core`, or the lockfiles — with the Rust
   toolchain (anima-core is a maturin workspace member built by uv) and
   cargo/uv caching.
2. The 18-check Inner Life behavioral lifecycle scenario (built during the
   IL-000 closeout as a throwaway verification script) promoted into the
   suite as `tests/test_inner_life_e2e.py`, so the cross-feature seams —
   affect -> catch-up -> drives -> initiative -> poll/ack -> dream ->
   dream-residue -> right-to-forget — are enforced, not verified once.

## Deliverables

- `.github/workflows/server-tests.yml` (new)
- `apps/server/tests/test_inner_life_e2e.py` (new; monkeypatch-scoped seams,
  no module-global mutation)

## Acceptance

- The workflow runs the suite on PRs and fails on any test failure.
- `test_inner_life_e2e.py` passes in-suite and standalone.
- Full suite green with the new test included.

## Activity Log

- 2026-07-30 15:50 MYT - Ticket created; workflow + promoted e2e test
  implemented on branch `rwf-007-server-test-gate`.

## Validation

- Commands:
  - `uv run pytest tests/test_inner_life_e2e.py` — 1 passed (standalone)
  - Full suite (`bun run test`) with the new e2e test included — **3167
    passed, 0 failed, 10 skipped**, run 2026-07-30 16:25 MYT
- Changed paths:
  - `.github/workflows/server-tests.yml`
  - `apps/server/tests/test_inner_life_e2e.py`
- Notes:
  - The workflow itself is exercised for the first time by this ticket's own
    PR (it triggers on `.github/workflows/server-tests.yml`).
