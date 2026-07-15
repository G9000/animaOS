# RWF-004 - Reconcile repository documentation and hygiene

- Status: backlog
- Priority: P2
- Scope: `AGENTS.md`, `README.md`, `.gitignore`, `docs`, `debug.log`
- Parent: `RWF-000`
- Depends on: `RWF-002`
- Owner: unassigned
- PRD: none
- Spec: docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-07-15 17:11 MYT
- Updated: 2026-07-15 17:34 MYT
- Started:
- Completed:

## Goal

Make repository navigation and tracked hygiene match the live monorepo without changing production source.

## Deliverables

- Rewrite the canonical directory map from live application, package, and workspace manifests
- Consolidate audit history under singular `docs/audit/` and repair tracked references
- Ignore and untrack root `debug.log`
- Add current repository-map navigation to `README.md` and `AGENTS.md`

## Acceptance

- The directory map covers every direct application and package manifest plus the repository's current documentation and tooling roots without volatile counts
- `docs/audits/` is removed, the audit file exists under `docs/audit/`, and live tracked links use the singular path
- `/debug.log` is matched by `.gitignore` and `debug.log` is absent from `git ls-files`
- `README.md` links to the canonical directory map and `AGENTS.md` lists the current app/package navigation
- The live organization check passes while relying on the `scratchboard/README.md` marker already delivered by `RWF-002`
- The changed-path diff contains no `apps/*/src/**`, `packages/*/src/**`, or `apps/desktop/src-tauri/**` production source paths

## Activity Log

- 2026-07-15 17:11 MYT - Ticket created from the approved repository-organization spec and implementation plan.
- 2026-07-15 17:27 MYT - Removed scratchboard-marker ownership; this ticket only verifies the marker through the live organization check.
- 2026-07-15 17:34 MYT - Added the `RWF-002` dependency so hygiene validation consumes the completed legacy marker.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - backlog ticket only
