# RWF-001 - Rebuild the canonical ticket initiative index

- Status: done
- Priority: P2
- Scope: `tickets`
- Parent: `RWF-000`
- Depends on: none
- Owner: Codex
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 20:06 MYT
- Started: 2026-07-15 19:37 MYT
- Completed: 2026-07-15 20:06 MYT

## Goal

Rebuild `tickets/README.md` as the concise canonical index of active, completed, and legacy or unclassified initiatives.

## Deliverables

- Rebuild the canonical `tickets/README.md` initiative index
- Classify conforming parent trackers as active or completed from normalized parent metadata
- Classify folders without a conforming parent as legacy or unclassified
- Link classified initiatives to parent trackers and retain conventions, template/workflow links, and `bun run check:repo`

## Acceptance

- `tickets/README.md` contains `Active Initiatives`, `Completed Initiatives`, and `Legacy or Unclassified` sections
- Every conforming parent tracker appears exactly once under active or completed according to normalized parent `Status:` metadata
- Parent completion is never inferred from child state, historical prose, blockers, or progress counts
- Every folder without a conforming parent is listed under legacy or unclassified, and every listed parent link resolves
- The index retains ticket conventions, template and workflow links, and the `bun run check:repo` command without duplicating child acceptance criteria

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.
- 2026-07-15 17:11 MYT - Aligned the ticket with the combined repository-organization plan and canonical `tickets/README.md` dashboard.
- 2026-07-15 17:27 MYT - Narrowed the outcome to the approved concise initiative classification index.
- 2026-07-15 19:37 MYT - Codex claimed `RWF-001` on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`; set the child and parent row to `in_progress`.
- 2026-07-15 19:46 MYT - Completed `RWF-001` after normalizing authoritative ticket state, synchronizing both supported parent-table forms, rebuilding the derived initiative index, and passing the recorded validation; set the parent row to `done` while the parent remains `in_progress` for its remaining children.
- 2026-07-15 20:02 MYT - Reopened `RWF-001` after bidirectional validation exposed the missing `VMI-008` parent row; preserved the prior completion timestamp `2026-07-15 19:46 MYT`, cleared the current completion, and synchronized the parent row to `in_progress` before repair.
- 2026-07-15 20:06 MYT - Re-completed `RWF-001` after adding `VMI-008` to its parent table and completed history, extending the future validator contract in both directions, and passing the 146-row bidirectional and dashboard checks; prior completion `2026-07-15 19:46 MYT` remains preserved in history.

## Validation

- Commands:
  - ``rg -n '^- Status: (todo|in-review|in_review)\r?$|^\|.*\|[[:space:]]*`?(todo|in-review|in_review)`?[[:space:]]*\|' tickets``
  - read-only PowerShell top-metadata parser before the first `##` for non-empty `Completed:` with non-`done` status
  - read-only PowerShell bidirectional parent-child parser for both supported table headings, header-derived `Ticket`/`Status` columns, quoted or unquoted cells, parent-row-to-unique-child status equality, and every child `Parent:` reference appearing in exactly one row of its conforming parent
  - read-only PowerShell index parser for the three required sections, normalized parent classification, unique parent links, legacy folders, overview links, and link resolution
  - `rg -n '^## (Active Initiatives|Completed Initiatives|Legacy or Unclassified)\r?$' tickets/README.md`
  - `git diff --check`
  - `git diff --unified=0 -- tickets`
- Changed paths:
  - docs/ops/prd-ticket-workflow.md
  - docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
  - docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md
  - tickets/README.md
  - tickets/agent-runtime-hardening/ARH-000-parent.md
  - tickets/agent-runtime-hardening/ARH-001-fix-anthropic-compaction.md
  - tickets/agent-runtime-hardening/ARH-002-cancellation-safe-turn-lifecycle.md
  - tickets/agent-runtime-hardening/ARH-003-soul-block-optimistic-locking.md
  - tickets/agent-runtime-hardening/ARH-004-background-retry-hygiene.md
  - tickets/agent-runtime-hardening/ARH-005-llm-client-robustness.md
  - tickets/agent-runtime-hardening/ARH-006-anthropic-prompt-caching.md
  - tickets/agent-runtime-hardening/ARH-007-background-dirty-checks.md
  - tickets/agent-runtime-hardening/ARH-008-context-token-hygiene.md
  - tickets/agent-runtime-hardening/ARH-009-embedding-contract-consistency.md
  - tickets/agent-runtime-hardening/ARH-010-crash-durable-extraction.md
  - tickets/agent-runtime-hardening/ARH-011-ttft-parallel-assembly-single-decrypt.md
  - tickets/agent-runtime-hardening/ARH-012-retrieval-scoring-correctness.md
  - tickets/agent-runtime-hardening/ARH-013-dedupe-drifted-logic.md
  - tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md
  - tickets/production-document-processing/PDP-000-production-document-processing.md
  - tickets/production-document-processing/PDP-001-chat-grounding-quick-wins.md
  - tickets/production-document-processing/PDP-002-hybrid-retrieval-fts-rrf.md
  - tickets/production-document-processing/PDP-003-structured-intermediate-chunking.md
  - tickets/production-document-processing/PDP-004-tiered-parsing-docling.md
  - tickets/production-document-processing/PDP-005-html-web-extraction.md
  - tickets/production-document-processing/PDP-006-agentic-document-tools.md
  - tickets/production-document-processing/PDP-007-llm-compiler-wiring-autocompile.md
  - tickets/production-document-processing/PDP-008-contextual-blurbs-reranker.md
  - tickets/production-document-processing/PDP-009-eval-harness-docs-validation.md
  - tickets/repo-workflow/RWF-000-parent.md
  - tickets/repo-workflow/RWF-001-ticket-dashboard.md
  - tickets/repo-workflow/RWF-003-ticket-metadata-validation.md
  - tickets/visual-memory-image-assets/VMI-000-parent.md
- Notes:
  - normalized 25 authoritative headers and 22 authoritative child-status cells across 2 parent trackers; missing or ambiguous child mappings: 0
  - legacy authoritative status search returned no matches (expected `rg` exit 1)
  - non-empty completion/status violations: 0
  - bidirectional parent-child validation covered 17 parents, 146 authoritative rows, and 146 reverse child references with 0 missing, ambiguous, duplicate, status, or synchronization violations
  - index validation covered 17 conforming parents: 11 active, 6 completed, and 1 legacy or unclassified folder, with 0 classification or link violations
  - all three required index section headings were found; `git diff --check` exited 0
  - final diff inspection found no edits to pre-existing historical prose or timestamps; new prose is limited to the intentional RWF lifecycle/planning entries, the `VMI-000` repair entry, validator-contract clarifications, and the rebuilt `tickets/README.md`
  - `VMI-008` now appears once in the `VMI-000` table and once in completed history using its existing `2026-07-01 13:04 MYT` completion timestamp
  - residual risks or follow-ups: none for `RWF-001`; implementation of the bidirectional `bun run check:repo` guard remains scheduled for `RWF-003`
