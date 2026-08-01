# RWF-008 - CI gate for the desktop app (typecheck, tests, build)

- Status: in_progress
- Priority: P2
- Scope: `.github/workflows`
- Parent: none
- Depends on: none
- Owner: Claude
- PRD: none
- Spec: none
- Plan: none
- Created: 2026-08-02 02:35 MYT
- Updated: 2026-08-02 02:35 MYT
- Started: 2026-08-02 02:35 MYT
- Completed:

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

## Validation

- Commands:
  - `bun test tests/` (apps/desktop) — 109 passed, 0 failed
  - `bunx tsc --noEmit` (apps/desktop) — clean
  - `bun run build` (apps/desktop, the exact command the workflow runs) —
    pass, 2026-08-02 02:36 MYT
- Changed paths:
  - `.github/workflows/desktop-tests.yml` (new)
- Notes:
  - The workflow is exercised for the first time by this ticket's own PR
    (it triggers on its own path).
