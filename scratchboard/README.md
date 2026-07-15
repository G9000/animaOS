# Legacy workflow area - frozen for new workstreams

`scratchboard/` is a legacy workflow area and is frozen as an intake location: no new initiative or workstream starts here. Existing legacy workstreams may continue here until a deliberate, approved cutover to canonical tickets.

This cleanup leaves every existing legacy artifact unchanged. A later migration preserves that history and its inbound links; it does not make legacy files forever immutable when an approved cutover needs deliberate cross-links or state-transfer notes. After a workstream's cutover, record new progress only in tickets.

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

Consider migration when a legacy workstream is touched, but perform its cutover only when deliberately approved. Do not bulk-move scratchboard history.

1. Read the full legacy workstream and all inbound links before changing state.
2. Find or create the canonical PRD or design/spec, when needed, and a dated implementation plan.
3. Create one parent tracker plus claimable child tickets with explicit dependencies and acceptance criteria.
4. Cross-link the new parent and the legacy folder or README in both directions.
5. Copy only current scope and state into tickets while preserving legacy history and inbound links.
6. After the approved cutover, record all new progress only in tickets; do not maintain parallel scratchboard status.
7. Validate links and parent/child state before declaring the migration complete.
