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
- Updated: 2026-07-30 22:01 MYT
- Started: 2026-07-30 15:50 MYT
- Completed: 2026-07-30 22:01 MYT

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

- 2026-07-30 16:42 MYT - PR #129 review round 1: path filter extended to
  every native build input (anima-corefs, anima-file-tools, workspace
  Cargo.toml/Cargo.lock — anima-core path-depends on both crates and the
  Rust-only CoreFS workflow never runs pytest), and the job now syncs
  `--all-packages --all-extras` per AGENTS.md before running pytest with
  `--no-sync` (a plain run pruned the docling extra, gating on a degraded
  no-parser environment).

- 2026-07-30 19:54 MYT - PR #129 review round 2 (P1 + P2), REOPENED and
  re-closed per the workflow's reopen routine (the round-1 fixes at
  16:42 were acceptance-affecting — they changed which PRs are gated and
  which environment is tested — but were recorded onto a ticket still
  completed at 15:50; prior completion timestamps 15:50 and the round-1
  edit are preserved above). Round-2 fixes: the e2e lifecycle test now
  builds unresolved_thread pressure through PRODUCTION ticks from a
  sub-theta seed (0.3 + real foresight growth), and dream_residue is not
  seeded at all — which immediately exposed a REAL bug the preloaded
  values had masked: drive_states.updated_at carried onupdate=func.now(),
  so the IL7 dream-attempt marker re-stamped the drive Δt reference with
  the wall clock, silently erasing the accumulated growth window since
  the last tick. onupdate removed (client-side; no migration) with a
  regression test. Revalidated on this corrected head: full suite 3168
  passed / 0 failed / 10 skipped at 19:54 MYT.

- 2026-07-30 22:01 MYT - PR #129 review round 3 (2 P2s), completion
  re-stamped: the path filter gained `.python-version` (uv resolves the
  interpreter from the root pin) and the suite's direct non-code inputs
  (`corefs-provenance.yml`, `docs/superpowers/plans/**`,
  `docs/benchmarks/**` — test_corefs_catalog_benchmark reads and asserts
  them); RWF-000 gained the missing planning activity entry for this
  ticket's registration. Filter changes are validated live by this PR's
  own Server Tests check re-running on the push; the 19:54 MYT full-suite
  evidence remains binding for the unchanged test content.

## Validation

- Commands:
  - `uv run pytest tests/test_inner_life_e2e.py` — 1 passed (standalone)
  - Full suite (`bun run test`) on the round-2 head — **3168 passed,
    0 failed, 10 skipped**, run 2026-07-30 19:54 MYT
- Changed paths:
  - `.github/workflows/server-tests.yml`
  - `apps/server/tests/test_inner_life_e2e.py`
  - `apps/server/src/anima_server/models/runtime_consciousness.py` (Δt fix)
  - `apps/server/tests/test_inner_life_initiative.py` (Δt regression test)
- Notes:
  - The workflow itself is exercised for the first time by this ticket's own
    PR (it triggers on `.github/workflows/server-tests.yml`).
