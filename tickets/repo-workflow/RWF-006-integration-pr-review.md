# RWF-006 - Validate, publish, and complete PR review

- Status: in_progress
- Priority: P1
- Scope: repository validation, Git metadata, draft PR, Codex review, ticket closeout
- Parent: `RWF-000`
- Depends on: `RWF-001`, `RWF-002`, `RWF-003`, `RWF-004`, `RWF-005`
- Owner: Codex
- PRD: none
- Spec: docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md; docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-07-15 17:11 MYT
- Updated: 2026-07-15 23:23 MYT
- Started: 2026-07-15 23:21 MYT
- Completed:

## Goal

Carry the repository-workflow implementation through final validation and a clean current-head Codex review, then commit project closeout and guard the final metadata head without merging.

## Deliverables

- Record focused organization, skill, workspace, build, diff, and scope validation
- Publish or update the explicitly user-authorized draft PR with the required review contract
- Request and monitor thread-aware Codex review until the implementation head is clean
- After that clean implementation head, close child and parent metadata in one commit, push it, and request review of the final closeout head

## Acceptance

- `RWF-001` through `RWF-005` are `done`, focused organization and skill checks pass, the required build passes, and final diff/scope checks are clean
- With explicit user authorization, a scoped draft PR is opened or updated with `Summary`, `Scope`, `Review focus`, `Out of scope`, and `Validation` sections
- The exact standalone comment `@codex review` is posted after each head requiring review
- Before closeout, thread-aware `reviewThreads(first: 100)` evidence shows the latest Codex review targets the implementation `headRefOid` and zero unresolved non-outdated actionable threads remain
- Non-actionable threads have evidence-based dispositions, required checks pass on the clean implementation head, and no merge occurs without separate user authorization
- The closeout metadata commit records `RWF-006` and `RWF-000` as `done`, is pushed, and receives a fresh exact `@codex review` request

## Post-closeout Terminal Guard

- Review the final closeout head with the same current-head, required-check, disposition, and zero-actionable-thread stopping rule.
- If actionable feedback invalidates acceptance, reopen the affected child, `RWF-006`, and `RWF-000` consistently, fix and validate narrowly, close again, push, re-request `@codex review`, and repeat.
- Leave the draft PR unmerged unless the user separately authorizes merge.

## Activity Log

- 2026-07-15 17:11 MYT - Ticket created from the approved repository-organization and project-management skill specs and implementation plan.
- 2026-07-15 17:27 MYT - Clarified clean implementation-head closeout followed by a terminal final-head review guard.
- 2026-07-15 23:21 MYT - Codex claimed the ticket on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management` after confirming `RWF-001` through `RWF-005` are `done`; set child and parent row to `in_progress` and preserved parent ownership.
- 2026-07-15 23:23 MYT - Recorded final local integration verification on implementation head `71dc234e3e114b3d5fb034803e0e2dcaf2822f0d`: focused checks, repository validation, staged-skill validation, workspace discovery, root build, merge-base diff, and scope assertions passed; kept `RWF-006` and `RWF-000` `in_progress` for the authorized Task 11-13 publication and current-head review loop.

## Validation

- Commands:
  - `rg -n '^- Status: done\r?$' tickets/repo-workflow -g 'RWF-00[1-5]-*.md'`
  - `bun test tests/repo-organization.test.ts`
  - `bun run check:repo`
  - `python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management`
  - `bunx nx show projects`
  - `bun run build`
  - `git diff --check origin/main...HEAD`
  - `git branch --show-current`
  - `git rev-parse HEAD`
  - `git rev-parse origin/main`
  - `git merge-base HEAD origin/main`
  - `git log --oneline origin/main..HEAD`
  - `$mergeBase = git merge-base HEAD origin/main; git diff --name-only "$mergeBase..HEAD"`
  - PowerShell exact/planned-scope, production-source, staged-skill, and personal-path assertions over `git diff --name-only "$mergeBase..HEAD"`
  - `git status --short --untracked-files=all`
  - `git check-ignore -v apps/desktop/dist/index.html dist/anima_server-0.1.0.tar.gz target/.rustc_info.json`
- Changed paths:
  - Task 10 bookkeeping: `tickets/repo-workflow/RWF-006-integration-pr-review.md`
  - Task 10 bookkeeping: `tickets/repo-workflow/RWF-000-parent.md`
  - Implementation-head categories from merge base: 2 repo-owned staged-skill files, 5 root metadata files, 8 docs files, 1 scratchboard guide, 1 organization script, 1 focused test file, and 36 ticket files (54 total)
- Notes:
  - `RWF-001` through `RWF-005` each reported `Status: done`; before the claim transaction, the worktree was clean at requested starting head `71dc234e3e114b3d5fb034803e0e2dcaf2822f0d`.
  - Focused validation passed with 32 tests, 59 assertions, and 0 failures; `bun run check:repo` passed; official staged-skill validation reported `Skill is valid!`.
  - Nx discovered 9 configured projects: `@anima/daemon-contracts`, `@anima/auth-contracts`, `@anima/standard-templates`, `@anima/ascii-motion`, `@anima/api-client`, `anima-mod`, `desktop`, `server`, and `site`.
  - `bun run build` exited 0 after Nx built `server` and `desktop` and `cargo check -p animus` finished; Vite emitted the expected non-blocking chunk-size warning for the 2,137.26 kB main bundle.
  - At validation time, `origin/main` was `408d9b64abf639739a2d044abfda647958e7ff3e`, the derived merge base was `e813e748f6fc44bdf03895e1e33a0aeef73e9c69`, and the branch had 28 commits and 54 changed paths from that comparison.
  - `git diff --check origin/main...HEAD` passed. Scope assertions returned 0 production-source hotspots, exactly the 2 intended `.codex-skill-staging/anima-project-management` paths, 0 personal or absolute paths, and 0 paths outside the plan's exact or mechanical ticket-normalization scope.
  - Generated desktop, server-wheel, and Cargo build outputs are ignored by `apps/desktop/.gitignore` or root `.gitignore` and do not enter the tracked Task 10 scope.
  - Runtime smoke and `GET /health` are not applicable because this initiative changes repository metadata, documentation, workflow validation, and tests only; no application runtime behavior changed.
  - Residual work is the explicitly authorized Tasks 11-13 draft-PR and current-head review loop. No push, PR, comment, or merge occurred in Task 10, and merge still requires separate user authority.
