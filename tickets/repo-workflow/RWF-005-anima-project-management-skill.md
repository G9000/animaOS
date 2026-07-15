# RWF-005 - Add the anima project-management skill

- Status: done
- Priority: P2
- Scope: `.codex-skill-staging/anima-project-management`, `AGENTS.md`, `docs/ops`, `docs/audit/skills`
- Parent: `RWF-000`
- Depends on: none
- Owner: Codex
- PRD: none
- Spec: docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-07-15 17:11 MYT
- Updated: 2026-07-15 23:15 MYT
- Started: 2026-07-15 17:39 MYT
- Completed: 2026-07-15 23:15 MYT

## Goal

Add a concise repo-owned animaOS project-management skill whose behavior is integrated into repository guidance and supported by RED-GREEN-REFACTOR evidence.

## Deliverables

- Create `.codex-skill-staging/anima-project-management/SKILL.md` and generated `agents/openai.yaml`
- Integrate the repo-owned skill path and lifecycle rules into `AGENTS.md` and `docs/ops/prd-ticket-workflow.md`
- Record isolated baseline and forward evaluation evidence for the approved workflow scenarios
- Keep personal skill directories and live project state outside evaluation scope

## Acceptance

- The official skill validator passes and generated interface metadata contains only the approved fields
- The skill routes status, planning, ticket execution, and authorized publish/review modes while enforcing ownership, dependency, validation, parent-sync, and no-merge rules
- `AGENTS.md` points to the exact repo-owned skill path and the workflow document defines the matching ticket and review lifecycle
- RED evidence retains the exact synthetic fixture/preconditions, evaluator prompt/response contract, and complete structured output for all five fresh isolated baseline agents
- Baseline evidence reports only actually observed failures or gaps; zero observed gaps is valid and must not be fabricated into a failure
- GREEN/REFACTOR evidence retains complete forward outputs and compares every scenario against its approved behavior contract with fresh isolated agents
- Evaluation fixtures are removed, no personal skill directory is changed, and the final skill remains concise and free of placeholders or machine-specific paths

## Activity Log

- 2026-07-15 17:11 MYT - Ticket created from the approved anima project-management skill design and implementation plan.
- 2026-07-15 17:39 MYT - Codex claimed `RWF-005` on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management` before RED baseline evaluation.
- 2026-07-15 17:49 MYT - Recorded five fresh-agent RED baseline scenarios; no workflow failure or missing guarantee was observed, so no failure was invented. Forward evaluation remains pending.
- 2026-07-15 18:01 MYT - Expanded RED evidence with exact reproducible prompts, synthetic preconditions, and complete evaluator outputs; aligned acceptance and forward comparison with an honest zero-gap baseline.
- 2026-07-15 18:11 MYT - Created the minimal GREEN skill from the official repo-owned scaffold and validated its trigger contract, lifecycle router, generated interface metadata, and concision; status remains `in_progress` pending repository integration and forward evaluation.
- 2026-07-15 18:31 MYT - Hardened named-ticket legal transitions, permission-blocked metadata closeout, pushed-OID review synchronization, and the untracked-edit closeout boundary; aligned the approved design and kept the ticket `in_progress`.
- 2026-07-15 18:37 MYT - Added atomic first-discovery and clearance bookkeeping for child blockers and synchronized parent state; kept the child and parent row `in_progress` pending integration and forward evaluation.
- 2026-07-15 18:45 MYT - Added assigned-backlog start semantics, state-first `done` precedence, and rejection of malformed transition combinations; kept project state `in_progress`.
- 2026-07-15 18:53 MYT - Integrated mandatory skill routing, canonical ticket transitions and parent synchronization, and the explicitly authorized current-head PR review loop into repository guidance; kept `RWF-005` and its parent row `in_progress` pending forward evaluation.
- 2026-07-15 18:59 MYT - Closed Task 4 review gaps by making initiative closeout an explicit skill trigger, guarding the first review request on pushed-OID/head synchronization, and replacing placeholder parent validation; kept `RWF-005` and its parent row `in_progress` pending forward evaluation.
- 2026-07-15 19:13 MYT - Refactored the skill into a canonical-doc-backed high-risk checklist and aligned action-scoped authority, fail-closed pagination, ownership-safe reopen, parent closeout timestamps, early-merge authority handling, and template validation; kept `RWF-005` and its parent row `in_progress` pending forward evaluation.
- 2026-07-15 22:07 MYT - Resumed the existing Codex-owned `in_progress` ticket without a new claim, preserved `Started: 2026-07-15 17:39 MYT`, and began the five fresh-agent forward evaluations; kept the parent row `in_progress`.
- 2026-07-15 22:17 MYT - Completed `RWF-005` after five distinct fresh-agent forward contracts passed on iteration 1, no skill loophole required a refactor, disposable fixtures were safely removed, and focused repository and official skill validation passed; synchronized the parent row and completion history while leaving the parent `in_progress` for `RWF-006`.
- 2026-07-15 22:38 MYT - Reopened `RWF-005` for acceptance-breaking evaluation-methodology findings: the prior forward prompts were leading and machine-specific. Preserved the prior completion timestamp `2026-07-15 22:17 MYT` in history, cleared the current `Completed:`, retained `Owner: Codex` and `Started: 2026-07-15 17:39 MYT`, and began a portable neutral v2 evaluation suite.
- 2026-07-15 22:56 MYT - Re-completed `RWF-005` after portable neutral scenarios 1-4, multi-page 5A, and cursor-failure 5B passed with six distinct fresh agents; exact replay manifests, privacy normalization, fixture cleanup, official skill validation, 32 focused tests, and the live repository check passed. No skill loophole required a refactor; synchronized the parent row/history and kept the parent `in_progress` for `RWF-006`.
- 2026-07-15 23:13 MYT - Reopened `RWF-005` for the acceptance-breaking closeout-scope finding: the current evidence used working-tree-only commands that became vacuous after commit. Preserved the prior completion timestamp `2026-07-15 22:56 MYT` in activity, cleared the current `Completed:`, retained `Owner: Codex` and `Started: 2026-07-15 17:39 MYT`, and began committed-range validation from `681dd11dc399faa8a593ef9e73dcb4796b91d5ad`.
- 2026-07-15 23:15 MYT - Re-completed `RWF-005` after replacing working-tree-only closeout evidence with reproducible committed-range commands from `681dd11dc399faa8a593ef9e73dcb4796b91d5ad`; the exact three-path assertion, empty production/skill filters, 32 focused tests, live repository check, and two-ticket follow-up diff all passed. Synchronized one current parent completion entry and kept the parent `in_progress` for `RWF-006`.

## Validation

### Historical snapshots (superseded by current closeout validation)

The commands, path sets, and counts below are preserved as evidence from RED, GREEN, Task 4 integration, and the pre-thinning quality follow-ups. They are historical snapshots, not the current reproducible closeout checklist. In particular, skill-layout grep assertions and the wider changed-path inventories predate the final 694-word checklist and Task 9's exact three-file scope. Historical `git diff --check` and `git diff --name-only HEAD` commands describe then-current working-tree state only and are explicitly superseded by the committed-range validation below.

- Commands (historical):
  - `rg -n '^- (Status: in_progress|Owner: Codex)\r?$' tickets/repo-workflow/RWF-005-anima-project-management-skill.md`
  - ``rg -n '^\| `RWF-005` \| Add the anima project-management skill \| `in_progress` \| none \|\r?$' tickets/repo-workflow/RWF-000-parent.md``
  - `rg -n '^## Scenario [1-5]: .+\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^# Synthetic Scenario [1-5]: .+\r?$|^## (Constraints|Response contract)\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^### Exact evaluator prompt, synthetic fixture, and response contract\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^### Complete evaluator output\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^#{1,2} (Proposed action log|File/state mutations|External actions|Stopping condition|Rationale)\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^- Forward result: not run yet\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management` (historical local root normalized for portability)
  - `(Get-Content .codex-skill-staging/anima-project-management/SKILL.md | Measure-Object -Word).Words`
  - `rg -n 'TODO|placeholder|C:\\Users\\|\.agents/skills|\.codex/skills/' .codex-skill-staging/anima-project-management`
  - `Get-ChildItem -Recurse -File .codex-skill-staging/anima-project-management`
  - `Get-Content .codex-skill-staging/anima-project-management/agents/openai.yaml`
  - `rg -n 'Any .*done.*takes precedence|Backlog and unassigned|Codex-owned backlog|Codex-owned .*in_progress|Codex-owned .*blocked|Another owner, non-|Unlisted or malformed' .codex-skill-staging/anima-project-management/SKILL.md`
  - `rg -n 'Before any backlog start.*verify dependencies and visible claims.*unassigned.*Owner: Codex.*Codex-owned.*preserve .*Owner: Codex.*log a start.*not a new ownership claim.*Status: in_progress.*Started:.*Updated:.*parent row/status.*Updated:.*activity' .codex-skill-staging/anima-project-management/SKILL.md`
  - `rg -n 'pushed OID|headRefOid.*equals that OID|integration child.*blocked|untracked isolated edit' .codex-skill-staging/anima-project-management/SKILL.md docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md`
  - `rg -n 'first concrete blocker discovery.*Status: blocked.*parent child row.*blocked.*parent.*Updated:.*parent activity.*Status: blocked.*no other initiative progress.*When cleared.*parent row.*in_progress.*restore parent.*in_progress' .codex-skill-staging/anima-project-management/SKILL.md`
  - `rg -n '\.codex-skill-staging/anima-project-management/SKILL\.md|@codex review|reviewThreads|headRefOid|Owner: unassigned|Project Management Skill' AGENTS.md docs/ops/prd-ticket-workflow.md`
  - `rg -n -i 'in-review|in_review' AGENTS.md docs/ops/prd-ticket-workflow.md`
  - `rg -n -i 'auto[ -]?merge|\.agents/skills|\.codex/skills|docs/prd/|scratchboard/' AGENTS.md docs/ops/prd-ticket-workflow.md`
  - `rg -n 'Action-Scoped External Authority|Local implementation or commit|Request Codex review|Address feedback|Monitor until clean|Merge' AGENTS.md docs/ops/prd-ticket-workflow.md .codex-skill-staging/anima-project-management/SKILL.md docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md`
  - `rg -n 'pageInfo|hasNextPage|endCursor|fail closed|pagination' docs/ops/prd-ticket-workflow.md .codex-skill-staging/anima-project-management/SKILL.md docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md docs/superpowers/plans/2026-07-15-repository-organization-project-management.md`
  - `rg -n 'owner gate|non-Codex|reassignment|parent .*Updated.*Completed|parent .*Completed' docs/ops/prd-ticket-workflow.md .codex-skill-staging/anima-project-management/SKILL.md docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md docs/superpowers/plans/2026-07-15-repository-organization-project-management.md`
  - `rg -n '^- (PRD|Spec|Plan): none\r?$' tickets/TEMPLATE.md`
  - `git diff --check`
  - `git diff --cached --check`
  - `git diff --name-only HEAD`
  - `git status --short --untracked-files=all`
- Changed paths (historical wider scope):
  - docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
  - docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md
  - docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md
  - docs/audit/skills/2026-07-15-anima-project-management-evaluation.md
  - .codex-skill-staging/anima-project-management/SKILL.md
  - .codex-skill-staging/anima-project-management/agents/openai.yaml
  - AGENTS.md
  - docs/ops/prd-ticket-workflow.md
  - tickets/TEMPLATE.md
  - tickets/repo-workflow/RWF-003-ticket-metadata-validation.md
  - tickets/repo-workflow/RWF-005-anima-project-management-skill.md
  - tickets/repo-workflow/RWF-000-parent.md
- Notes (historical):
  - RED fixtures removed; no `.tmp-eval-*` path remains
  - owner/status search returned 2 matches; synchronized parent-row search returned 1 match
  - scenario, exact-prompt, complete-output, and forward-result searches each returned 5 matches; fixture/constraint/contract search returned 15 matches
  - structured-output search returned 25 headings: all 5 required fields for each of 5 evaluator outputs
  - both RED-evidence diff checks exited 0; that earlier working scope contained exactly its 3 follow-up paths
  - all five forward results are `not run yet`
  - official skill validator exited 0 with `Skill is valid!`; hardened `SKILL.md` is 986 words
  - forbidden-pattern search returned no matches (expected `rg` exit 1)
  - skill folder contains exactly `SKILL.md` and `agents/openai.yaml`; frontmatter contains only `name` and `description`, and generated interface YAML matches the approved three fields exactly
  - legal-transition search returned 7 matches; assigned-backlog start assertion returned 1 matching contract line; OID/blocker/untracked-boundary search returned 8 matches across skill and design
  - first-discovery blocker-bookkeeping assertion returned 1 matching contract line
  - repository-guidance terminology search found the exact repo-owned skill path, `Owner: unassigned`, standalone `@codex review`, `reviewThreads(first: 100)`, and current `headRefOid` contract
  - forbidden-status search returned no `in-review` or `in_review` matches; auto-merge appears only in an explicit prohibition, personal skill paths are absent, `docs/prd/` appears only in the stale-personal-workflow override, and `scratchboard/` appears only in override/historical-compatibility guidance
  - integration diff and scope checks exited 0 and contained exactly the four Task 4 files
  - quality follow-up official validation exited 0 with `Skill is valid!`; the high-risk checklist is 694 words and remains below the approved 1,000-word ceiling
  - `agents/openai.yaml` remains unchanged at SHA-256 `840778D52C1848E98FE8ED923393A8D075DC16CF5EA5057D61288A8D9D77EEF3`
  - authority, pagination, owner-gate, parent-closeout, and early-merge contract searches exited 0; the forbidden contradiction/path search returned no matches
  - `tickets/TEMPLATE.md` metadata search returned exactly 3 matches, expected link targets exist, `git diff --check` exited 0, and working scope contains exactly the 10 approved quality-follow-up paths

### Current portable neutral v2 validation

- Exact commands:
  - `$validator = Join-Path $env:USERPROFILE '.codex\skills\.system\skill-creator\scripts\quick_validate.py'; python $validator .codex-skill-staging/anima-project-management`
  - `(Get-Content .codex-skill-staging/anima-project-management/SKILL.md | Measure-Object -Word).Words`
  - `python -c "from pathlib import Path; import yaml; text=Path(r'.codex-skill-staging/anima-project-management/SKILL.md').read_text(encoding='utf-8'); actual=yaml.safe_load(text.split('---', 2)[1]); expected={'name':'anima-project-management','description':'Use when animaOS initiative or feature work involves status, definition, revision, PRD, plan, tickets, claim, assignment, resume, block, completion, next-ticket selection, ticket-ID execution, parent-child reconciliation, or explicitly requested publish, PR, Codex review, or monitor-until-clean; exclude explanation, diagnosis-only, and isolated edits unless publish or review is explicitly requested.'}; assert actual == expected; print(actual)"`
  - `python -c "from pathlib import Path; import yaml; actual=yaml.safe_load(Path(r'.codex-skill-staging/anima-project-management/agents/openai.yaml').read_text(encoding='utf-8')); expected={'interface':{'display_name':'Anima Project Management','short_description':'Manage animaOS project and ticket lifecycles','default_prompt':'Use '+chr(36)+'anima-project-management to manage this animaOS initiative or ticket lifecycle.'}}; assert actual == expected; print(actual)"`
  - `rg -n '\.codex-skill-staging/anima-project-management/SKILL\.md' AGENTS.md`
  - `$forbidden = rg -n -i 'TODO|placeholder|C:\\Users\\|\.agents/skills|\.codex/skills/' .codex-skill-staging/anima-project-management 2>$null; if ($LASTEXITCODE -eq 0) { $forbidden; throw 'Forbidden pattern found in staged skill' }; if ($LASTEXITCODE -ne 1) { throw "rg failed with exit $LASTEXITCODE" }`
  - `$files = @('docs/audit/skills/2026-07-15-anima-project-management-evaluation.md','tickets/repo-workflow/RWF-005-anima-project-management-skill.md','tickets/repo-workflow/RWF-000-parent.md'); $winRoot = 'C:' + [IO.Path]::DirectorySeparatorChar + 'Users' + [IO.Path]::DirectorySeparatorChar; $macRoot = '/' + 'Users' + '/'; $localName = 'le' + 'oca'; $private = @($files | ForEach-Object { Select-String -LiteralPath $_ -SimpleMatch -Pattern $winRoot,$macRoot,$localName }); if ($private.Count -ne 0) { $private; throw 'Machine-specific user path remains' }`

Replay-manifest, prompt-neutrality, audit-count, and pagination assertions:

```powershell
@'
from pathlib import Path
import json
import re

audit = Path("docs/audit/skills/2026-07-15-anima-project-management-evaluation.md")
text = audit.read_text(encoding="utf-8")
blocks = re.findall(
    r"### Equivalent `collaboration\.spawn_agent` argument object\n\n```json\n(.*?)\n```",
    text,
    re.S,
)
objects = [json.loads(block) for block in blocks]
expected = {
    "v2_urgent_portable": "scenario-1-urgent-feature.md",
    "v2_next_ticket_portable": "scenario-2-next-ticket.md",
    "v2_mark_done_portable": "scenario-3-mark-done.md",
    "v2_diagnosis_edit_portable": "scenario-4-diagnosis-edit.md",
    "v2_multipage_monitor_portable": "scenario-5a-multipage-monitor.md",
    "v2_cursor_failure_portable": "scenario-5b-cursor-failure.md",
}
assert len(objects) == 6
assert {item["task_name"] for item in objects} == set(expected)
for item in objects:
    assert set(item) == {"task_name", "fork_turns", "message"}
    assert item["fork_turns"] == "none"
    message = item["message"]
    assert ".worktrees/repo-organization-project-management" in message
    assert expected[item["task_name"]] in message
    assert ("C:" + chr(92) + "Users" + chr(92)) not in message
    assert ("/" + "Users" + "/") not in message
    assert "approved behavior" not in message.lower()
    assert "expected answer" not in message.lower()
    assert "baseline rational" not in message.lower()
    for heading in (
        "## Proposed action log",
        "## File/state mutations",
        "## External actions",
        "## Stopping condition",
        "## Rationale",
    ):
        assert heading in message

assert text.count("## Portable neutral v2 Scenario ") == 6
assert text.count("- Result: PASS on first accepted run") == 6
assert text.count("### Exact synthetic fixture reconstruction content") == 6
assert text.count("### Exact neutral evaluator prompt") == 6
assert text.count("### Equivalent `collaboration.spawn_agent` argument object") == 6
assert text.count("### Complete verbatim output") == 6
assert text.count("### Contract comparison") == 6
assert text.count("- Skill refactor/rerun: none") == 6
assert text.count("### Superseded forward v1 result") == 5
assert "Forward result: not run yet" not in text
assert ("C:" + chr(92) + "Users" + chr(92)) not in text
assert ("/" + "Users" + "/") not in text
assert ("le" + "oca").lower() not in text.lower()

five_a = text.split("## Portable neutral v2 Scenario 5A", 1)[1].split(
    "## Portable neutral v2 Scenario 5B", 1
)[0]
assert five_a.count('"hasNextPage": true') == 3
assert five_a.count('"hasNextPage": false') == 4
assert "THREAD-BEHAVIOR-208" in five_a
assert "second thread page" in five_a
assert "second comment page" in five_a

five_b = text.split("## Portable neutral v2 Scenario 5B", 1)[1]
assert five_b.count('"hasNextPage": true') == 1
assert five_b.count('"hasNextPage": false') == 1
assert "synthetic cursor service unavailable" in five_b
assert "Fail closed" in five_b
assert "Do not declare the PR clean" in five_b
print("portable-neutral-v2 manifests=6 scenarios=6 v1_superseded=5 pagination=pass")
'@ | python -
```

- `bun test tests/repo-organization.test.ts`
- `bun run check:repo`
- `$root = (Resolve-Path .).Path; $candidate = Join-Path $root '.tmp-eval-anima-project-management-v2'; if (Test-Path -LiteralPath $candidate) { $fixture = (Resolve-Path $candidate).Path; if (-not $fixture.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture path escaped workspace' }; Remove-Item -Recurse -Force -LiteralPath $fixture }`
- `$remaining = @(Get-ChildItem -Force -Directory -Filter '.tmp-eval-*'); $tracked = @(git ls-files -- '.tmp-eval-*'); if ($remaining.Count -ne 0 -or $tracked.Count -ne 0) { throw 'Temporary evaluation fixture remains' }`
- `git diff --check 681dd11dc399faa8a593ef9e73dcb4796b91d5ad..HEAD`
- `git diff --name-only 681dd11dc399faa8a593ef9e73dcb4796b91d5ad..HEAD`
- `$changed = @(git diff --name-only 681dd11dc399faa8a593ef9e73dcb4796b91d5ad..HEAD); $expected = @('docs/audit/skills/2026-07-15-anima-project-management-evaluation.md','tickets/repo-workflow/RWF-000-parent.md','tickets/repo-workflow/RWF-005-anima-project-management-skill.md'); $delta = @(Compare-Object -ReferenceObject $expected -DifferenceObject $changed); if ($changed.Count -ne 3 -or $delta.Count -ne 0) { $changed; $delta; throw 'Committed Task 9 changed-path scope mismatch' }; "TASK9_CHANGED_PATHS=$($changed.Count)"`
- `git diff --name-only 681dd11dc399faa8a593ef9e73dcb4796b91d5ad..HEAD -- ':(glob)apps/**/src/**' ':(glob)apps/desktop/src-tauri/**' ':(glob)packages/**/src/**'`
- `git diff --name-only 681dd11dc399faa8a593ef9e73dcb4796b91d5ad..HEAD -- .codex-skill-staging/anima-project-management/SKILL.md`

- Results:
  - six distinct portable neutral `fork_turns=none` evaluators passed scenarios 1-4, multi-page 5A, and cursor-failure 5B on their first accepted runs; no accepted prompt exposed expected behavior, baseline rationalizations, or an answer sequence
  - 5A consumed two review pages, two review-thread pages, and the behavioral thread's two comment pages; it discovered the later-page defect, derived regression-first narrow fixing, pushed-OID/head synchronization, exact `@codex review`, and the same-head stopping rule
  - 5B refused a clean declaration and merge after the required next `reviewThreads` cursor page became unavailable
  - replay-manifest validation parsed six exact `collaboration.spawn_agent` argument objects with six unique agent names, `fork_turns=none`, repo-relative cwd/fixture paths, neutral prompts, and the five-field response schema
  - v1 remains preserved but explicitly superseded; committed local roots are normalized to `<repo-root>` and no machine-specific user path remains in the audit or current ticket files
  - official validation exited 0 with `Skill is valid!`; the unchanged staged skill is 694 words; exact frontmatter and three-field interface YAML assertions passed; `AGENTS.md` exact-path and staged-skill forbidden-pattern checks passed
  - focused repository validation passed with 32 tests, 59 assertions, and 0 failures; `bun run check:repo` passed
  - safe fixture cleanup left zero `.tmp-eval-*` directories and zero tracked fixture paths
  - committed-range `git diff --check` passed from base `681dd11dc399faa8a593ef9e73dcb4796b91d5ad`; its exact path assertion returned `TASK9_CHANGED_PATHS=3`, while the production-source filter and staged-skill assertion produced no output
- Committed Task 9 changed paths from `681dd11dc399faa8a593ef9e73dcb4796b91d5ad..HEAD`:
  - docs/audit/skills/2026-07-15-anima-project-management-evaluation.md
  - tickets/repo-workflow/RWF-005-anima-project-management-skill.md
  - tickets/repo-workflow/RWF-000-parent.md
- Residual notes:
  - The actual authorized draft-PR publication, current-head Codex review, and monitor-until-clean loop remains pending `RWF-006`; 5A and 5B are simulation evidence only.
  - No accepted evaluator mutated production files, personal skill directories, live tickets, services, or real PR state.
