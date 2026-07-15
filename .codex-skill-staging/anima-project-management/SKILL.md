---
name: anima-project-management
description: Use when animaOS initiative or feature work involves status, definition, revision, PRD, plan, tickets, claim, assignment, resume, block, completion, next-ticket selection, ticket-ID execution, parent-child reconciliation, or explicitly requested publish, PR, Codex review, or monitor-until-clean; exclude explanation, diagnosis-only, and isolated edits unless publish or review is explicitly requested.
---

# Anima Project Management

## Overview

Use the smallest honest lifecycle. Keep repository artifacts authoritative, project state synchronized, external actions authorized, and completion evidence-based.

## Required sources and modes

Read `AGENTS.md` and `docs/ops/prd-ticket-workflow.md` completely. Read `tickets/TEMPLATE.md` before creating tickets. Read the relevant PRD, design/spec, dated plan, parent, and children before changing state.

| Mode | Use | Boundary |
| --- | --- | --- |
| Status-only | Report state | Read-only |
| Planning | Define/revise scope and artifacts | Discover first; avoid duplicates |
| Ticket execution | Select, claim, resume, block, implement, complete | Follow acceptance; synchronize parent |
| Publish/review | Explicit publish, PR, Codex review, or monitoring request | No merge without separate authorization |

Diagnosis-only stays read-only. An isolated edit creates no fake project artifacts; explicit publish/review routes only that edit into publish/review mode.

## Plan without duplicating

Search existing PRDs, specs, plans, and initiatives before creating anything; reuse their source of truth. For new/changed scope use PRD -> design/spec when approval is needed -> dated plan -> one parent plus ordered children. Respect active approval gates. Cross-link distinct artifacts. New executable children are `Status: backlog`, `Owner: unassigned` unless explicitly assigned.

## Select, transition, and claim safely

For a named ticket, read it and its parent, then apply only its legal transition:

| Current child state | Action |
| --- | --- |
| Backlog and unassigned | Claim normally |
| Codex-owned `in_progress` | Resume; preserve `Started:` and do not add a false new claim |
| Codex-owned `blocked` | Resume only after the blocker clears; transition/log child and parent back to `in_progress` |
| `done` | Refuse normal execution; only the acceptance-breaking review routine may reopen |
| Another owner (any state) | Never mutate unless the user explicitly authorizes reassignment; log it |

For "next ticket," keep documented order and choose the first backlog, unassigned child whose dependencies are done and with no visible branch, worktree, or activity claim. Record any explicit dependency waiver. If none qualifies, report each blocking owner, dependency, claim, or state.

For a normal claim, update child and parent atomically. Set child `Owner: Codex`, `Status: in_progress`, `Started:` if empty, `Updated:` in MYT, and claim activity with branch/worktree. Set parent row and top-level status `in_progress`, update its timestamp/activity, and never change parent ownership.

## Execute, block, and complete

Preserve unrelated dirt; stage only intended work. Ticket execution does not imply push, PR, deployment, messages, or other external actions. Record material progress/scope changes in `Updated:` and activity. Use `blocked` only for a concrete missing decision, dependency, permission, or external state; log transition back when cleared. Block the parent only when required blocked work leaves no eligible initiative progress.

Complete only after acceptance, validation, and changed paths are recorded. Set child `done`, `Updated:`, `Completed:`, and activity; synchronize parent row, completed history, timestamp, and activity. Mark parent done only when all required children and initiative validation/closeout pass.

When authorized publish/review includes an existing tracked child/parent, keep integration state open through a clean implementation head; close in a metadata commit, push, re-request review, and require clean current-head review of that closeout commit. An untracked isolated edit creates no project artifacts and skips ticket metadata closeout.

## Publish and review only when authorized

Inspect scope/staging; confirm base/head; run focused and broad checks; push with upstream; default to a draft PR. Use body sections `Summary`, `Scope`, `Review focus`, `Out of scope`, `Validation`. Prioritize correctness, security, regressions, contracts, migrations, and missing tests; de-prioritize style-only, speculative, unrelated, or tool-enforced churn without hiding defects. The review request is this exact standalone comment:

```text
@codex review
```

Cache PR number, branch, and `headRefOid`. Query GraphQL `reviewThreads(first: 100)` for `isResolved`, `isOutdated`, path, line, comments, review commits, and `merged`; flat comments are supplemental only. A Codex review older than `headRefOid` is never clean.

Classify unresolved, non-outdated threads as actionable or duplicate, already-fixed, outdated, style-only, speculative, unrelated, or contradicted. Fix actionable defects narrowly; add a failing regression first for behavioral defects. Give non-actionable feedback one evidence disposition, not blind churn. Resolve only after verified fix/disposition.

Before the initial review request and after every fix, run required validation, commit if needed, push, and record the pushed OID. Re-query until PR `headRefOid` equals that OID; only then resolve any materially addressed threads and post exact `@codex review`. Repeat. Stop only when latest Codex review equals refreshed current head, checks pass, zero unresolved non-outdated actionable threads remain, and all non-actionable threads are dispositioned. Never auto-merge.

If another actor merges early with tracked state open, create a metadata-only follow-up branch/draft PR and run the same loop. If permissions prevent it, set the integration child `blocked`; block the parent only if no other eligible initiative progress remains; record/report the concrete permission blocker; never mark done.

For an acceptance-breaking finding after completion, set child, parent row, and parent status `in_progress`; preserve prior child/parent completion timestamps in their activity logs; clear current `Completed:` values and remove the child from parent completed history. Fix, then close again with new timestamps. Do not reopen for non-actionable or non-acceptance findings.

Delete any asynchronous PR monitor at terminal state, PR closure, or replacement.

## Red flags

- Skipped claim/dependency rules, stolen ownership, or false resume claim
- Premature done or inconsistent parent state
- Blind nitpick churn or hidden valid defects
- Resolving/re-pinging before pushed OID becomes PR head
- Stale-head stop, auto-merge, or unauthorized external action
- Personal installation of this repo-owned skill
