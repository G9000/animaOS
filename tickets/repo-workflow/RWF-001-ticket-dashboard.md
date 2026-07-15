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
- Updated: 2026-07-16 00:28 MYT
- Started: 2026-07-15 19:37 MYT
- Completed: 2026-07-16 00:28 MYT

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
- 2026-07-15 23:42 MYT - Reopened `RWF-001` because rebasing onto current `origin/main` added the canonical `inner-life-v1` initiative and made the derived dashboard acceptance stale; preserved the current completion timestamp `2026-07-15 20:06 MYT`, cleared current completion, and synchronized the parent row to `in_progress` before reconciliation.
- 2026-07-15 23:43 MYT - Re-completed `RWF-001` after adding the normalized `inner-life-v1` parent to the active dashboard and verifying 18 conforming parents, 153 authoritative rows, 153 reverse references, 12 active initiatives, 6 completed initiatives, 1 legacy folder, 18 unique parent links, and 0 graph, classification, or link violations.
- 2026-07-16 00:23 MYT - Reopened `RWF-001` for the acceptance-breaking active-ownership defect reported on PR #99: legacy `in_review` tickets without an owner were normalized to `in_progress`, which violates the canonical transition contract. Preserved completion `2026-07-15 23:43 MYT` in history, cleared the current completion, and synchronized the parent row to `in_progress` before the test-first repair.
- 2026-07-16 00:28 MYT - Re-completed `RWF-001` after a red/green active-owner and parent-lifecycle regression, evidence-safe status normalization, live zero-violation ownership audit, repository/skill validation, exact scope checks, and the root build; synchronized the parent row and one current completed-history entry to `done` while `RWF-006` and `RWF-000` remain `in_progress` for review follow-through.

## Validation

- Commands:
  - ``rg -n '^- Status: (todo|in-review|in_review)\r?$|^\|.*\|[[:space:]]*`?(todo|in-review|in_review)`?[[:space:]]*\|' tickets``
  - read-only PowerShell top-metadata parser before the first `##` for non-empty `Completed:` with non-`done` status
  - read-only PowerShell bidirectional parent-child parser for both supported table headings, header-derived `Ticket`/`Status` columns, quoted or unquoted cells, parent-row-to-unique-child status equality, and every child `Parent:` reference appearing in exactly one row of its conforming parent
  - read-only PowerShell index parser for the three required sections, normalized parent classification, unique parent links, legacy folders, overview links, and link resolution
  - `rg -n '^## (Active Initiatives|Completed Initiatives|Legacy or Unclassified)\r?$' tickets/README.md`
  - `bun run check:repo`
  - PowerShell/Bun graph counter:
    ```powershell
    @'
    import { Glob } from "bun";
    import { readFile } from "node:fs/promises";
    import { parseTicketDocument } from "./scripts/check-repo-organization.ts";
    const docs = [];
    for await (const path of new Glob("tickets/**/*.md").scan(".")) {
      const doc = parseTicketDocument(path, await readFile(path, "utf8"));
      if (doc.ticketId) docs.push(doc);
    }
    const parents = docs.filter((doc) => doc.hasAuthoritativeChildTable);
    const ids = new Set(parents.map((doc) => doc.ticketId));
    console.log("PARENTS=" + parents.length);
    console.log("AUTHORITATIVE_ROWS=" + parents.reduce((n, doc) => n + doc.parentRows.length, 0));
    console.log("REVERSE_REFS=" + docs.filter((doc) => doc.parent && doc.parent !== "none" && ids.has(doc.parent)).length);
    '@ | bun -
    ```
  - read-only PowerShell dashboard-section parser over `tickets/README.md` that resolves every first parent link, derives expected active/completed classification from top-level parent `Status:`, and asserts `12` active, `6` completed, `1` legacy, `18` parent links, `18` unique parent links, and `0` classification errors
  - ``rg -n '^\- \[Inner Life v1\]\(\./inner-life-v1/IL-000-parent\.md\) \(`backlog`; \[overview\]\(\./inner-life-v1/README\.md\)\)\r?$' tickets/README.md``
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
  - current-base reconciliation: tickets/README.md
  - current-base reconciliation: tickets/repo-workflow/RWF-001-ticket-dashboard.md
  - current-base reconciliation: tickets/repo-workflow/RWF-000-parent.md
- Notes:
  - normalized 25 authoritative headers and 22 authoritative child-status cells across 2 parent trackers; missing or ambiguous child mappings: 0
  - legacy authoritative status search returned no matches (expected `rg` exit 1)
  - non-empty completion/status violations: 0
  - current bidirectional parent-child validation covers 18 parents, 153 authoritative rows, and 153 reverse child references with 0 missing, ambiguous, duplicate, status, or synchronization violations
  - current index validation covers 18 conforming parents: 12 active, 6 completed, and 1 legacy or unclassified folder, with 18 unique parent links and 0 classification or link violations
  - all three required index section headings were found; `git diff --check` exited 0
  - final diff inspection found no edits to pre-existing historical prose or timestamps; new prose is limited to the intentional RWF lifecycle/planning entries, the `VMI-000` repair entry, validator-contract clarifications, and the rebuilt `tickets/README.md`
  - `VMI-008` now appears once in the `VMI-000` table and once in completed history using its existing `2026-07-01 13:04 MYT` completion timestamp
  - residual risks or follow-ups: none for `RWF-001`; the bidirectional `bun run check:repo` guard is implemented and passes on the rebased current-main graph

### PR #99 active-ownership review fix

- Red evidence:
  - `bun test tests/repo-organization.test.ts` failed only the two new regressions: missing `ticket-ownership` violations for unassigned/missing owners and missing `ticket-lifecycle` violations for a backlog parent with an active child; result was 32 pass, 2 fail, 61 expectations.
- Green commands:
  - `bun test tests/repo-organization.test.ts`
  - `bun run check:repo`
  - PowerShell top-metadata audit asserting zero `in_progress` tickets with missing or `unassigned` owners
  - `python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management`
  - `git diff --check`
  - exact merge-base, changed-path, production-hotspot, and staged-skill PowerShell assertions over `git diff --name-only origin/main`
  - `bun run build`
- Results:
  - focused suite passed 34 tests, 61 expectations, and 0 failures; live organization check passed; `IN_PROGRESS_UNASSIGNED=0`
  - PDP-001 through PDP-009 and ASR-001 are `backlog`/`unassigned`; PDP-000 and all nine rows remain synchronized at `backlog`
  - PCF-000 and PCF-002 remain `in_progress` with `Owner: Codex`, supported by their existing claim/start and active PR history
  - official staged-skill validation and both diff checks passed; merge base remained `408d9b64abf639739a2d044abfda647958e7ff3e`, changed scope was 56 paths, production hotspots were 0, and the staged-skill set remained exactly 2 intended paths
  - root build passed for cached server/desktop Nx builds plus `cargo check -p animus`; the existing Vite chunk-size warning remained non-blocking
- Review-fix changed paths:
  - docs/ops/prd-ticket-workflow.md
  - docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
  - docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md
  - scripts/check-repo-organization.ts
  - tests/repo-organization.test.ts
  - tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md
  - tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
  - tickets/portable-core-filesystem/PCF-002-corefs-catalog.md
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
  - tickets/repo-workflow/RWF-006-integration-pr-review.md
- Residual risks or follow-ups:
  - none for the active-ownership defect; `RWF-006` and `RWF-000` stay open for the next current-head review.
