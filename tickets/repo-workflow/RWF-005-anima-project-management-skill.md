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
- Updated: 2026-07-15 17:49 MYT
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
- RED evidence records at least one real baseline gap without the skill, and GREEN/REFACTOR evidence shows all approved forward scenarios passing with fresh isolated agents
- Evaluation fixtures are removed, no personal skill directory is changed, and the final skill remains concise and free of placeholders or machine-specific paths

## Activity Log

- 2026-07-15 17:11 MYT - Ticket created from the approved anima project-management skill design and implementation plan.
- 2026-07-15 17:39 MYT - Codex claimed `RWF-005` on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management` before RED baseline evaluation.
- 2026-07-15 17:49 MYT - Recorded five fresh-agent RED baseline scenarios; no workflow failure or missing guarantee was observed, so no failure was invented. Forward evaluation remains pending.

## Validation

- Commands:
  - ticket/parent/plan/evaluation PowerShell assertions
  - `git diff --check`
  - safe resolved-path fixture removal and `git status --short --untracked-files=all`
- Changed paths:
  - docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
  - docs/audit/skills/2026-07-15-anima-project-management-evaluation.md
  - tickets/repo-workflow/RWF-005-anima-project-management-skill.md
  - tickets/repo-workflow/RWF-000-parent.md
- Notes:
  - RED fixtures removed; no `.tmp-eval-*` path remains
  - all forward results are `not run yet`
