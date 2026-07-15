---
name: anima-project-management
description: Use when animaOS initiative or feature work involves status, definition, revision, PRD, plan, tickets, claim, assignment, resume, block, completion, next-ticket selection, ticket-ID execution, parent-child reconciliation, or explicitly requested publish, PR, Codex review, or monitor-until-clean; exclude explanation, diagnosis-only, and isolated edits unless publish or review is explicitly requested.
---

# Anima Project Management

## Canonical sources and modes

Read `AGENTS.md` and `docs/ops/prd-ticket-workflow.md` completely. The workflow document is authoritative; this skill is a high-risk checklist, not a substitute. Read `tickets/TEMPLATE.md` before creating tickets and the relevant PRD, design/spec, dated plan, parent, and children before changing state.

| Mode | Boundary |
| --- | --- |
| Status-only | Read-only |
| Planning | Discover/reuse sources; preserve approval gates |
| Ticket execution | Use the canonical selection, transition, claim, blocker, and completion sections |
| Publish/review | Use the canonical action-scope, pagination, review, and closeout sections |

Diagnosis-only is read-only. Isolated edits create no project artifacts.

## High-risk checklist

### Plan and select

- Search existing PRDs/specs/plans/initiatives before creating; new executable children are backlog/unassigned unless explicitly assigned.
- For next-ticket selection, use the canonical ordered eligibility rule and logged dependency-waiver rule. Report when none qualifies.
- For named tickets, `done` precedence applies. Reject malformed state/owner combinations and another owner's work unless the user explicitly authorizes and logs reassignment.

### Mutate ticket state

- Use the exact `Ticket Selection and Legal Transitions`, `Claim or Assigned-Start Transaction`, `Progress, Blockers, and Clearance`, and `Completion and Parent Closeout` sections. Synchronize child and parent atomically; never change parent ownership.
- Preserve unrelated dirt. Ticket execution does not authorize push, PR, deployment, comments, monitoring, or merge.
- Complete only with acceptance, validation, changed paths, and history. When the parent becomes `done`, set parent `Updated:` and `Completed:` to the closeout timestamp and log it.

### Scope external authority

- Read `Action-Scoped External Authority` before any external action. Local work grants none. A broader explicit request covers clearly encompassed actions; never escalate a narrower push, PR, review, feedback, or monitoring request. Merge always needs separate explicit authority.
- For authorized PR creation or updates, inspect and stage scope, confirm base and head, run focused and broad validation, default to draft, and use `Summary`, `Scope`, `Review focus`, `Out of scope`, and `Validation` body sections.
- Request-review authority permits exact standalone `@codex review` plus read-only state checks, not fixes. Address-feedback authority permits scoped fixes/replies/resolution but re-ping only when included. Monitor-until-clean/full follow-through permits the complete loop.

### Review without stale or partial state

- After each authorized push, record its OID and wait for PR `headRefOid` to match before later review mutations or pings.
- Fully paginate every `reviewThreads`, `reviews`, and per-thread `comments` connection using `pageInfo { hasNextPage endCursor }`. Fail closed if any page remains or pagination fails.
- Under authorized feedback handling, fix actionable defects narrowly, add a failing regression first for behavioral defects, and disposition non-actionable feedback with evidence. Never hide valid feedback.
- Under full follow-through, stop only when the latest Codex review equals the refreshed head, checks pass, zero unresolved non-outdated actionable threads remain, and all non-actionable threads are dispositioned.

### Close or recover tracked work

- Keep tracked integration state open through a clean implementation head, then review the metadata closeout head under the same guard.
- Before acceptance-breaking reopen, reapply the owner gate. Reassign and log a non-Codex owner explicitly before mutating state or executing fixes; otherwise do nothing and report.
- After early merge, create a metadata-only follow-up without a fresh prompt only when the original request clearly included full publication plus project closeout/follow-through. Otherwise block for missing authority and request it. Record repository permission failure separately. Block the parent only when no other work is eligible; never mark blocked closeout `done`.
- Delete asynchronous monitors at terminal state, closure, or replacement.

## Red flags

- Stolen ownership, false claim/resume, skipped dependency, or partial parent sync
- Premature completion, missing parent `Completed:`, or ownership-unsafe reopen
- Narrow authority expanded into PR, ping, fixes, re-ping, monitoring, or merge
- Incomplete pagination, stale-head stop, unresolved valid feedback, or blind nitpick churn
- Unauthorized early-merge follow-up or conflated authority/permission blocker
- Personal installation of this repo-owned skill
