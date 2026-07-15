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
- Updated: 2026-07-15 22:17 MYT
- Started: 2026-07-15 17:39 MYT
- Completed: 2026-07-15 22:17 MYT

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

## Validation

### Historical snapshots (superseded by current closeout validation)

The commands, path sets, and counts below are preserved as evidence from RED, GREEN, Task 4 integration, and the pre-thinning quality follow-ups. They are historical snapshots, not the current reproducible closeout checklist. In particular, skill-layout grep assertions and the wider changed-path inventories predate the final 694-word checklist and Task 9's exact three-file scope.

- Commands (historical):
  - `rg -n '^- (Status: in_progress|Owner: Codex)\r?$' tickets/repo-workflow/RWF-005-anima-project-management-skill.md`
  - ``rg -n '^\| `RWF-005` \| Add the anima project-management skill \| `in_progress` \| none \|\r?$' tickets/repo-workflow/RWF-000-parent.md``
  - `rg -n '^## Scenario [1-5]: .+\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^# Synthetic Scenario [1-5]: .+\r?$|^## (Constraints|Response contract)\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^### Exact evaluator prompt, synthetic fixture, and response contract\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^### Complete evaluator output\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^#{1,2} (Proposed action log|File/state mutations|External actions|Stopping condition|Rationale)\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^- Forward result: not run yet\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management`
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

### Current reproducible closeout validation

- Commands:
  - `python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management`
  - `(Get-Content .codex-skill-staging/anima-project-management/SKILL.md | Measure-Object -Word).Words`
  - `python -c "from pathlib import Path; import yaml; text=Path(r'.codex-skill-staging/anima-project-management/SKILL.md').read_text(encoding='utf-8'); actual=yaml.safe_load(text.split('---', 2)[1]); expected={'name':'anima-project-management','description':'Use when animaOS initiative or feature work involves status, definition, revision, PRD, plan, tickets, claim, assignment, resume, block, completion, next-ticket selection, ticket-ID execution, parent-child reconciliation, or explicitly requested publish, PR, Codex review, or monitor-until-clean; exclude explanation, diagnosis-only, and isolated edits unless publish or review is explicitly requested.'}; assert actual == expected; print(actual)"`
  - `python -c "from pathlib import Path; import yaml; actual=yaml.safe_load(Path(r'.codex-skill-staging/anima-project-management/agents/openai.yaml').read_text(encoding='utf-8')); expected={'interface':{'display_name':'Anima Project Management','short_description':'Manage animaOS project and ticket lifecycles','default_prompt':'Use '+chr(36)+'anima-project-management to manage this animaOS initiative or ticket lifecycle.'}}; assert actual == expected; print(actual)"`
  - `rg -n '\.codex-skill-staging/anima-project-management/SKILL\.md' AGENTS.md`
  - `$forbidden = rg -n -i 'TODO|placeholder|C:\\Users\\|\.agents/skills|\.codex/skills/' .codex-skill-staging/anima-project-management 2>$null; if ($LASTEXITCODE -eq 0) { $forbidden; throw 'Forbidden pattern found in staged skill' }; if ($LASTEXITCODE -ne 1) { throw "rg failed with exit $LASTEXITCODE" }`
  - PowerShell count assertions for five scenarios, five first-iteration passes, five fresh evaluator IDs, five exact prompts, five fresh fixtures, five complete verbatim outputs, five field comparisons, five no-refactor results, and zero `Forward result: not run yet` placeholders
  - `bun test tests/repo-organization.test.ts`
  - `bun run check:repo`
  - safe fixture removal using resolved root and fixture paths plus a root-prefix assertion before `Remove-Item -Recurse -Force -LiteralPath $fixture`
  - `Get-ChildItem -Force -Directory -Filter '.tmp-eval-*'`
  - `git ls-files -- '.tmp-eval-*'`
  - `git diff --check`
  - exact changed-path, production-source, personal-skill/external-path, and staged-skill diff assertions over `git diff --name-only HEAD`
- Results:
  - official validation exited 0 with `Skill is valid!`; the unchanged staged skill is 694 words
  - frontmatter parsed to exactly the approved `name` and trigger-only `description`; interface YAML parsed to exactly the three approved interface fields and default prompt
  - `AGENTS.md` contains the exact repo-owned skill path; the staged-skill forbidden-pattern search returned no matches (expected `rg` exit 1)
  - all five forward scenarios passed on iteration 1 with five distinct `fork_turns=none` evaluators; the audit contains complete prompt, fixture, verbatim output, and field-by-field evidence for each; no stale forward placeholder remains
  - `.codex-skill-staging/anima-project-management/SKILL.md` is unchanged because no observed forward loophole required a guardrail
  - focused repository tests passed: 32 tests, 59 assertions, 0 failures; `bun run check:repo` passed
  - fixture cleanup reported zero remaining `.tmp-eval-*` directories and zero tracked fixture paths
  - `git diff --check` passed; the current scope contains exactly the three changed paths below, with zero production-source paths and zero personal-skill or machine-external paths
- Changed paths:
  - docs/audit/skills/2026-07-15-anima-project-management-evaluation.md
  - tickets/repo-workflow/RWF-005-anima-project-management-skill.md
  - tickets/repo-workflow/RWF-000-parent.md
- Residual notes:
  - The actual authorized draft-PR publication, current-head Codex review, and monitor-until-clean loop is intentionally pending `RWF-006`; Scenario 5 is isolated simulation evidence only.
  - No production file, personal skill directory, live ticket fixture, external service, or real PR was mutated by the forward evaluators.
