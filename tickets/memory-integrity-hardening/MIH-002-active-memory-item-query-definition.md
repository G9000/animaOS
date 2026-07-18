# MIH-002 - Single "active memory item" query definition

- Status: backlog
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent`
- Parent: none
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: none
- Created: 2026-07-19 03:34 MYT
- Updated: 2026-07-19 03:34 MYT
- Started:
- Completed:

## Goal

Replace the ad-hoc, copy-pasted "active memory item" predicate scattered across the codebase with one shared definition, so a new dimension of "active" only has to be added once.

## Context

"Active memory item" is expressed inline as `superseded_by IS NULL` in a dozen+ queries. IL-005 added a second required dimension — `distilled_at IS NULL` (tombstones) — and the #112 review then found roughly six separate query sites that each had to be patched to add it: scored retrieval, listing, overview counts, tag lookup, focus queries, embedding backfill (×2), evidence audit, evidence backfill, `decay_all_heat`, `get_hottest_items`, health check, and the three search hit-hydration paths. Every one was found individually. A shared helper would have made IL-005 a one-line change.

## Deliverables

- A single `active_memory_items(user_id)` (and/or a reusable `active_item_filter()` returning the where-clauses) used by all read/maintenance paths that mean "durable, visible memory items".
- Migrate the existing sites to it (superseded + distilled at minimum; extensible for future dimensions).
- A test that adding a hypothetical new "hidden" dimension requires touching only the helper.

## Acceptance

- All active-item read/maintenance queries route through one definition.
- No behavioral change vs current (the guards are already correct post-IL-005); this is a consolidation.

## Activity Log

- 2026-07-19 03:34 MYT - Ticket created from IL-005 review findings.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Evidence: PR #112 review threads (multiple distilled_at guard findings across scattered queries).
