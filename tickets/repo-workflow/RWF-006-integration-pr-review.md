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
- Updated: 2026-07-16 00:07 MYT
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
- 2026-07-15 23:46 MYT - Rebased the never-pushed branch onto current `origin/main` with local safety ref `codex/repo-organization-project-management-pre-rebase`; intentionally resolved the expected cleanup-spec add/add conflict from `REBASE_HEAD`, verified the final spec matches the safety version, reconciled the new canonical `inner-life-v1` initiative through an ownership-safe RWF-001 reopen/reclose, and reran the full local integration gate while keeping RWF-006 and its parent open.
- 2026-07-15 23:52 MYT - Recorded post-reconciliation range evidence: the committed branch is `0` behind and `30` ahead of current `origin/main`, still spans exactly 54 planned paths, has a conflict-free merge tree equal to HEAD, and remains locally clean without any push or PR action.
- 2026-07-16 00:03 MYT - Added exact reproducible pre-publication PowerShell assertions for the derived merge base, 54 changed paths, zero production-source hotspots, and exactly the two intended repo-owned staged-skill paths; kept RWF-006 and its parent `in_progress` with `Completed:` empty.
- 2026-07-16 00:07 MYT - Pushed only branch `codex/repo-organization-project-management` with upstream tracking at `0f02ce73c307f7f6a03d45286a5b48720273f4e1` and opened draft PR [#99](https://github.com/G9000/animaOS/pull/99) against `main` with the required review contract; verified the PR is draft and unmerged, removed the ignored temporary body file, and intentionally withheld `@codex review` until this evidence commit is pushed and `headRefOid` catches up.

## Validation

### Superseded pre-rebase Task 10 snapshot

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

### Current-main rebase and reconciliation validation

- Exact commands:
  - `git status --short --untracked-files=all`
  - `git fetch origin main`
  - `git branch codex/repo-organization-project-management-pre-rebase HEAD`
  - `git -c core.editor=true rebase origin/main`
  - `git ls-files -u -- docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md`
  - `git rev-parse :2:docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md :3:docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md REBASE_HEAD:docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md origin/main:docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md`
  - `git diff --no-ext-diff --unified=3 :2:docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md :3:docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md`
  - `git restore --source=REBASE_HEAD --worktree -- docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md`
  - `git add -- docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md`
  - `git -c core.editor=true rebase --continue`
  - `git diff --name-only codex/repo-organization-project-management-pre-rebase..HEAD -- docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md`
  - `git merge-base HEAD origin/main`
  - `git rev-list --left-right --count origin/main...HEAD`
  - `git merge-tree --write-tree origin/main HEAD`
  - `bun run check:repo`
  - PowerShell/Bun graph counter:
    ```powershell
    @'
    import { Glob } from "bun";
    import { readFile } from "node:fs/promises";
    import { parseTicketDocument } from "./scripts/check-repo-organization.ts";
    const docs = [];
    for await (const path of new Glob("tickets/**/*.md").scan(".")) {
      const doc = parseTicketDocument(path, await readFile(path, "utf8"));
      if (doc.ticketId) docs.push(doc);
    }
    const parents = docs.filter((doc) => doc.hasAuthoritativeChildTable);
    const ids = new Set(parents.map((doc) => doc.ticketId));
    console.log("PARENTS=" + parents.length);
    console.log("AUTHORITATIVE_ROWS=" + parents.reduce((n, doc) => n + doc.parentRows.length, 0));
    console.log("REVERSE_REFS=" + docs.filter((doc) => doc.parent && doc.parent !== "none" && ids.has(doc.parent)).length);
    '@ | bun -
    ```
  - PowerShell/Bun dashboard parser:
    ```powershell
    @'
    import { readFile } from "node:fs/promises";
    import { join } from "node:path";
    const text = await readFile("tickets/README.md", "utf8");
    const block = (name) => text.match(new RegExp("^## " + name + "\\r?\\n([\\s\\S]*?)(?=^## )", "m"))?.[1] ?? "";
    const lines = (name) => block(name).split(/\r?\n/).filter((line) => line.startsWith("- "));
    const active = lines("Active Initiatives");
    const completed = lines("Completed Initiatives");
    const legacy = lines("Legacy or Unclassified");
    const entries = [...active, ...completed];
    const links = entries.map((line) => line.match(/^- \[[^\]]+\]\((\.\/[^)]+\.md)\)/)?.[1]).filter(Boolean);
    let errors = 0;
    for (let i = 0; i < links.length; i += 1) {
      const status = (await readFile(join("tickets", links[i].slice(2)), "utf8")).match(/^- Status: (.+)$/m)?.[1].trim();
      const expected = status === "done" ? "completed" : "active";
      const actual = i < active.length ? "active" : "completed";
      if (expected !== actual) errors += 1;
    }
    console.log("DASHBOARD_ACTIVE=" + active.length);
    console.log("DASHBOARD_COMPLETED=" + completed.length);
    console.log("DASHBOARD_LEGACY=" + legacy.length);
    console.log("DASHBOARD_PARENT_LINKS=" + links.length);
    console.log("DASHBOARD_UNIQUE_PARENT_LINKS=" + new Set(links).size);
    console.log("DASHBOARD_CLASSIFICATION_ERRORS=" + errors);
    if (active.length !== 12 || completed.length !== 6 || legacy.length !== 1 || links.length !== 18 || new Set(links).size !== 18 || errors !== 0) process.exit(1);
    '@ | bun -
    ```
  - `bun test tests/repo-organization.test.ts`
  - `python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management`
  - `bunx nx show projects`
  - `bun run build`
  - `git diff --check origin/main...HEAD`
  - `git diff --check`
  - PowerShell production-source and exact staged-skill assertions over `git diff --name-only origin/main...HEAD`
- Current reconciliation changed paths:
  - tickets/README.md
  - tickets/repo-workflow/RWF-001-ticket-dashboard.md
  - tickets/repo-workflow/RWF-006-integration-pr-review.md
  - tickets/repo-workflow/RWF-000-parent.md
- Results:
  - fetched `origin/main` `408d9b64abf639739a2d044abfda647958e7ff3e`; safety ref `codex/repo-organization-project-management-pre-rebase` remains local at pre-rebase head `142a5c91cd5a14c4708adc93b5feb1f461ca8950`
  - the only rebase conflict was the expected cleanup-spec add/add: stage 2/origin was `da8dfb3825135c524bbaa8bb917d190fe1439a33`, while stage 3 and `REBASE_HEAD` both were `79bc20274dbfc955865f79af94306fc1bdf86ef0`; the instructed replayed-commit version was staged and all 29 commits replayed
  - final cleanup-spec blob `c05594c1629bbece1b9b2bb566403214163b694f` exactly matches the safety ref; the spec comparison produced no changed path
  - rebased Task 10 head/commit is `85a3475a089d7a6de70d0e571647a63076c4081f`; merge base equals current `origin/main`, ahead/behind is `0 29`, and conflict-free merge-tree `e9007efdbe78601460e4e13c74360f8652645806` equals the HEAD tree
  - current ticket graph passes with 18 conforming parents, 153 authoritative rows, 153 reverse references, and 0 organization violations; all 8 `IL-000` through `IL-007` documents are canonical `backlog`, and all are unassigned
  - current dashboard passes with 12 active, 6 completed, 1 legacy, 18 parent links, 18 unique parent links, and 0 classification errors; `inner-life-v1` resolves to backlog parent `IL-000`
  - focused validation passed with 32 tests, 59 assertions, and 0 failures; the organization check and official skill validator passed; Nx discovered 9 projects
  - root build exited 0 after server and desktop Nx builds plus `cargo check -p animus`; Vite's 2,137.26 kB chunk warning remains non-blocking
  - committed range contains 29 branch commits and 54 paths before this reconciliation commit, with 0 production-source hotspots and exactly the 2 intended staged-skill files
  - after the single four-file reconciliation commit, the final local range is 30 commits and the same 54 paths, with ahead/behind `0 30`, 0 production-source hotspots, and exactly 2 intended staged-skill files
  - RWF-001 was reopened and reclosed under its unchanged Codex ownership and Started timestamp; prior completions `2026-07-15 19:46 MYT` and `2026-07-15 20:06 MYT` remain in activity, while one current `2026-07-15 23:43 MYT` parent history entry exists
  - runtime smoke and `GET /health` remain not applicable because no application runtime behavior changed; no push, PR, comment, or merge occurred, and the local safety ref has not been pushed

### Reproducible pre-publication scope assertions

- Exact PowerShell commands:
  ```powershell
  $base = git merge-base HEAD origin/main
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($base)) { throw 'Unable to derive merge base' }
  $changed = @(git diff --name-only "$base..HEAD")
  if ($LASTEXITCODE -ne 0) { throw 'Unable to derive changed paths' }
  $hotspots = @($changed | Where-Object { $_ -match '^(apps|packages)/.*/src/|^apps/desktop/src-tauri/' })
  $skills = @($changed | Where-Object { $_ -like '.codex-skill-staging/*' } | Sort-Object)
  $expectedSkills = @(
    '.codex-skill-staging/anima-project-management/SKILL.md',
    '.codex-skill-staging/anima-project-management/agents/openai.yaml'
  )
  $skillDelta = @(Compare-Object -ReferenceObject $expectedSkills -DifferenceObject $skills)
  if ($base -ne '408d9b64abf639739a2d044abfda647958e7ff3e') { throw "Unexpected merge base: $base" }
  if ($changed.Count -ne 54) { throw "Expected 54 changed paths, found $($changed.Count)" }
  if ($hotspots.Count -ne 0) { $hotspots; throw "Expected 0 production-source hotspots, found $($hotspots.Count)" }
  if ($skills.Count -ne 2 -or $skillDelta.Count -ne 0) { $skills; $skillDelta; throw 'Staged-skill scope mismatch' }
  "BASE=$base"
  "CHANGED_PATHS=$($changed.Count)"
  "HOTSPOTS=$($hotspots.Count)"
  "STAGED_SKILLS=$($skills.Count)"
  $skills
  ```
- Expected assertions:
  - `BASE=408d9b64abf639739a2d044abfda647958e7ff3e`
  - `CHANGED_PATHS=54`
  - `HOTSPOTS=0`
  - `STAGED_SKILLS=2`
  - `.codex-skill-staging/anima-project-management/SKILL.md`
  - `.codex-skill-staging/anima-project-management/agents/openai.yaml`

### Task 11 draft PR publication evidence

- Exact commands:
  - `git push -u origin codex/repo-organization-project-management`
  - `gh pr create --draft --base main --head codex/repo-organization-project-management --title "workflow: organize repository project management" --body-file .tmp-eval-pr-body.md`
  - `gh pr view 99 --json number,url,title,headRefName,headRefOid,baseRefName,isDraft`
  - `gh api graphql -F owner=G9000 -F repo=animaOS -F number=99 -f 'query=query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$number){merged}}}'`
- Results:
  - pushed only `codex/repo-organization-project-management` and set upstream to `origin/codex/repo-organization-project-management`; initial publication OID is `0f02ce73c307f7f6a03d45286a5b48720273f4e1`
  - draft PR [#99](https://github.com/G9000/animaOS/pull/99) has exact title `workflow: organize repository project management`, base `main`, head branch `codex/repo-organization-project-management`, and initial `headRefOid` `0f02ce73c307f7f6a03d45286a5b48720273f4e1`
  - `isDraft` is `true`; GraphQL `merged` is `false`
  - the PR body contains `Summary`, `Scope`, `Review focus`, `Out of scope`, and `Validation`; it excludes runtime behavior, migrations, personal skill installation, and merge
  - ignored `.tmp-eval-pr-body.md` was removed after PR creation; no `@codex review` comment was posted before the publication-evidence commit and subsequent PR-head synchronization
