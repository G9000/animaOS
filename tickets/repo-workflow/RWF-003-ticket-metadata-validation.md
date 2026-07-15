# RWF-003 - Add ticket metadata validation

- Status: backlog
- Priority: P2
- Scope: `tickets`, `apps`, `packages`, `docs`, `scratchboard`, `scripts`, `tests`, `package.json`
- Parent: `RWF-000`
- Depends on: `RWF-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 17:11 MYT
- Started:
- Completed:

## Goal

Add one read-only organization validator that reports repository metadata and hygiene drift without mutating the tree.

## Deliverables

- Add `scripts/check-repo-organization.ts` with deterministic, aggregated, read-only reporting
- Validate canonical ticket and parent-table statuses, parent-child status synchronization including missing or ambiguous child mappings, and the rule that a non-empty `Completed:` field implies `Status: done`
- Validate recognized manifests for every direct child of `apps/` and `packages/`
- Validate singular `docs/audit/`, an untracked root `debug.log`, and a legacy marker at `scratchboard/README.md`
- Add focused Bun coverage in `tests/repo-organization.test.ts`
- Expose the validator through the root `check:repo` command without changing existing build, lint, or test commands

## Acceptance

- Canonical statuses are accepted and legacy or unknown authoritative statuses are reported; missing, ambiguous, or mismatched parent child-status rows are reported actionably
- A ticket with a non-empty `Completed:` field and any status other than `done` is reported
- Missing direct-child manifests, plural audit layout, tracked `debug.log`, and a missing scratchboard legacy marker are each reported actionably
- Multiple violations are grouped and reported in one read-only run; unexpected filesystem or Git failures use a distinct nonzero failure path
- `bun test tests/repo-organization.test.ts` covers LF/CRLF metadata, supported parent table forms, quoted/unquoted cells, organization checks, aggregation, and clean behavior
- `bun run check:repo` executes the approved validator from the repository root

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.
- 2026-07-15 17:11 MYT - Expanded acceptance to the approved read-only repository organization validator contract.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
