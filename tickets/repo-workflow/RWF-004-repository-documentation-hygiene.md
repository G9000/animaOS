# RWF-004 - Reconcile repository documentation and hygiene

- Status: done
- Priority: P2
- Scope: `AGENTS.md`, `README.md`, `.gitignore`, `docs`, `debug.log`
- Parent: `RWF-000`
- Depends on: `RWF-002`
- Owner: Codex
- PRD: none
- Spec: docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-07-15 17:11 MYT
- Updated: 2026-07-15 21:45 MYT
- Started: 2026-07-15 21:25 MYT
- Completed: 2026-07-15 21:45 MYT

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
- 2026-07-15 21:25 MYT - Codex claimed `RWF-004` on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`; confirmed dependency `RWF-002` is `done`, found no competing claim, and synchronized the parent row to `in_progress`.
- 2026-07-15 21:33 MYT - Completed `RWF-004` after reconciling the manifest-derived app/package map and entry-point navigation, moving audit history to `docs/audit/`, repairing both live consumers, and untracking root `debug.log` without deleting or altering its local contents; synchronized the parent row and completed history.
- 2026-07-15 21:43 MYT - Reopened `RWF-004` after spec review found that the Nx orchestration wording incorrectly implied the full root build selected only `server` and `desktop`; preserved prior completion `2026-07-15 21:33 MYT`, cleared the current completion, and synchronized the parent row/history for repair.
- 2026-07-15 21:45 MYT - Re-completed `RWF-004` after clarifying that only the Nx portion of the root build selects `server` and `desktop`, followed by the separate `cargo check -p animus`; revalidated the manifest contract, focused suite, live organization check, diff, and source exclusion, then synchronized the parent row and one current completed-history entry.

## Validation

- Commands:
  - `bun test tests/repo-organization.test.ts`
  - `bun run check:repo`
  - `bunx nx show projects`
  - PowerShell JSON equality check for root `scripts.build` plus `Select-String -SimpleMatch` for the canonical orchestration wording
  - `rg -n 'docs/audits/2026-06-11-agent-server-audit\.md' docs/superpowers/plans/2026-07-14-dual-session-scope-a6.md tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md`
  - PowerShell `Test-Path` checks for the canonical directory map, architecture index, moved audit, workflow, PRDs, plans, and tickets
  - `git check-ignore -v debug.log`
  - `git ls-files -- debug.log`
  - SHA-256 comparison and `Test-Path` for the local `debug.log` before and after `git rm --cached`
  - `git diff HEAD --name-only | rg '^(apps|packages)/.*/src/|^apps/desktop/src-tauri/'`
  - `git diff --check`
- Changed paths:
  - .gitignore
  - AGENTS.md
  - README.md
  - debug.log (removed from tracking; ignored local file preserved)
  - docs/architecture/system/directory-structure.md
  - docs/audits/2026-06-11-agent-server-audit.md -> docs/audit/2026-06-11-agent-server-audit.md
  - docs/superpowers/plans/2026-07-14-dual-session-scope-a6.md
  - tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md
  - tickets/repo-workflow/RWF-003-ticket-metadata-validation.md
  - tickets/repo-workflow/RWF-004-repository-documentation-hygiene.md
  - tickets/repo-workflow/RWF-000-parent.md
- Notes:
  - focused suite passed 32 tests with 59 assertions and 0 failures; live `check:repo` exited 0 with `Repository organization check passed.`
  - canonical map covers `anima-mod`, `animus`, `desktop`, `local-runtime-daemon`, `server`, and `site`, plus all direct shared packages and the Bun/Nx/uv/Cargo responsibility split
  - `bunx nx show projects` exited 0 and reported the package-manifest JavaScript/TypeScript projects together with `desktop` and `server`; the map records that root build/lint intentionally select only `desktop` and `server`
  - targeted old live-path search returned no matches; all 7 targeted navigation and moved-audit targets resolved
  - root `debug.log` remains locally present with identical SHA-256 `BDC3EAD49DA4403CD114C09B700F7607914AC783BD415064D08D7F1C15CCC5A6`, is matched by `.gitignore:2:/debug.log`, and is absent from `git ls-files`
  - source-hotspot exclusion returned no paths; no `apps/*/src/**`, `packages/*/src/**`, or `apps/desktop/src-tauri/**` production source changed
  - review follow-up verified root `build` is exactly `node ./scripts/run-nx.cjs run-many -t build --projects=server,desktop && cargo check -p animus`; the directory map now distinguishes the Nx selection from the subsequent Cargo check
  - residual risks or follow-ups: none within the approved repository-organization scope
