---
name: anima-project-management
description: Use when an animaOS initiative or feature needs status, definition, revision, PRD, plan, tickets, claim, assignment, resume, block, completion, next-ticket selection, ticket-ID execution, parent-child reconciliation, or explicitly requested publish, PR, Codex review, or monitor-until-clean follow-through; exclude explanation, diagnosis-only, and isolated edits unless publish or review is explicitly requested.
---

# Anima Project Management

## Overview

Route animaOS work through the smallest honest lifecycle. Keep repository artifacts authoritative, project state synchronized, external actions explicitly authorized, and completion evidence-based.

## Required sources and modes

Read `AGENTS.md` and `docs/ops/prd-ticket-workflow.md` completely. Read `tickets/TEMPLATE.md` before creating tickets. Read the relevant PRD, design/spec, dated plan, parent tracker, and child tickets before changing their state.

| Mode | Use | Boundary |
| --- | --- | --- |
| Status-only | Report initiative or ticket state | Read-only; do not claim or mutate |
| Planning | Define or revise scope and executable work | Discover existing artifacts first; avoid duplicate initiatives |
| Ticket execution | Select, claim, resume, block, implement, or complete work | Follow child acceptance and synchronize its parent |
| Publish/review | User explicitly requests publish, PR, Codex review, or monitoring | No merge without separate authorization |

Diagnosis-only stays read-only. A truly isolated edit creates no fake PRD, plan, parent, or child; an explicit publish/review request routes only that edit into publish/review mode.

## Plan without duplicating

Search existing PRDs, specs, plans, and ticket initiatives before creating anything; reuse the established source of truth. For new or changed scope, use: PRD -> design/spec when approval is needed -> dated implementation plan -> one parent plus ordered child tickets. Respect active design and planning approval gates before downstream artifacts or implementation. Keep artifact responsibilities distinct and cross-link them. Create executable children as `Status: backlog`, `Owner: unassigned` unless the user explicitly assigns them.

## Select and claim safely

For a named ticket, read it and its parent. For "next ticket," choose the first ordered child that is backlog, unassigned, has all dependencies done, and has no visible branch, worktree, or activity-log claim. A dependency may be waived only by explicit user direction recorded in the ticket. If none qualifies, report each blocking owner, dependency, claim, or state.

Claim before implementation as one child/parent transaction. Never steal an owner. Set child `Owner: Codex`, `Status: in_progress`, `Started:` only if empty, and `Updated:` in MYT; log the claim with branch/worktree. Set the matching parent row and top-level parent `Status:` to `in_progress`, update parent `Updated:`, and add material activity. Do not change parent ownership.

## Execute, block, and complete

Preserve unrelated dirt and stage only intended work. Ticket execution does not imply push, PR, deployment, messages, or other external actions. Record material progress and scope changes in `Updated:` and activity. Use `blocked` only for a concrete missing decision, dependency, permission, or external-state condition; when cleared, return to `in_progress` and log both transitions. Block the parent only when no eligible initiative progress remains because required work is blocked.

Complete only after acceptance passes and validation and changed paths are recorded. Set child `done`, `Updated:`, `Completed:`, and completion activity; synchronize the parent row, completed history, timestamp, and material activity. Mark the parent done only when every required child and initiative-level validation/closeout pass.

If publish/review is authorized, keep the integration child and parent open through a clean implementation head. Close them in a metadata commit, push, re-request review, and treat clean current-head review of that closeout commit as the terminal guard. Actionable feedback reopens state consistently.

## Publish and review only when authorized

Inspect intended scope and staging; confirm base/head, run focused and broad checks, push with upstream, and default to a draft PR. Use PR body sections `Summary`, `Scope`, `Review focus`, `Out of scope`, and `Validation`. Ask review to prioritize correctness, security, regressions, contracts, migrations, and missing tests while de-prioritizing style-only, speculative, unrelated, or tool-enforced churn without concealing defects. Post this exact standalone comment:

```text
@codex review
```

Cache PR number, branch, and `headRefOid`. Query GraphQL `reviewThreads(first: 100)` for `isResolved`, `isOutdated`, path, line, comments, review commits, and `merged`; use flat comments only as supplemental context. A latest Codex review older than `headRefOid` is stale, never clean.

Classify unresolved, non-outdated threads as actionable or duplicate, already-fixed, outdated, style-only, speculative, unrelated, or contradicted by current code/tests. Fix actionable defects narrowly; add a failing regression first for behavioral defects. For non-actionable feedback, reply once with an evidence-based disposition instead of blind code churn. Resolve only after a verified fix or sound disposition. Run focused then appropriate broad validation, commit and push, resolve addressed threads, post `@codex review`, and repeat.

Stop only when the latest Codex review covers current `headRefOid`, required checks pass, zero unresolved non-outdated actionable threads remain, and every non-actionable thread is dispositioned. Never auto-merge.

If another actor merges early while tracked state is open, create a metadata-only follow-up branch and draft PR and run the same loop; if permissions prevent it, leave state `in_progress`, record the blocker, and report it. If an acceptance-breaking finding arrives after completion, set child, matching parent row, and parent status to `in_progress`; preserve prior child and parent completion timestamps in their respective activity logs; clear current `Completed:` values and remove the child from parent completed history. Fix, then close again with new timestamps. Do not reopen for non-actionable or non-acceptance findings.

Delete any asynchronous PR monitor when its loop reaches terminal state, the PR closes, or a replacement monitor is created.

## Red flags

- Skipping the claim transaction or dependency rules
- Overwriting ownership or changing the parent owner
- Marking done before acceptance, validation, and changed paths exist
- Blindly implementing review nitpicks or hiding valid defects
- Stopping on a stale review or unresolved current-head evidence
- Auto-merging or taking unauthorized external actions
- Installing this repo-owned skill into a personal skill directory
