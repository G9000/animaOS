# Repository Organization and Project Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align animaOS repository documentation and ticket metadata with the live monorepo, add mechanical organization checks, and install a repo-owned project-management skill that carries approved work through a focused Codex PR-review loop.

**Architecture:** Keep production source paths stable and treat repository metadata as the integration boundary. A read-only Bun/TypeScript validator will parse ticket headers and parent tables, inspect project manifests and documentation hygiene, and aggregate violations without modifying the tree. The repo-owned skill will remain a thin workflow router over `AGENTS.md`, `docs/ops/prd-ticket-workflow.md`, and `tickets/TEMPLATE.md`; a dedicated integration ticket will keep the initiative open through publication and current-head review without blocking dependencies between implementation tickets.

**Tech Stack:** Markdown workflow artifacts, Bun test runner, TypeScript, Git, GitHub CLI and GraphQL, official Codex skill scaffold and validator.

---

## File and Responsibility Map

### New files

- `.codex-skill-staging/anima-project-management/SKILL.md` — concise repo-owned project lifecycle and publish/review workflow.
- `.codex-skill-staging/anima-project-management/agents/openai.yaml` — generated skill display metadata and default invocation.
- `scripts/check-repo-organization.ts` — read-only repository organization validator and CLI.
- `tests/repo-organization.test.ts` — focused unit tests for validator parsing, checks, and reporting.
- `scratchboard/README.md` — legacy-only marker and migration guidance.
- `docs/audit/skills/2026-07-15-anima-project-management-evaluation.md` — baseline and forward skill-evaluation evidence.
- `tickets/repo-workflow/RWF-004-repository-documentation-hygiene.md` — claimable documentation and hygiene cleanup ticket.
- `tickets/repo-workflow/RWF-005-anima-project-management-skill.md` — claimable repo-owned skill ticket.
- `tickets/repo-workflow/RWF-006-integration-pr-review.md` — final verification, publication, review loop, and closeout ticket.

### Modified files

- `AGENTS.md` — live application/package map and mandatory project-management skill routing.
- `README.md` — short link to the canonical directory map.
- `.gitignore` — ignore root `/debug.log`.
- `package.json` — add the nonbreaking `check:repo` command.
- `docs/architecture/system/directory-structure.md` — canonical, count-free map of the polyglot monorepo.
- `docs/ops/prd-ticket-workflow.md` — ownership, assignment, parent synchronization, publication, and review-loop clarifications.
- `docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md` — repair the historical audit path example after the directory move if needed.
- `docs/superpowers/plans/2026-07-14-dual-session-scope-a6.md` — repair links and commands referring to the plural audit directory.
- `tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md` — repair the audit-document reference and normalize current status.
- `tickets/repo-workflow/RWF-000-parent.md` — expand the initiative, synchronize child states, and track closeout.
- `tickets/repo-workflow/RWF-001-ticket-dashboard.md` — align acceptance with the canonical `tickets/README.md` index and record execution.
- `tickets/repo-workflow/RWF-002-scratchboard-legacy.md` — record execution and validation.
- `tickets/repo-workflow/RWF-003-ticket-metadata-validation.md` — align deliverables with the organization validator and record execution.
- `tickets/repo-workflow/README.md` — list all six child tickets and the integration done condition.
- `tickets/README.md` — rebuild active, completed, and legacy/unclassified initiative sections from parent metadata.
- Ticket files whose authoritative header or parent child-status cell uses `todo`, `in-review`, or `in_review` — mechanical status normalization only; historical prose remains unchanged.

### Moved or untracked files

- Move `docs/audits/2026-06-11-agent-server-audit.md` to `docs/audit/2026-06-11-agent-server-audit.md`.
- Remove `debug.log` from Git tracking while leaving local logging behavior untouched.

### Explicitly untouched

- `apps/*/src/**`, `packages/*/src/**`, `apps/desktop/src-tauri/**`, and all other production hotspot files being refactored on another pull request.
- Personal skill directories under `C:/Users/leoca/.codex/skills` and `C:/Users/leoca/.agents/skills`.

---

### Task 1: Extend and start the repo-workflow initiative

**Files:**
- Modify: `tickets/repo-workflow/RWF-000-parent.md`
- Modify: `tickets/repo-workflow/RWF-001-ticket-dashboard.md`
- Modify: `tickets/repo-workflow/RWF-002-scratchboard-legacy.md`
- Modify: `tickets/repo-workflow/RWF-003-ticket-metadata-validation.md`
- Modify: `tickets/repo-workflow/README.md`
- Create: `tickets/repo-workflow/RWF-004-repository-documentation-hygiene.md`
- Create: `tickets/repo-workflow/RWF-005-anima-project-management-skill.md`
- Create: `tickets/repo-workflow/RWF-006-integration-pr-review.md`

- [ ] **Step 1: Capture one MYT timestamp for this logical bookkeeping change**

Run:

```powershell
Get-Date -Format 'yyyy-MM-dd HH:mm "MYT"'
```

Expected: one timestamp such as `2026-07-15 16:30 MYT`; use the same value in related parent and child updates.

- [ ] **Step 2: Expand the parent tracker and start the initiative**

Set `RWF-000` to `Status: in_progress`, `Owner: Codex`, populate `Started:` if empty, update `Updated:`, and append an activity entry naming branch `codex/repo-organization-project-management` and this plan. Add `RWF-004`, `RWF-005`, and `RWF-006` to the child table. Keep new child tickets at `backlog` and `Owner: unassigned` until their task begins.

Use this execution order and dependency shape:

| Ticket | Depends on |
| --- | --- |
| `RWF-005` | none |
| `RWF-001` | none |
| `RWF-002` | `RWF-001` |
| `RWF-003` | `RWF-001` |
| `RWF-004` | `RWF-002` |
| `RWF-006` | `RWF-001`, `RWF-002`, `RWF-003`, `RWF-004`, `RWF-005` |

- [ ] **Step 3: Align existing ticket plan links and acceptance**

Change each `RWF-001` through `RWF-003` `Plan:` field to `docs/superpowers/plans/2026-07-15-repository-organization-project-management.md`. Update `RWF-001` to deliver the concise canonical `tickets/README.md` initiative index rather than a second `tickets/INDEX.md`: classify active, completed, and legacy or unclassified initiatives from normalized parent metadata; link parent trackers and conventions/workflow/check guidance; do not promise next-ticket, blocker, or completion-count reporting; and never infer parent completion from children. Update `RWF-003` to cover the full read-only organization-validator contract from the approved cleanup spec.

- [ ] **Step 4: Create the three new child tickets from `tickets/TEMPLATE.md`**

Give every ticket a single outcome, measurable acceptance, `Owner: unassigned`, `Status: backlog`, and the plan/spec links. `RWF-006` must separate its pre-closeout acceptance from its post-closeout terminal guard:

- focused and broad validation passing;
- an in-scope draft PR with required body sections;
- the exact standalone `@codex review` request;
- a clean current-head Codex review of the implementation head with zero unresolved non-outdated actionable threads;
- no merge without separate authorization;
- after that clean implementation head, Task 13 marks child and parent closeout `done` in one metadata commit, pushes it, and sends a fresh exact `@codex review`;
- post-closeout review of the final head is a terminal guard, not a precondition claimed before the `done` transition, and it reopens affected child/integration/parent state if actionable feedback invalidates acceptance.

- [ ] **Step 5: Update the initiative README**

List the execution order as `RWF-005`, `RWF-001`, `RWF-002`, `RWF-003`, `RWF-004`, then `RWF-006`. State that implementation tickets may close after local acceptance, while `RWF-006` and `RWF-000` remain `in_progress` through a clean implementation-head review. Task 13 then marks them `done` in the closeout metadata commit; review of that final head is a terminal guard that reopens acceptance-breaking state and repeats closeout when necessary.

- [ ] **Step 6: Verify ticket links and initial state**

Run:

```powershell
rg -n 'RWF-00[0-6]|2026-07-15-repository-organization-project-management' tickets/repo-workflow
```

Expected: all seven tracker/ticket IDs and the combined plan appear; only `RWF-000` is `in_progress` at this point.

- [ ] **Step 7: Commit the initiative expansion**

```powershell
git add docs/superpowers/plans/2026-07-15-repository-organization-project-management.md tickets/repo-workflow
git -c commit.gpgsign=false commit -m "tickets: expand repo workflow initiative"
```

Expected: one commit containing the approved implementation plan and repo-workflow ticket artifacts, so every ticket plan link resolves from the first execution commit.

---

### Task 2: Run RED baseline evaluations without the skill

**Files:**
- Modify: `docs/superpowers/plans/2026-07-15-repository-organization-project-management.md`
- Modify: `tickets/repo-workflow/RWF-005-anima-project-management-skill.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`
- Create after evaluations: `docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
- Use temporarily, then remove: `.tmp-eval-anima-project-management/**` (covered by the existing `.tmp-eval-*` ignore rule)

- [ ] **Step 1: Claim `RWF-005` before creating evaluation fixtures**

Set the child `Owner: Codex`, `Status: in_progress`, `Started:` if empty, and `Updated:` to one current MYT timestamp. Append a claim activity entry naming branch `codex/repo-organization-project-management` and worktree `.worktrees/repo-organization-project-management`. Synchronize the parent row, `Updated:`, and activity log in the same logical change without changing the parent owner.

- [ ] **Step 2: Create disposable fixture descriptions outside live ticket state**

Create ignored fixtures under `.tmp-eval-anima-project-management/baseline/` for these scenarios; fixtures contain synthetic PRDs, tickets, Git status, review-thread JSON, and command results only:

1. urgent new feature request that says to skip planning and code immediately;
2. `do the next ticket` with an unfinished dependency and a later ticket owned by another agent;
3. `mark this done` with failed/missing validation and unrelated dirty files;
4. diagnosis-only and isolated-edit requests that must not create project artifacts;
5. publish/monitor request whose old review targets a stale head and includes one behavioral defect plus one style-only suggestion.

- [ ] **Step 3: Run each baseline with a fresh subagent that cannot see the intended skill**

For every scenario, dispatch a fresh general-purpose subagent with only the fixture path and prompt. Require a proposed action log, file mutations, external actions, stopping condition, and rationale. Explicitly prohibit touching live repo files or services.

Expected RED evidence: at least one meaningful workflow failure or missing guarantee across the baseline set; do not invent a failure if a baseline behaves correctly.

- [ ] **Step 4: Record verbatim baseline behavior and expected corrections**

Create the evaluation document with a table containing `Scenario`, `Baseline action`, `Failure or gap`, `Required guardrail`, and `Forward result`. Mark forward results `not run yet`. Avoid names or secrets from external systems.

- [ ] **Step 5: Remove disposable baseline fixtures**

Run:

```powershell
$root = (Resolve-Path .).Path
$fixture = (Resolve-Path .tmp-eval-anima-project-management).Path
if (-not $fixture.StartsWith($root + [IO.Path]::DirectorySeparatorChar)) { throw "Fixture path escaped workspace" }
Remove-Item -Recurse -Force -LiteralPath $fixture
git status --short
```

Expected: only the intended plan, `RWF-005`, `RWF-000`, and evaluation evidence changes remain; no fixture, personal skill, production file, or external service changed.

- [ ] **Step 6: Commit RED evidence and claim bookkeeping**

```powershell
git add docs/superpowers/plans/2026-07-15-repository-organization-project-management.md tickets/repo-workflow/RWF-005-anima-project-management-skill.md tickets/repo-workflow/RWF-000-parent.md docs/audit/skills/2026-07-15-anima-project-management-evaluation.md
git -c commit.gpgsign=false commit -m "docs: record project management skill baseline"
```

---

### Task 3: Create the minimal repo-owned project-management skill

**Files:**
- Create: `.codex-skill-staging/anima-project-management/SKILL.md`
- Create: `.codex-skill-staging/anima-project-management/agents/openai.yaml`
- Modify: `tickets/repo-workflow/RWF-005-anima-project-management-skill.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`

- [ ] **Step 1: Confirm `RWF-005` is already claimed before creating the skill**

Verify the child remains `Owner: Codex` and `Status: in_progress`, its claim activity names the branch/worktree, and the parent row matches. Do not re-claim it or change the parent owner.

- [ ] **Step 2: Initialize the official skill scaffold**

Run:

```powershell
python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\init_skill.py anima-project-management --path .codex-skill-staging --interface display_name="Anima Project Management" --interface short_description="Manage animaOS project and ticket lifecycles" --interface default_prompt="Use `$anima-project-management to manage this animaOS initiative or ticket lifecycle."
```

Expected: `SKILL.md` and `agents/openai.yaml` exist below the exact repo-owned path; no personal skill is installed or changed.

- [ ] **Step 3: Replace scaffold placeholders with the minimal GREEN skill**

Keep YAML frontmatter to `name` and a trigger-focused `description`. The body must route among `status-only`, `planning`, `ticket execution`, and `publish/review` modes and encode these non-negotiable transitions:

```markdown
## Required sources

Read `AGENTS.md` and `docs/ops/prd-ticket-workflow.md` completely. Read
`tickets/TEMPLATE.md` before creating tickets, then read the relevant PRD, plan,
parent tracker, and child ticket before changing their state.

## Claim before implementation

Never overwrite another owner. Select only an unassigned backlog ticket whose
dependencies are done, then update the child and parent in one logical change:
`Owner: Codex`, `Status: in_progress`, `Started:` if empty, `Updated:` in MYT,
and matching activity entries and parent-table state.

## Complete with evidence

Do not mark work done until acceptance passes and validation and changed paths
are recorded. Synchronize the parent row and completed history. Failed checks
leave work in progress unless a concrete external blocker requires `blocked`.

## Publish only when authorized

Inspect the diff, validate, push, open or update a scoped draft PR, and use body
sections `Summary`, `Scope`, `Review focus`, `Out of scope`, and `Validation`.
Post the exact standalone comment `@codex review`. Query thread-aware
`reviewThreads(first: 100)` and compare the latest Codex review commit with
`headRefOid`. Fix actionable defects narrowly, disposition evidence-backed
nitpicks without code churn, push, re-request review, and repeat. Stop only when
the latest review targets the current head, required checks pass, and no
unresolved non-outdated actionable thread remains. Never merge without separate
authorization.
```

Also include the closeout rule, reopening rule for acceptance-breaking review findings, metadata-only follow-up PR rule if another actor merges early, quick-reference table, and red flags from the approved skill design. Link to canonical repo docs rather than duplicating their full contents.

- [ ] **Step 4: Confirm generated interface metadata**

`agents/openai.yaml` must contain only:

```yaml
interface:
  display_name: "Anima Project Management"
  short_description: "Manage animaOS project and ticket lifecycles"
  default_prompt: "Use $anima-project-management to manage this animaOS initiative or ticket lifecycle."
```

- [ ] **Step 5: Run official skill validation and concision checks**

Run:

```powershell
python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management
(Get-Content .codex-skill-staging/anima-project-management/SKILL.md | Measure-Object -Word).Words
rg -n 'TODO|placeholder|C:\\Users\\|\.agents/skills|\.codex/skills/' .codex-skill-staging/anima-project-management
```

Expected: official validation passes, word count is reviewed and reasonably concise, and the search returns no placeholders, machine-specific paths, or personal-install instructions.

- [ ] **Step 6: Commit the GREEN skill**

```powershell
git add .codex-skill-staging/anima-project-management tickets/repo-workflow/RWF-005-anima-project-management-skill.md tickets/repo-workflow/RWF-000-parent.md
git -c commit.gpgsign=false commit -m "workflow: add anima project management skill"
```

---

### Task 4: Integrate project management into repository guidance

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/ops/prd-ticket-workflow.md`
- Modify: `tickets/repo-workflow/RWF-005-anima-project-management-skill.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`

- [ ] **Step 1: Add the mandatory skill-routing section to `AGENTS.md`**

Direct agents to read `.codex-skill-staging/anima-project-management/SKILL.md` completely before initiative, PRD, plan, ticket selection/assignment/execution/closeout, or explicitly requested PR publication/review actions. State that the repo-owned workflow overrides stale personal `docs/prd/` or scratchboard-oriented skills.

- [ ] **Step 2: Add ownership and parent-sync rules to the workflow doc**

Document `Owner: unassigned` for new tickets, eligibility and dependency checks, the exact self-assignment mutation, owner-conflict behavior, parent row/activity synchronization, and acceptance/validation requirements for completion. Preserve the canonical four statuses and `YYYY-MM-DD HH:MM MYT` timestamp format.

- [ ] **Step 3: Add the scoped publication and review loop to the workflow doc**

Document explicit authorization, draft PR default, required PR body sections, exact `@codex review`, thread-aware `reviewThreads(first: 100)`, current `headRefOid`, actionable-vs-nitpick treatment, strict current-head stopping rule, reopen/closeout behavior, metadata-only follow-up after an early merge, monitor cleanup, and the prohibition on merging without separate authorization.

- [ ] **Step 4: Verify exact path and terminology**

Run:

```powershell
rg -n '\.codex-skill-staging/anima-project-management/SKILL\.md|@codex review|reviewThreads|headRefOid|Owner: unassigned' AGENTS.md docs/ops/prd-ticket-workflow.md
```

Expected: the exact committed skill path and review-state terms are present in repository guidance.

- [ ] **Step 5: Record validation without closing `RWF-005` yet**

Update its `Updated:`, activity log, validation commands, and changed paths. Keep it `in_progress` until forward evaluation passes in Task 9.

- [ ] **Step 6: Commit repository workflow integration**

```powershell
git add AGENTS.md docs/ops/prd-ticket-workflow.md tickets/repo-workflow/RWF-005-anima-project-management-skill.md tickets/repo-workflow/RWF-000-parent.md
git -c commit.gpgsign=false commit -m "docs: integrate project management workflow"
```

---

### Task 5: Normalize ticket state and rebuild the concise initiative index

**Files:**
- Modify: `tickets/repo-workflow/RWF-001-ticket-dashboard.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`
- Modify: `tickets/README.md`
- Modify: ticket files whose authoritative metadata uses `todo`, `in-review`, or `in_review`
- Modify: parent trackers whose child-status tables disagree with normalized child metadata

- [ ] **Step 1: Claim `RWF-001` and synchronize the parent**

Apply the standard self-assignment mutation to `RWF-001` and its matching parent row/activity entry.

- [ ] **Step 2: Inventory authoritative status values before mutation**

Run:

```powershell
rg -n '^- Status: |^\|.*\|[[:space:]]*`?(todo|in-review|in_review)`?[[:space:]]*\|' tickets
```

Expected: current legacy variants are visible in ticket headers and in both quoted and unquoted child-status table cells; activity log prose is not part of this anchored migration query.

- [ ] **Step 3: Normalize only authoritative current state**

Apply the approved mapping:

- `todo` becomes `backlog`;
- `in-review` and `in_review` become `done` when `Completed:` is non-empty, otherwise `in_progress`;
- a non-empty `Completed:` on any parent or child forces current metadata to `done`;
- synchronize every parent child-status cell from the corresponding child metadata.

Do not rewrite activity logs, historical narratives, completion timestamps, or ticket-folder locations. This vocabulary migration does not create a new activity entry for every mechanically normalized ticket.

- [ ] **Step 4: Rebuild `tickets/README.md` from normalized parent trackers**

List parents with `Status: done` under `Completed Initiatives`, every other canonical parent under `Active Initiatives`, and folders without a conforming parent under `Legacy or Unclassified`. Retain conventions, template/workflow links, and add `bun run check:repo` as the mechanical consistency command (noting it becomes available later in the same initiative). Link every classified initiative to its parent tracker. Do not infer parent completion from child state and do not add next-ticket, blocker, or completion-count reporting.

- [ ] **Step 5: Verify normalized state and index classification**

Run:

```powershell
rg -n '^- Status: (todo|in-review|in_review)\r?$|^\|.*\|[[:space:]]*`?(todo|in-review|in_review)`?[[:space:]]*\|' tickets
rg -n '^## (Active Initiatives|Completed Initiatives|Legacy or Unclassified)' tickets/README.md
```

Expected: first command has no matches; all three index sections exist.

- [ ] **Step 6: Complete `RWF-001` locally**

Record validation and changed paths, set `Status: done`, `Updated:` and `Completed:`, append a completion entry, set the parent row to `done`, and add `RWF-001` once to parent completed history. Keep `RWF-000` `in_progress`.

- [ ] **Step 7: Commit normalized ticket state**

```powershell
git add tickets
git -c commit.gpgsign=false commit -m "tickets: normalize initiative state"
```

---

### Task 6: Mark scratchboard as a legacy workflow area

**Files:**
- Create: `scratchboard/README.md`
- Modify: `tickets/repo-workflow/RWF-002-scratchboard-legacy.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`

- [ ] **Step 1: Confirm `RWF-001` is done, then claim `RWF-002`**

Run:

```powershell
rg -n '^- Status: done\r?$' tickets/repo-workflow/RWF-001-ticket-dashboard.md
```

Expected: one match. Then perform standard child/parent claim bookkeeping for `RWF-002`.

- [ ] **Step 2: Inventory legacy workstreams without changing them**

Inspect `scratchboard/_system/active-tasks.md`, `scratchboard/v1-encrypted-core/`, and `scratchboard/v2-memory-recall-reliability/`. In the README, list `v2-memory-recall-reliability` as the workstream currently named by the legacy active-task index, and list `v1-encrypted-core` as a migration candidate that still contains unresolved-looking legacy planning sections requiring human confirmation. Describe `_system` as legacy coordination metadata, not a product initiative. Do not infer ticket completion or mutate any of these files.

- [ ] **Step 3: Add the legacy README and migration checklist without touching historical files**

State that `scratchboard/` is retained only for workstreams already tied to it and that new work follows `PRD -> design/spec when needed -> dated plan -> parent and child tickets`. Link to `docs/ops/prd-ticket-workflow.md`, `docs/prds/`, `docs/superpowers/plans/`, and `tickets/`.

Include this incremental migration checklist:

1. read the whole legacy workstream and its inbound links before changing state;
2. find or create the canonical PRD/spec and dated implementation plan;
3. create one parent tracker plus claimable child tickets with dependencies and acceptance;
4. cross-link the new parent to the legacy folder and the legacy README to the new parent;
5. copy only current scope/state into tickets while preserving historical scratchboard files unchanged;
6. stop recording new progress in scratchboard after the ticket cutover;
7. validate links and parent/child state before declaring migration complete.

- [ ] **Step 4: Verify the marker, checklist, inventory, and preserved history**

Run:

```powershell
rg -n 'legacy|PRD|tickets|v1-encrypted-core|v2-memory-recall-reliability|Migration checklist' scratchboard/README.md
git diff --name-status -- scratchboard
```

Expected: the README clearly says legacy, names both candidate workstreams, and contains the incremental checklist; no existing scratchboard file is modified or removed.

- [ ] **Step 5: Complete `RWF-002` and commit**

Record validation and changed paths, close the child, synchronize parent row/history, then run:

```powershell
git add scratchboard/README.md tickets/repo-workflow/RWF-002-scratchboard-legacy.md tickets/repo-workflow/RWF-000-parent.md
git -c commit.gpgsign=false commit -m "docs: mark scratchboard as legacy"
```

---

### Task 7: Build the read-only organization validator with TDD

**Files:**
- Create: `tests/repo-organization.test.ts`
- Create: `scripts/check-repo-organization.ts`
- Modify: `package.json`
- Modify: `tickets/repo-workflow/RWF-003-ticket-metadata-validation.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`

- [ ] **Step 1: Confirm `RWF-001` is done, then claim `RWF-003`**

Perform the standard dependency check and self-assignment mutation.

- [ ] **Step 2: Write parser tests first**

Export pure helpers from the future script and add tests covering canonical header statuses, rejection of `todo`/`in-review`/`in_review`/unknown values, parent child-status cells, and ignoring matching words inside Activity Log prose. Use temporary directories created by Bun tests and remove them in `afterEach`.

Core test shape:

```ts
import { describe, expect, test } from "bun:test";
import {
  collectOrganizationViolations,
  parseTicketDocument,
  renderReport,
} from "../scripts/check-repo-organization";

test("ignores historical status words outside authoritative fields", () => {
  const parsed = parseTicketDocument("ticket.md", `
# ABC-001
- Status: done
- Completed: 2026-07-15 10:00 MYT
## Activity Log
- Moved from in-review to done.
  `);

  expect(parsed.status).toBe("done");
  expect(parsed.parentRows).toEqual([]);
});

test("parses CRLF ticket metadata and unquoted child status cells", () => {
  const parsed = parseTicketDocument(
    "PDP-000.md",
    "# PDP-000\r\n- Status: in_progress\r\n\r\n## Child Tickets\r\n" +
      "| Ticket | Title | Status | Depends on |\r\n" +
      "| --- | --- | --- | --- |\r\n" +
      "| `PDP-001` | First child | done | none |\r\n",
  );

  expect(parsed.status).toBe("in_progress");
  expect(parsed.parentRows).toEqual([{ ticketId: "PDP-001", status: "done" }]);
});
```

- [ ] **Step 3: Write repository-check tests before implementation**

Add focused failing cases for:

- non-`done` ticket with non-empty `Completed:`;
- child metadata disagreeing with its parent table row;
- parent row naming a missing child ticket;
- direct app/package directory without a recognized manifest;
- existing `docs/audits/` or missing `docs/audit/`;
- tracked root `debug.log` via an injected tracked-file set;
- missing or non-legacy `scratchboard/README.md`;
- aggregation and grouping of multiple failures;
- clean success report and exit behavior;
- unexpected filesystem/Git error rendered distinctly from violations.

- [ ] **Step 4: Run focused tests to verify RED**

Run:

```powershell
bun test tests/repo-organization.test.ts
```

Expected: FAIL because the validator module or its exports do not exist.

- [ ] **Step 5: Implement pure parsing and aggregation**

Use focused types and dependency injection so tests never shell out:

```ts
export const CANONICAL_STATUSES = new Set([
  "backlog",
  "in_progress",
  "blocked",
  "done",
]);

export type OrganizationViolation = {
  check: string;
  path: string;
  message: string;
};

export type RepositorySnapshot = {
  root: string;
  trackedFiles: ReadonlySet<string>;
};

export function collectOrganizationViolations(
  snapshot: RepositorySnapshot,
): OrganizationViolation[] {
  // Call focused ticket, manifest, docs, Git, and scratchboard checks and
  // return every violation in deterministic check/path order.
}
```

Normalize CRLF to LF (or trim trailing `\r` from parsed lines) before field comparison. Parse only top metadata lines before the first `##` section. Recognize authoritative Markdown tables under both `## Child Ticket Order` and `## Child Tickets`; derive the `Ticket` and `Status` column indexes from the header row rather than fixed positions, and accept optional inline-code backticks around ticket IDs and status cells. Match child IDs to ticket filenames by ID prefix, compare row state to child metadata, and report missing/ambiguous children actionably. Add test fixtures for LF and CRLF, both heading forms, and both quoted and unquoted statuses so `PDP-000`-style rows cannot escape validation.

- [ ] **Step 6: Implement the CLI boundary and grouped report**

At the CLI boundary, resolve repo root, obtain tracked files with `git ls-files --full-name`, catch unexpected errors separately, render all grouped violations, and set exit code 1 for violations and a distinct nonzero code for unexpected failures. Guard execution so importing the module from tests has no side effect.

- [ ] **Step 7: Add the root command**

Add exactly:

```json
"check:repo": "bun run scripts/check-repo-organization.ts"
```

Do not change existing `build`, `lint`, or `test` scripts.

- [ ] **Step 8: Run tests to verify GREEN**

Run:

```powershell
bun test tests/repo-organization.test.ts
```

Expected: all focused tests pass. `bun run check:repo` may still fail at this stage only on real documentation/hygiene items scheduled in Task 8; its output must aggregate them.

- [ ] **Step 9: Commit validator implementation without falsely closing the ticket**

Record focused test evidence and current repository-check violations in `RWF-003`, but keep it `in_progress` until the live tree is clean in Task 8.

```powershell
git add tests/repo-organization.test.ts scripts/check-repo-organization.ts package.json tickets/repo-workflow/RWF-003-ticket-metadata-validation.md tickets/repo-workflow/RWF-000-parent.md
git -c commit.gpgsign=false commit -m "tooling: add repository organization check"
```

---

### Task 8: Reconcile repository documentation and hygiene

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `.gitignore`
- Modify: `docs/architecture/system/directory-structure.md`
- Move: `docs/audits/2026-06-11-agent-server-audit.md` to `docs/audit/2026-06-11-agent-server-audit.md`
- Modify: `docs/superpowers/plans/2026-07-14-dual-session-scope-a6.md`
- Modify: `tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md`
- Modify if it contains a live old-path reference: `docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md`
- Untrack: `debug.log`
- Modify: `tickets/repo-workflow/RWF-004-repository-documentation-hygiene.md`
- Modify: `tickets/repo-workflow/RWF-003-ticket-metadata-validation.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`

- [ ] **Step 1: Confirm `RWF-002` is done, then claim `RWF-004`**

Verify the dependency is complete, then perform standard child/parent claim bookkeeping before documentation or Git-index changes.

- [ ] **Step 2: Rewrite the canonical directory map from live manifests**

Document all six applications and eight shared packages, plus `docs/`, `scripts/`, `tests/`, `third_party/`, `tickets/`, and legacy `scratchboard/`. Explain Bun workspaces/package manifests, Nx orchestration, uv, and Cargo as complementary authorities. Describe runtime-generated/machine-local paths without volatile test, route, table, or tool counts. Remove the stale `apps/api` description.

- [ ] **Step 3: Update entry-point navigation**

Expand the `AGENTS.md` project-structure summary to include `local-runtime-daemon`, `site`, and the shared packages. Add a concise root `README.md` link to `docs/architecture/system/directory-structure.md`.

- [ ] **Step 4: Consolidate the audit directory and repair references**

Use Git to move the audit file, then update tracked links/commands to the singular path. Preserve historical content.

```powershell
git mv docs/audits/2026-06-11-agent-server-audit.md docs/audit/2026-06-11-agent-server-audit.md
rg -n 'docs/audits/2026-06-11-agent-server-audit\.md' docs/superpowers/plans/2026-07-14-dual-session-scope-a6.md tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md
```

Expected after edits: no output from the targeted live-link search. A repository-wide search may still find intentional historical descriptions in the approved design/current implementation plan and the validator/test literals that reject the deprecated directory; those are not navigational links to the moved file.

- [ ] **Step 5: Ignore and untrack root debug output**

Add `/debug.log` to `.gitignore` and run:

```powershell
git rm --cached -- debug.log
git check-ignore -v debug.log
```

Expected: `debug.log` is scheduled for removal from tracking and the root ignore rule matches; do not delete or alter any unrelated local runtime data.

- [ ] **Step 6: Run the live validator**

Run:

```powershell
bun run check:repo
```

Expected: PASS with a short clean summary. Fix only organization-scope violations exposed by the validator.

- [ ] **Step 7: Complete `RWF-004` and `RWF-003`**

Record validation and changed paths for both. Close `RWF-004`, then close `RWF-003` now that its focused tests and live clean run both pass. Synchronize both parent rows, completed history, timestamps, and activity log entries.

- [ ] **Step 8: Confirm the source-hotspot exclusion before commit**

Run:

```powershell
git diff HEAD --name-only | rg '^(apps|packages)/.*/src/|^apps/desktop/src-tauri/'
```

Expected: no output.

- [ ] **Step 9: Commit documentation and hygiene cleanup**

```powershell
git add -- .gitignore AGENTS.md README.md `
  docs/architecture/system/directory-structure.md `
  docs/audit/2026-06-11-agent-server-audit.md `
  docs/superpowers/plans/2026-07-14-dual-session-scope-a6.md `
  docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md `
  tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md `
  tickets/repo-workflow/RWF-000-parent.md `
  tickets/repo-workflow/RWF-003-ticket-metadata-validation.md `
  tickets/repo-workflow/RWF-004-repository-documentation-hygiene.md
git status --short
git -c commit.gpgsign=false commit -m "docs: align repository organization"
```

Expected: the explicit list stages only the documentation/hygiene paths named by this task. The earlier `git rm --cached -- debug.log` already staged the tracked removal, so `debug.log` is not passed to `git add`; inspect `git status --short` for its staged deletion before committing even if the ignored working-tree file remains locally.

---

### Task 9: Run forward skill evaluations and close the skill ticket

**Files:**
- Modify only if evidence requires a guardrail: `.codex-skill-staging/anima-project-management/SKILL.md`
- Modify: `docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
- Modify: `tickets/repo-workflow/RWF-005-anima-project-management-skill.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`
- Use temporarily, then remove: `.tmp-eval-anima-project-management/**`

- [ ] **Step 1: Recreate equivalent disposable fixtures**

Use the five RED scenario contracts from Task 2, with new synthetic IDs and fresh state. Do not reuse a subagent or expose baseline rationalizations.

- [ ] **Step 2: Run each scenario with a fresh subagent explicitly using the repo-owned skill**

Require the subagent to read `.codex-skill-staging/anima-project-management/SKILL.md`, its required repo sources, and only the isolated fixture. The expected forward behavior is:

1. preserve PRD/design/plan/ticket approval gates under urgency;
2. refuse the ineligible dependency and owned ticket, reporting why no next ticket is claimable;
3. keep the ticket open, preserve unrelated dirt, and report missing validation;
4. remain read-only for diagnosis and avoid fake project artifacts for an isolated edit;
5. recognize the stale review, fix only the real defect with regression evidence, disposition the style nitpick, post `@codex review` after the new push, and wait for a clean current-head review.

- [ ] **Step 3: Refactor only for observed loopholes**

If a forward run violates the contract, add the smallest explicit guardrail to `SKILL.md`, rerun that scenario with another fresh subagent, and record the iteration. Do not add instructions merely because they might be useful someday.

- [ ] **Step 4: Remove fixtures and update evaluation evidence**

Populate every `Forward result`, record pass/fail and any skill refactor, then resolve and verify the fixture path remains under the worktree before recursively removing `.tmp-eval-anima-project-management`, as in Task 2. Confirm no live ticket, personal skill, production file, or external service was mutated by evaluation.

- [ ] **Step 5: Re-run official skill validation**

Run:

```powershell
python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management
(Get-Content .codex-skill-staging/anima-project-management/SKILL.md | Measure-Object -Word).Words
rg -n '\.codex-skill-staging/anima-project-management/SKILL\.md' AGENTS.md
```

Expected: validation passes, word count is reviewed as concise, and `AGENTS.md` points to the exact repo path.

- [ ] **Step 6: Complete `RWF-005` locally and commit**

Record baseline/forward evidence, official validation, changed paths, and residual notes. Close the child, synchronize the parent row/history, and keep the parent `in_progress` for `RWF-006`.

```powershell
git add .codex-skill-staging/anima-project-management docs/audit/skills/2026-07-15-anima-project-management-evaluation.md tickets/repo-workflow/RWF-005-anima-project-management-skill.md tickets/repo-workflow/RWF-000-parent.md
git -c commit.gpgsign=false commit -m "test: validate project management skill"
```

---

### Task 10: Claim integration and run final local verification

**Files:**
- Modify: `tickets/repo-workflow/RWF-006-integration-pr-review.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`

- [ ] **Step 1: Verify every implementation dependency is done**

Run:

```powershell
rg -n '^- Status: ' tickets/repo-workflow -g 'RWF-00[1-5]-*.md'
```

Expected: `RWF-001` through `RWF-005` each report `Status: done`.

- [ ] **Step 2: Claim `RWF-006` and synchronize the parent**

Perform standard claim bookkeeping. Keep `RWF-006` and `RWF-000` `in_progress` until the implementation head satisfies the full current-head review stopping rule. Task 13 then marks them `done` in the closeout metadata commit before the terminal review guard runs on that final head.

- [ ] **Step 3: Run focused organization and skill checks**

Run:

```powershell
bun test tests/repo-organization.test.ts
bun run check:repo
python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management
```

Expected: all pass.

- [ ] **Step 4: Verify workspace discovery and the required build**

Run:

```powershell
bunx nx show projects
bun run build
```

Expected: Nx reports its configured projects; server/desktop build and `cargo check -p animus` pass. Existing Vite chunk-size warnings are non-blocking unless they become errors.

- [ ] **Step 5: Inspect exact scope and working-tree cleanliness**

Run:

```powershell
git diff --check origin/main...HEAD
git status --short
git diff --name-only origin/main...HEAD
git diff --name-only origin/main...HEAD | rg '^(apps|packages)/.*/src/|^apps/desktop/src-tauri/'
git diff --name-only origin/main...HEAD | rg '^\.codex-skill-staging/'
```

Expected: no whitespace errors; only planned repo-owned paths changed; the source-hotspot search returns no output; the final command lists only the intended repo-owned staged skill path and cannot include personal skill directories outside the repository.

- [ ] **Step 6: Record validation in `RWF-006`, but do not close it**

Add commands, results, changed-path categories, and any non-blocking warnings. Update the parent activity log. Keep both statuses `in_progress`.

- [ ] **Step 7: Commit integration evidence**

```powershell
git add tickets/repo-workflow/RWF-006-integration-pr-review.md tickets/repo-workflow/RWF-000-parent.md
git -c commit.gpgsign=false commit -m "tickets: record repository integration validation"
```

---

### Task 11: Publish a focused draft PR and request Codex review

**Files:**
- No expected source mutation; GitHub PR metadata and comments only.
- Modify tickets only if publication evidence or a real review finding changes project state.

- [ ] **Step 1: Reconfirm branch, base, commits, and GitHub authentication**

Run:

```powershell
git branch --show-current
git merge-base HEAD origin/main
git log --oneline origin/main..HEAD
gh auth status
```

Expected: branch is `codex/repo-organization-project-management`, the base relationship is understood, commits are scoped, and GitHub authentication succeeds.

- [ ] **Step 2: Push with upstream tracking**

Run:

```powershell
git push -u origin codex/repo-organization-project-management
```

Expected: push succeeds and the upstream is set.

- [ ] **Step 3: Open a draft PR with the required review contract**

Create the PR body with exactly these sections:

```markdown
## Summary
- Align repository maps, audit paths, ticket state, and legacy-workflow guidance.
- Add a read-only organization validator and repo-owned project-management skill.

## Scope
- Repository metadata, documentation, workflow artifacts, validation, and tests only.

## Review focus
- Actionable correctness, security, regressions, workflow contracts, Git/index behavior, and missing tests.
- Please de-prioritize style-only preferences, speculative redesign, unrelated hotspot refactors, and suggestions already enforced by repository tooling.

## Out of scope
- Production source/hotspot refactors, API/runtime behavior, migrations, and personal skill installation.

## Validation
- `bun test tests/repo-organization.test.ts`
- `bun run check:repo`
- official skill `quick_validate.py`
- `bunx nx show projects`
- `bun run build`
```

Run `gh pr create --draft --base main --head codex/repo-organization-project-management --title "workflow: organize repository project management" --body-file <temporary-body-path>`, then remove the temporary body file.

Expected: draft PR URL returned.

- [ ] **Step 4: Post the exact review request**

Run:

```powershell
gh pr comment <PR_NUMBER> --body '@codex review'
```

Expected: the comment contains only `@codex review`.

- [ ] **Step 5: Cache PR identity and current head**

Run:

```powershell
gh pr view <PR_NUMBER> --json number,url,headRefName,headRefOid,baseRefName,isDraft,merged
```

Expected: PR identity, current head OID, draft state, and merge state are recorded. If the installed `gh` JSON view lacks `merged`, obtain it through GraphQL; do not substitute `isMerged`.

---

### Task 12: Self-loop on thread-aware Codex review until the current head is clean

**Files:**
- Modify only files required by actionable in-scope review findings.
- Modify: `tickets/repo-workflow/RWF-006-integration-pr-review.md`
- Modify: relevant child and `tickets/repo-workflow/RWF-000-parent.md` only when a finding invalidates completed acceptance.

- [ ] **Step 1: Query review threads and current-head review state**

Use `gh api graphql` with variables for owner, repository, and PR number. Query:

```graphql
pullRequest(number: $number) {
  merged
  headRefOid
  reviews(last: 50) {
    nodes { author { login } commit { oid } state submittedAt body }
  }
  reviewThreads(first: 100) {
    nodes {
      isResolved
      isOutdated
      path
      line
      originalLine
      comments(first: 50) {
        nodes { id databaseId author { login } body createdAt url commit { oid } }
      }
    }
  }
}
```

Expected: thread-level state and latest Codex review commit are available; flat PR comments are supplemental only.

- [ ] **Step 2: Apply the strict stopping rule**

Do not declare clean unless all are true:

- latest Codex review commit equals current `headRefOid`;
- required checks for the changed surface pass;
- zero unresolved, non-outdated actionable threads remain;
- every non-actionable thread has a concise evidence-based disposition.

If the latest review is older than the head, continue monitoring even when no thread is visible.

- [ ] **Step 3: Classify and address feedback without nitpick churn**

Treat correctness, security, behavior, compatibility, contracts, tests, and in-scope documentation defects as actionable. Treat duplicate, already-fixed/outdated, style-only, speculative redesign, unrelated expansion, or claims contradicted by current code/tests as non-actionable. Never dismiss a valid defect as a nitpick.

For each behavioral defect: reopen the acceptance-owning child if necessary, preserve prior completion timestamps in activity logs, add a failing regression test first, implement the narrow fix, run focused and broad checks, and update ticket/parent state honestly. For a non-actionable observation, reply once with evidence and resolve only after the disposition is sound.

- [ ] **Step 4: Commit, push, resolve addressed threads, and re-request review**

Run scoped tests, inspect/stage only intended changes, commit with `git -c commit.gpgsign=false`, push, resolve materially addressed threads through GraphQL, and post the exact standalone `@codex review` again. Then return to Step 1 for the new head.

- [ ] **Step 5: Handle asynchronous or early-merge cases**

If a recurring monitor is created, remove it when the PR closes or reaches the stopping rule. If another actor merges before ticket closeout, create a metadata-only follow-up branch and draft PR for child/parent closeout, apply the same review-focus contract and current-head loop, and do not merge it. If permissions prevent this, leave tickets open and record/report the blocker.

---

### Task 13: Close project metadata and review the final head

**Files:**
- Modify: `tickets/repo-workflow/RWF-006-integration-pr-review.md`
- Modify: `tickets/repo-workflow/RWF-000-parent.md`
- Modify: `tickets/README.md`

- [ ] **Step 1: Close `RWF-006` only after the implementation head is clean**

Record the PR URL, clean implementation-head OID, successful validations, zero actionable-thread result, and review notes. Set `RWF-006` to `done`, set timestamps, update parent row/history, and close `RWF-000` only because all six required children are done and initiative-level validation passed. This metadata transition occurs after the implementation head is clean and before review of the new closeout head.

- [ ] **Step 2: Rebuild the initiative index for final parent state**

Move the repo-workflow initiative from active to completed in `tickets/README.md` based on `RWF-000` metadata. Do not infer other parent statuses.

- [ ] **Step 3: Re-run metadata-sensitive checks**

Run:

```powershell
bun test tests/repo-organization.test.ts
bun run check:repo
git diff --check
```

Expected: all pass on the closeout changes.

- [ ] **Step 4: Commit and push the closeout head**

```powershell
git add tickets/repo-workflow/RWF-006-integration-pr-review.md tickets/repo-workflow/RWF-000-parent.md tickets/README.md
git -c commit.gpgsign=false commit -m "tickets: close repo workflow initiative"
git push
gh pr comment <PR_NUMBER> --body '@codex review'
```

Expected: the final project-metadata commit is pushed and a fresh exact review request is posted.

- [ ] **Step 5: Repeat Task 12 for the closeout head**

Treat this review as the terminal guard: stop only when Codex's latest review targets this final `headRefOid`, required checks still pass, and no unresolved non-outdated actionable feedback remains. If actionable feedback invalidates acceptance, reopen the acceptance-owning child when applicable, `RWF-006`, and `RWF-000` consistently; preserve earlier completion timestamps in activity logs, fix and validate narrowly, close again, push, post the exact `@codex review`, and repeat the guard. Do not merge without separate authorization.

- [ ] **Step 6: Report the achieved terminal state without merging**

Report branch, PR URL, final reviewed head OID, checks run, and zero actionable-thread status. Leave the draft PR unmerged unless the user separately authorizes merge.
