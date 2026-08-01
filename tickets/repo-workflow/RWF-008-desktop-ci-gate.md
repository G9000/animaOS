# RWF-008 - CI gate for the desktop app (typecheck, tests, build)

- Status: done
- Priority: P2
- Scope: `.github/workflows`
- Parent: none
- Depends on: none
- Owner: Claude
- PRD: none
- Spec: none
- Plan: none
- Created: 2026-08-02 02:35 MYT
- Updated: 2026-08-02 03:13 MYT
- Started: 2026-08-02 02:35 MYT
- Completed: 2026-08-02 03:13 MYT
Standalone follow-up beyond the closed repo-workflow initiative — registered
in `RWF-000`'s follow-ups, not as a child of that `done` parent. Sibling of
`RWF-007`, which closed the same hole for the server suite.

## Goal

`RWF-007` made the Python suite a required PR gate, but nothing gates the
desktop app: a PR touching only `apps/desktop` reports **no checks at all**,
so its typecheck, unit tests, and build run solely on a developer's machine.

That gap is not theoretical. PR #131 (IL-009) spent fifteen review rounds on
desktop code — thread-close races, stale-closure routing, cross-thread
promise reuse, seed-context loss — with zero CI coverage the whole time; the
only thing standing between those changes and `main` was a human remembering
to run three commands locally.

## Deliverables

- `.github/workflows/desktop-tests.yml` running, on every PR that touches
  the desktop app or a workspace package it imports:
  - `bunx tsc --noEmit`
  - `bun test tests/`
  - `bun run build` (tsc + vite build)
- Path filter covering `apps/desktop/**`, the imported workspace packages
  (`api-client`, `standard-templates`, `ascii-motion`,
  `anima-runtime-daemon-contracts`), the root `package.json`, and `bun.lock`.

## Acceptance

- A desktop-only PR reports the new check, and the check fails when
  typecheck, tests, or the build fail.
- The gate is green on current `main` (verified before merge — a gate that
  starts red teaches people to ignore it).

## Activity Log

- 2026-08-02 02:35 MYT - Ticket created and implemented on branch `rwf-008-desktop-ci`.
  Baseline verified on merged `main` first: 109/109 desktop tests pass,
  `tsc --noEmit` clean, build clean — so the gate starts green.

- 2026-08-02 03:05 MYT - PR #133 review round 1 (P1): the path filter missed
  `anima-auth-contracts` — api-client depends on it and re-exports its
  types to the desktop, so a contract change could break the desktop
  typecheck without triggering the gate. Added; and rather than leave the
  list hand-maintained (this is the same class of miss that cost #129
  three rounds), `apps/desktop/tests/ci-path-filter.test.ts` now derives
  the desktop's transitive workspace-dependency closure from package.json
  files and fails when the workflow omits a member — so the gate polices
  its own trigger list. Verified it fails when a package is removed.

- 2026-08-02 03:13 MYT - CLOSED OUT: PR #133 merged to main as `8ab688e` after one
  review round (transitive auth-contracts path, fixed with a
  self-verifying closure test). The gate ran on its own PR and passed in
  33s, so the desktop app now has typecheck/test/build coverage on every
  relevant PR — the hole RWF-007 left is closed.

## Validation

- Commands:
  - `bun test tests/` (apps/desktop) — 111 passed, 0 failed (incl. the 2
    new path-filter closure tests)
  - `bunx tsc --noEmit` (apps/desktop) — clean
  - CI: the `desktop` check passed on PR #133 itself (33s)
  - `bun run build` (apps/desktop, the exact command the workflow runs) —
    pass, 2026-08-02 02:36 MYT
- Changed paths:
  - `.github/workflows/desktop-tests.yml` (new)
  - `apps/desktop/tests/ci-path-filter.test.ts` (new)
- Notes:
  - The workflow is exercised for the first time by this ticket's own PR
    (it triggers on its own path).
