# RWF-005 - Add the anima project-management skill

- Status: in_progress
- Priority: P2
- Scope: `.codex-skill-staging/anima-project-management`, `AGENTS.md`, `docs/ops`, `docs/audit/skills`
- Parent: `RWF-000`
- Depends on: none
- Owner: Codex
- PRD: none
- Spec: docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-07-15 17:11 MYT
- Updated: 2026-07-15 18:01 MYT
- Started: 2026-07-15 17:39 MYT
- Completed:

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

## Validation

- Commands:
  - `rg -n '^- (Status: in_progress|Owner: Codex)\r?$' tickets/repo-workflow/RWF-005-anima-project-management-skill.md`
  - ``rg -n '^\| `RWF-005` \| Add the anima project-management skill \| `in_progress` \| none \|\r?$' tickets/repo-workflow/RWF-000-parent.md``
  - `rg -n '^## Scenario [1-5]: .+\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^# Synthetic Scenario [1-5]: .+\r?$|^## (Constraints|Response contract)\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^### Exact evaluator prompt, synthetic fixture, and response contract\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^### Complete evaluator output\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^#{1,2} (Proposed action log|File/state mutations|External actions|Stopping condition|Rationale)\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `rg -n '^- Forward result: not run yet\r?$' docs/audit/skills/2026-07-15-anima-project-management-evaluation.md`
  - `git diff --check`
  - `git diff --cached --check`
  - `git diff --name-only HEAD`
  - `git status --short --untracked-files=all`
- Changed paths:
  - docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
  - docs/audit/skills/2026-07-15-anima-project-management-evaluation.md
  - tickets/repo-workflow/RWF-005-anima-project-management-skill.md
- Notes:
  - RED fixtures removed; no `.tmp-eval-*` path remains
  - owner/status search returned 2 matches; synchronized parent-row search returned 1 match
  - scenario, exact-prompt, complete-output, and forward-result searches each returned 5 matches; fixture/constraint/contract search returned 15 matches
  - structured-output search returned 25 headings: all 5 required fields for each of 5 evaluator outputs
  - both diff checks exited 0; working scope contained exactly the 3 follow-up paths listed above
  - all five forward results are `not run yet`
