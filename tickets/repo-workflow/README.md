# Repo Workflow

This folder tracks local tickets for maintaining the repo-native planning and ticket system itself.

Agents claiming work in this folder should follow [prd-ticket-workflow.md](../../docs/ops/prd-ticket-workflow.md).

Parent tracker: [RWF-000-parent.md](./RWF-000-parent.md)

## Order

1. `RWF-001` Add top-level tickets dashboard
2. `RWF-002` Mark scratchboard legacy and add migration checklist
3. `RWF-003` Add ticket metadata validation
4. `RWF-004` Reconcile repository documentation and hygiene
5. `RWF-005` Add the anima project-management skill
6. `RWF-006` Validate, publish, and complete PR review

## Done Condition

- Repo-local planning has a single dashboard for active initiatives.
- New work uses tickets instead of scratchboard.
- Repository metadata, parent-child consistency, manifests, documentation hygiene, tracked log state, and the scratchboard marker can be checked mechanically and read-only.
- The repo-owned project-management skill is integrated and supported by RED-GREEN-REFACTOR evaluation evidence.
- Implementation tickets may close after their local acceptance criteria pass. `RWF-006` and `RWF-000` remain `in_progress` through draft-PR review and review of the final project-metadata head.
- The initiative is complete only when all six children are `done`, required validation passes, and the final closeout head has a current-head Codex review with no unresolved non-outdated actionable threads.
