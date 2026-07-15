# RWF-004 - Reconcile repository documentation and hygiene

- Status: backlog
- Priority: P2
- Scope: `AGENTS.md`, `README.md`, `.gitignore`, `docs`, `scratchboard`, `debug.log`
- Parent: `RWF-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Spec: docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-07-15 17:11 MYT
- Updated: 2026-07-15 17:11 MYT
- Started:
- Completed:

## Goal

Make repository navigation and tracked hygiene match the live monorepo without changing production source.

## Deliverables

- Rewrite the canonical directory map from live application, package, and workspace manifests
- Consolidate audit history under singular `docs/audit/` and repair tracked references
- Ignore and untrack root `debug.log`
- Add current repository-map navigation to `README.md` and `AGENTS.md`
- Add the legacy-only `scratchboard/README.md` marker required by the approved workflow

## Acceptance

- The directory map covers every direct application and package manifest plus the repository's current documentation and tooling roots without volatile counts
- `docs/audits/` is removed, the audit file exists under `docs/audit/`, and live tracked links use the singular path
- `/debug.log` is matched by `.gitignore` and `debug.log` is absent from `git ls-files`
- `README.md` links to the canonical directory map and `AGENTS.md` lists the current app/package navigation
- `scratchboard/README.md` identifies scratchboard as legacy and routes new work to the PRD-plan-ticket workflow
- The changed-path diff contains no `apps/*/src/**`, `packages/*/src/**`, or `apps/desktop/src-tauri/**` production source paths

## Activity Log

- 2026-07-15 17:11 MYT - Ticket created from the approved repository-organization spec and implementation plan.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - backlog ticket only
