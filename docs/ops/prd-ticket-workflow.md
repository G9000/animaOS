# PRD, Plan, and Ticket Workflow

This document is the canonical repository source for planning artifacts, ticket state, and explicitly authorized publication/review. The repo-owned project-management skill routes agents into this workflow; it does not replace this contract.

## Artifact Roles

Keep product scope, implementation sequencing, and executable work separate:

1. `docs/prds/` contains product requirements and version scope.
2. `docs/superpowers/specs/` contains behavior or architecture that requires approval.
3. `docs/superpowers/plans/` contains dated implementation sequencing.
4. `tickets/` contains issue-style units that can be assigned, progressed, blocked, and completed.

### PRD

Use a PRD when defining what a feature or version should deliver.

Path patterns:

- `docs/prds/<domain>/<name>.md`
- `docs/prds/<name>.md` for a top-level umbrella document

A PRD states user-visible outcomes, constraints, success measures, and explicit non-goals. It is not an execution checklist.

### Design/Spec

Use a design/spec when behavior, architecture, or a material trade-off needs approval before implementation planning. Preserve its approval gate; urgency does not imply approval.

Path pattern:

- `docs/superpowers/specs/YYYY-MM-DD-<slug>-design.md`

### Implementation Plan

Use a plan when approved work is large enough to require engineering sequence, file-level scope, verification, migration, or rollout steps.

Path pattern:

- `docs/superpowers/plans/YYYY-MM-DD-<slug>.md`

A plan is an execution map, not a ticket queue.

### Tickets

Use `tickets/` for single issue-style units. A ticket records its parent, dependencies, owner, state, acceptance, activity, validation, and changed paths.

## Intake, Discovery, and Planning

Before creating or revising artifacts, search existing PRDs, designs/specs, plans, parent trackers, and child tickets for the same initiative. Reuse established sources, slugs, and identifiers instead of creating a duplicate because the request uses different wording. Read the relevant artifacts completely and respect outstanding user-approval gates.

For new or materially changed scope, use this order when applicable:

1. PRD when product scope is new or changing.
2. Design/spec when behavior or architecture needs approval.
3. Dated implementation plan when sequencing matters.
4. One parent tracker plus ordered child tickets for executable units.

Cross-link these artifacts without collapsing or copying their responsibilities. Planning does not auto-claim an initiative: every new executable child starts with `Status: backlog` and `Owner: unassigned` unless the user explicitly assigns it during creation.

Planning-mode revisions to a `backlog`/`unassigned` ticket's goal, deliverables, or acceptance may occur without a claim only while both `Status:` and `Owner:` remain unchanged. Update the ticket's `Updated:` field and activity log, add parent planning activity when the revision is material, and do not begin implementation under this exception.

Status-only requests are read-only. Explanation and diagnosis-only work does not create or mutate project artifacts. An isolated edit does not receive fake project artifacts merely because it may later be published.

## Initiative and Ticket Shape

Each initiative folder under `tickets/` should contain:

- `tickets/<initiative>/README.md` with a short folder purpose;
- one `<PREFIX>-000` parent tracker;
- ordered child tickets created from [tickets/TEMPLATE.md](../../tickets/TEMPLATE.md).

The parent states the initiative goal, lists child order and dependencies, mirrors child status in one table, records completed-ticket history, and summarizes overall progress and blockers. Every child references the parent.

Required child metadata includes:

- `Status:`
- `Priority:`
- `Scope:`
- `Parent:`
- `Depends on:`
- `Owner:`
- relevant `PRD:`, `Spec:`, and `Plan:` links
- `Created:`, `Updated:`, `Started:`, and `Completed:`

Every ticket also contains a goal, deliverables, acceptance, activity log, and validation section.

`tickets/TEMPLATE.md` is the contract for new child metadata and must expose top-level `PRD:`, `Spec:`, and `Plan:` fields. The organization validator checks those fields on the template; it does not require historical tickets to backfill a missing `Spec:` field.

## Status and Timestamp Contract

The only legal ticket statuses are:

- `backlog`: not started;
- `in_progress`: actively owned and underway;
- `blocked`: unable to progress because of a concrete missing decision, dependency, permission, or external-state change;
- `done`: acceptance met and validation recorded.

Use `YYYY-MM-DD HH:MM MYT` for every ticket timestamp.

- `Created:` is set when the ticket file is created.
- `Updated:` changes on every material edit.
- `Started:` is set once, when work first begins, and is preserved on resume.
- `Completed:` is set only while the ticket is `done`; a documented acceptance-breaking reopen clears the current value while preserving the prior timestamp in history.

Every `in_progress` ticket has a non-`unassigned` owner, and a `backlog` parent cannot contain an `in_progress` or `blocked` child. These are malformed lifecycle states, not normalization shortcuts; correct them from recorded ownership and state evidence without inventing a claim.

Historical workstreams that already depend on `scratchboard/` may continue there until deliberately migrated. Do not silently rewrite historical artifacts during unrelated work; all new initiatives use the current PRD, design/spec, dated-plan, and ticket paths above.

## Ticket Selection and Legal Transitions

For a named ticket, read the child and parent before mutation. Apply state precedence and ownership exactly:

| Current child state | Legal action |
| --- | --- |
| Any `done`, regardless of owner | Reject mutation. Only the acceptance-breaking reopen routine below may change it. |
| `backlog` with `Owner: unassigned` | Claim through the transaction below. |
| `backlog` with `Owner: Codex` | Start through the transaction below; log a start, not a false ownership claim. |
| `in_progress` with `Owner: Codex` | Resume without changing `Started:` or logging a new claim. |
| `blocked` with `Owner: Codex` | Resume only after blocker clearance is verified and recorded in child and parent. |
| Any non-`done` state owned by someone else | Reject unless the user explicitly authorizes reassignment; log that reassignment before applying the new owner's legal transition. |
| Unlisted or malformed status/owner combination | Reject without lifecycle mutation and report the malformed contract. |

For a request to choose the next ticket, evaluate the parent child table in documented order and select the first child that satisfies all of these conditions:

- `Status: backlog`;
- `Owner: unassigned`;
- every dependency is `done`;
- no branch, worktree, or activity-log entry shows a visible existing claim.

A dependency may be bypassed only through explicit user authorization recorded as a waiver in the child and parent activity logs. If no child is eligible, do not improvise: report the specific owner, dependency, visible claim, malformed metadata, or state that prevents selection.

## Claim or Assigned-Start Transaction

Before any backlog start, recheck dependencies and visible claims. Treat the child and parent edits as one logical transaction:

1. For an unassigned child, set `Owner: Codex` and log that Codex claimed it. For a Codex-owned backlog child, preserve `Owner: Codex` and log that work started without claiming ownership again.
2. Set child `Status: in_progress`.
3. Set child `Started:` only if empty and update child `Updated:` in MYT.
4. Include the branch and worktree in child activity when available.
5. Set the matching parent child row to `in_progress`.
6. Set top-level parent `Status: in_progress` when executable initiative work is active, update parent `Updated:`, and append a material parent activity entry.
7. Preserve the parent `Owner:` exactly; child assignment never reassigns the parent.

If any required part cannot be recorded, do not start implementation under a partially claimed state.

## Progress, Blockers, and Clearance

Preserve unrelated dirty work and stage only intended files. Update the child `Updated:` and activity log for material progress or scope changes, and synchronize material state changes to the parent.

On first discovery of a concrete blocker:

1. Set child `Status: blocked`, update child `Updated:`, and append an activity entry naming the blocker and required clearance.
2. Set the parent child row to `blocked`, update parent `Updated:`, and append material parent activity.
3. Set top-level parent `Status: blocked` only when no other initiative work is eligible to progress; otherwise keep it `in_progress`.

When the blocker clears, record the evidence of clearance in both child and parent, set the child and parent row to `in_progress`, update both timestamps, and restore top-level parent `Status: in_progress` when eligible work resumes. Preserve the original `Started:` value and all blocker history.

Ticket execution does not authorize pushing, opening a PR, deploying, sending messages, merging, or any other external action.

## Completion and Parent Closeout

A child is complete only when all acceptance conditions are met and the ticket records:

- focused and required broader validation commands or checks and their results;
- every changed path;
- residual risks, follow-ups, or an explicit `none`.

Then close child and parent state as one logical update:

1. Set child `Status: done`, `Updated:`, and `Completed:` and append a completion activity entry.
2. Set the parent child row to `done`.
3. Add the child and completion timestamp to parent completed-ticket history without duplicating it.
4. Update parent `Updated:` and append material parent activity.

The top-level parent remains `in_progress` while required children or initiative closeout remain. Set it to `done` only when every required child is `done` and initiative-level validation and closeout have passed. On that transition, set parent `Updated:` and parent `Completed:` to the same closeout timestamp and log the transition. Preserve parent ownership throughout. Validate this at transition time; do not mass-backfill historical `done` tickets or add a repository-wide reverse rule that every `done` ticket must already have `Completed:`.

## Scoped Publication and Review

External authority is action-scoped. Actions clearly encompassed by a broader explicit request count as authorized prerequisites or follow-through; never escalate a narrower request. Merge always requires separate explicit authority.

### Action-Scoped External Authority

| Explicit request | Authorized actions | Not authorized by that request alone |
| --- | --- | --- |
| Local implementation or commit | Local edits, tests, validation, and scoped commits | Push, PR creation/update, comments, review-state monitoring, or merge |
| `push` | Scoped validation/commit prerequisites and branch push | PR creation/update, review ping, feedback handling, monitoring, or merge |
| Open or update PR | Needed scoped validation/commit/push plus PR creation or update | `@codex review`, review monitoring, feedback fixes, or merge |
| Request Codex review | Exact standalone review ping and read-only review-state checks on that PR | Feedback fixes, commits, pushes, replies, thread resolution, re-ping, monitoring-until-clean, or merge |
| Address feedback | Thread-aware reads plus in-scope fixes, tests, commits, pushes, replies, and resolution for that PR | Re-ping or monitor-until-clean unless explicitly requested or clearly included; merge |
| Monitor until clean or full review follow-through | Thread-aware reads, actionable fixes, tests, commits, pushes, replies, resolutions, and re-pings until the clean stopping rule | Merge |
| Merge | Merge only when separately and explicitly authorized | Any unrelated external action |

Publishing an untracked isolated edit does not create planning artifacts or tickets and does not require tracked-work metadata closeout.

### Prepare and Publish

Apply only the preparation steps authorized by the matrix. When the request includes a new or updated PR:

1. Inspect the intended diff and staging area; stage only in-scope files.
2. Confirm the base branch, head branch, and commit relationship, especially for stacked work.
3. Run focused validation plus the broader checks required for the changed surface.
4. Commit and push only when authorized, use upstream tracking when needed, and record any pushed commit OID.
5. Open or update a draft PR by default unless the user explicitly requests a ready PR.
6. After any push, re-query the PR until its `headRefOid` equals the recorded pushed OID before performing a later authorized review action.

The PR body must contain these sections: `Summary`, `Scope`, `Review focus`, `Out of scope`, and `Validation`. Ask reviewers to prioritize actionable correctness, security, regressions, contracts, migrations, and missing tests. De-prioritize style-only preference, speculative redesign, unrelated refactors, and churn already enforced by tooling, without hiding or dismissing real defects.

When review-request authority exists, the Codex review request must be this exact standalone comment:

```text
@codex review
```

Do not auto-merge, and do not merge without separate explicit authorization.

### Read Current-Head Review State

Cache the PR number, branch, and current `headRefOid`. Query GraphQL with cursor pagination. Every `reviewThreads`, `reviews`, and per-thread `comments` connection used for classification or stopping must request `pageInfo { hasNextPage endCursor }`, advance its cursor, and continue until all pages are consumed. Retrieve, at minimum:

- PR `merged` and `headRefOid`;
- reviews with author, state, submitted time, and review commit OID;
- each thread's `isResolved`, `isOutdated`, path, line/original line, comments, and comment commit OIDs.

Flat PR/review comments are supplemental; they cannot replace thread-aware state. If any required connection still has `hasNextPage: true`, a cursor fails, or pagination cannot complete, fail closed: report the incomplete read and never declare the PR clean. The latest Codex review commit must equal the refreshed current `headRefOid`. An older review is stale even if it was clean and even if no current thread is visible.

### Classify, Fix, and Repeat

Review-request-only authority permits read-only checks and reporting, not lifecycle mutation or fixes. With address-feedback or full-follow-through authority, handle every unresolved, non-outdated thread:

1. Classify it as actionable or as duplicate, already-fixed, outdated, style-only, speculative, unrelated, or contradicted by current evidence.
2. Fix actionable defects narrowly. For a behavioral defect, add a failing regression first, then implement and verify the fix.
3. Give every non-actionable finding one concise evidence-based disposition. Never relabel or hide valid feedback merely to clear a thread.
4. Run focused and required broad validation, commit and push the scoped change, and record the pushed OID.
5. Re-query until PR `headRefOid` equals that pushed OID. Only then resolve threads whose concerns were materially addressed by a verified fix or sound disposition.
6. Re-ping only when explicitly requested, clearly included in address-feedback authority, or authorized by monitor-until-clean/full-follow-through. Under full follow-through, post exact standalone `@codex review` and repeat from a fresh, fully paginated read.

Stop only when all five conditions are true on the same refreshed head:

- every required review, review-thread, and comment page was consumed successfully;
- the latest Codex review commit equals current `headRefOid`;
- required checks pass;
- zero unresolved, non-outdated actionable threads remain;
- every non-actionable thread has an evidence-based disposition.

### Tracked-Work Two-Phase Closeout

When the authorized scope includes full publication/review follow-through for an existing tracked child and parent, keep the integration child and parent open through a clean implementation head. After that head meets the stopping rule:

1. record final evidence and close the child and parent metadata consistently;
2. commit and push that metadata closeout;
3. record the pushed closeout OID and wait for PR `headRefOid` to catch up;
4. post exact `@codex review` and apply the same stopping rule to the final closeout head.

If an actionable review finding invalidates acceptance after a ticket was closed, apply the owner gate before any lifecycle mutation or fix execution. A completed Codex-owned child may enter the documented reopen. If its owner is not Codex, require explicit user-authorized reassignment and log it first; otherwise leave child and parent untouched, report the ownership conflict, and request authority. After the gate, reopen consistently: set the child and matching parent row to `in_progress`, return top-level parent to `in_progress`, clear current child/parent `Completed:` values, remove the child from current parent completed history, and preserve every prior completion timestamp and history in child and parent activity logs. Fix and validate, then perform closeout again with new timestamps. Do not reopen for a non-actionable or non-acceptance finding.

### Early Merge, Permissions, and Monitor Cleanup

If another actor merges while tracked metadata remains open, create a metadata-only follow-up branch and draft PR without a fresh prompt only when the original explicit request clearly included full publication plus project closeout or review follow-through. Otherwise set the integration child to `blocked` for missing authority, set the parent `blocked` only when no other eligible initiative work remains, record/report the authority gap, and request fresh authority. If follow-up authority exists but repository permissions prevent the branch or PR, record a distinct permission blocker and apply the same child/parent blocking rule. An authority gap and a permission failure are different blockers; neither permits `done`.

Delete any asynchronous review monitor when its PR reaches the applicable terminal state, closes, or is replaced by a follow-up PR monitor.

## Example Activity Log

```markdown
## Activity Log

- 2026-06-26 17:40 MYT - Codex claimed the ticket on branch `codex/example` in worktree `.worktrees/example`; set child and parent row to `in_progress`.
- 2026-06-26 18:05 MYT - Added the scoped implementation and recorded material progress.
- 2026-06-26 18:20 MYT - Ran focused and broad validation and recorded results.
```

## Example Validation

```markdown
## Validation

- Commands:
  - `bun run test`
  - `bun run build`
- Changed paths:
  - `apps/server/src/anima_server/auth/__init__.py`
  - `apps/server/src/anima_server/api/routes/auth.py`
- Notes:
  - Desktop unlock flow remains on the compatibility shim.
```
