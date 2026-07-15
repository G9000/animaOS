# Legacy workflow area - frozen for new work

`scratchboard/` is a legacy workflow area and is frozen for new work. Existing artifacts remain readable and unchanged so their history and inbound links continue to work.

All new work follows `PRD -> design/spec when needed -> dated plan -> parent and child tickets` through the canonical locations:

- [docs/ops/prd-ticket-workflow.md](../docs/ops/prd-ticket-workflow.md)
- [docs/prds/](../docs/prds/)
- [docs/superpowers/plans/](../docs/superpowers/plans/)
- [tickets/](../tickets/)

## Legacy inventory, not current status

- `_system` is legacy coordination metadata, not a product initiative.
- `v2-memory-recall-reliability` is named by the legacy active-task index. Confirm its current state against current artifacts and human context before migrating it; its presence or checklist marks do not establish an active or completed ticket state.
- `v1-encrypted-core` retains unresolved-looking `Still Missing` items and unchecked `Success Criteria` items. It is a migration candidate whose current state requires human and current-artifact confirmation; do not infer that it is active or done.

## Incremental migration checklist

Migrate a legacy workstream only when it is touched or deliberately approved for migration. Do not bulk-move scratchboard history.

1. Read the full legacy workstream and all inbound links before changing state.
2. Find or create the canonical PRD or design/spec, when needed, and a dated implementation plan.
3. Create one parent tracker plus claimable child tickets with explicit dependencies and acceptance criteria.
4. Cross-link the new parent and the legacy folder or README in both directions.
5. Copy only current scope and state into tickets while preserving all historical scratchboard files unchanged.
6. Stop recording new progress in scratchboard after the ticket cutover.
7. Validate links and parent/child state before declaring the migration complete.
