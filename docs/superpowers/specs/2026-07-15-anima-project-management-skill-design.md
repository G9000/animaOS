# Anima Project Management Skill Design

**Date:** 2026-07-15
**Status:** Approved for implementation planning
**Scope:** Repo-owned Codex skill and repository workflow integration

## Context

AnimaOS already separates product requirements, implementation sequencing, and executable work into `docs/prds/`, `docs/superpowers/plans/`, and `tickets/`. The repository also defines a ticket lifecycle in `docs/ops/prd-ticket-workflow.md` and a ticket shape in `tickets/TEMPLATE.md`. What is missing is a concise operational skill that makes agents use those artifacts consistently from initiative intake through ticket completion.

The existing personal `create-prd` and `update-todos` skills target the older `docs/prd/` and `scratchboard/` model. They are outside this repository and must remain untouched. The new skill is repository-owned, uses the current animaOS workflow, and is made mandatory through `AGENTS.md` rather than being installed into a personal skill directory.

## Goals

1. Treat feature work as lightweight project management rather than isolated artifact generation.
2. Guide agents through initiative intake, planning, ticket creation, assignment, execution tracking, and closeout.
3. Make self-assignment explicit and prevent agents from stealing or bypassing claimed work.
4. Keep parent trackers synchronized with child ticket state.
5. Ensure completion always records acceptance, validation, changed paths, and project history.
6. Keep repository workflow documents as the source of truth instead of duplicating their full contents in the skill.
7. Publish requested work through a scoped PR and self-loop through Codex review until the latest head is genuinely clean.

## Non-Goals

- Installing or updating anything under a user's personal Codex or agent skill directories.
- Integrating Jira, Linear, GitHub Projects, calendars, staffing, estimates, or capacity planning.
- Inferring a push, PR, review ping, feedback mutation, or monitor from local implementation or from a narrower external-action request.
- Merging a pull request unless the user separately and explicitly requests the merge.
- Replacing `AGENTS.md`, `docs/ops/prd-ticket-workflow.md`, or `tickets/TEMPLATE.md` with skill-local copies.
- Forcing every small bug fix or documentation edit to become a new initiative when the user did not request project tracking.
- Implementing product features directly from an unapproved idea without the repository's planning gates.

## Selected Approach

Create a thin repo-owned process skill named `anima-project-management` at:

```text
.codex-skill-staging/anima-project-management/
  SKILL.md
  agents/openai.yaml
```

The skill routes work into the correct lifecycle mode, reads the current repository guidance, and enforces high-risk state transitions. It contains no generator script, template copy, README, or personal-install step. Mechanical ticket consistency remains the responsibility of the repository validator designed separately.

## Trigger Contract

The skill should trigger when an agent is asked to:

- start, define, revise, or report the status of an animaOS initiative or feature;
- create or revise a PRD, implementation plan, parent tracker, or child tickets;
- claim, assign, resume, block, complete, or choose the next ticket;
- execute work identified by a ticket ID;
- close an initiative or reconcile parent and child project state.
- publish a branch, open a PR, request Codex review, address review feedback, or monitor a PR until clean.

It should not trigger for general code explanation or diagnosis-only requests. An isolated change that has no initiative or ticket does not trigger planning or ticket bookkeeping, but an explicit request to publish that change or follow its PR review does trigger only the publish-and-review mode. Do not create fake project artifacts merely to open a PR.

## Repository Integration

Add a mandatory `Project Management Skill` section to `AGENTS.md`. It must direct agents to read `.codex-skill-staging/anima-project-management/SKILL.md` completely before taking project-management or ticket-lifecycle actions. The section must list the same triggers above and make clear that repo instructions override stale personal workflow skills.

`AGENTS.md` must also state that external authority is action-scoped, point to the canonical `Action-Scoped External Authority` matrix in `docs/ops/prd-ticket-workflow.md`, forbid escalation from a narrower request, and reserve merge for separate explicit authority.

The skill must begin by reading:

1. the repository `AGENTS.md`;
2. `docs/ops/prd-ticket-workflow.md`;
3. `tickets/TEMPLATE.md` when creating tickets;
4. the relevant existing PRD, plan, parent tracker, and child tickets for the requested initiative.

## Lifecycle Design

### 1. Intake and Discovery

Before creating artifacts, inspect existing PRDs, plans, specs, and ticket initiatives for the same product area. Reuse the established slug and identifiers where appropriate. Do not create a duplicate initiative merely because the user's wording changed.

Clarify only decisions that materially change scope. Respect active design or planning approval gates. If the request is status-only, remain read-only.

### 2. Planning Artifacts

For new work, use this order when applicable:

1. PRD when product scope is new or changing.
2. Design/spec when behavior or architecture needs approval.
3. Dated implementation plan when sequencing matters.
4. One parent tracker and ordered child tickets for executable units.

Each artifact has one responsibility. PRDs state product outcomes and boundaries; plans state implementation order and verification; tickets state a claimable unit of work. Cross-link artifacts without copying entire sections between them.

Planning an initiative does not automatically claim every child ticket. New executable child tickets start with `Owner: unassigned` and `Status: backlog` unless the user explicitly assigns them during creation.

### 3. Ticket Selection

When the user names a ticket, read that child and its parent first. When the user asks for the next ticket, read the parent and select the first child in documented order that:

- has `Status: backlog`;
- has `Owner: unassigned`;
- has every dependency in `done`, unless the user explicitly waives a dependency;
- is not already being implemented in another visible branch, worktree, or activity-log entry.

If no ticket is eligible, report the specific owner, dependency, or state preventing selection. Do not silently select a later or already claimed ticket.

### 4. Self-Assignment

Claim a ticket before implementation. Apply the child and parent bookkeeping as one logical change:

- set child `Owner: Codex`;
- set child `Status: in_progress`;
- set `Started:` only if empty;
- update `Updated:` using `YYYY-MM-DD HH:MM MYT`;
- append a child activity entry stating that Codex claimed the ticket and, when available, the branch or worktree;
- update the matching parent child-status row to `in_progress`;
- update parent `Updated:` and append a material parent activity entry.

Do not change the parent `Owner:` merely because one child was claimed. If another owner already holds the child, do not overwrite it unless the user explicitly authorizes reassignment; record any authorized reassignment in the activity log.

### 5. Execution and Progress

After claiming, follow the ticket's acceptance criteria and the repository's relevant implementation workflows. Preserve unrelated dirty-tree changes. Use an isolated worktree when requested or when the approved execution plan requires it. Do not broaden external actions: ticket execution alone does not authorize a push, pull request, deployment, or message.

Update `Updated:` and the activity log for material progress, scope changes, or newly discovered blockers. Use `blocked` only for a concrete missing decision, dependency, permission, or external-state condition. When a blocker clears, return the ticket to `in_progress` and record the transition.

### 6. Completion and Closeout

Do not mark a ticket `done` until its acceptance conditions are met and validation is recorded. On completion:

- set child `Status: done`, `Updated:`, and `Completed:`;
- record validation commands or checks, changed paths, and residual notes;
- append the child completion activity entry;
- set the parent child-status row to `done`;
- add the child to the parent's completed-ticket history if not already present;
- update parent `Updated:` and activity log.

The parent becomes `in_progress` when at least one child has started and incomplete work remains. It becomes `done` only when every required child is `done` and initiative-level validation or closeout requirements are satisfied. At that transition, set parent `Updated:` and parent `Completed:` to the same closeout timestamp and log it. Validate this closeout transition without imposing a repo-wide backfill rule on historical `done` tickets. A parent is `blocked` only when the initiative has no eligible progress because its remaining required work is blocked.

When the same user request includes PR publication and review follow-through, record implementation validation but keep the child and parent `in_progress` until the review loop reaches a clean current head. Final ticket closeout then becomes a small project-metadata commit inside the PR; push it, re-request review, and continue the loop until that final head is also clean. This prevents acceptance-breaking review findings from arriving after the project was declared complete.

This two-phase ticket closeout applies only when an existing tracked child and parent are in scope. Publishing or reviewing an untracked isolated edit must not create project artifacts and skips ticket-metadata closeout.

### 7. Publish and Codex Review Loop

External authority is action-scoped. A broader explicit request authorizes actions it clearly encompasses, but a narrower request never escalates. The canonical workflow owns the exact matrix:

| Explicit request | Authorized scope |
| --- | --- |
| Local implementation or commit | Local edits, tests, validation, and commits only; no push, PR, comments, or monitor |
| `push` | Scoped validation/commit prerequisites and branch push only |
| Open/update PR | Needed scoped commit/push and PR creation/update; no review ping or monitor |
| Request Codex review | Exact ping and read-only review-state checks; no fixes unless separately requested |
| Address feedback | Scoped reads, fixes/tests/commits/pushes/replies/resolution; re-ping only when requested or clearly included |
| Monitor until clean/full follow-through | Full thread-aware read/fix/reply/resolve/re-ping loop until clean |
| Merge | Separate explicit authority always required |

This matrix applies to both tracked work and isolated edits. Never use ticket execution or one narrow external action to infer another.

When the authorized action scope includes publication, perform only its allowed preparation:

1. Inspect the intended diff and stage only in-scope files.
2. Confirm the branch and base branch, especially for stacked work.
3. Run the required focused checks and broader repository validation.
4. Update relevant ticket validation and project state honestly.
5. Confirm GitHub CLI authentication, push with upstream tracking when authorized, and open a draft PR only when authorized unless the user explicitly asks for a ready PR.

The PR body must contain `Summary`, `Scope`, `Review focus`, `Out of scope`, and `Validation` sections. The review-focus contract should ask for actionable correctness, security, regression, contract, migration, and missing-test findings. It should explicitly de-prioritize style-only preferences, speculative redesign, unrelated refactors, and suggestions already enforced mechanically by repository tooling. This focuses review without suppressing real defects.

After an authorized push, record the pushed OID and re-query until `headRefOid` equals it. Only when review-request authority also exists, post the review request as the exact standalone comment:

```text
@codex review
```

Under monitor-until-clean/full-follow-through authority, self-loop instead of returning immediately:

1. Cache the PR number, branch, and current `headRefOid`.
2. Query thread-aware `reviewThreads(first: 100)` with `isResolved`, `isOutdated`, file and line anchors, comment metadata, latest Codex review commit, and PR `merged` state. Request `pageInfo { hasNextPage endCursor }` and cursor-paginate every `reviewThreads`, `reviews`, and per-thread `comments` connection used for classification or stopping until all pages are consumed. Flat comments are supplemental only.
3. If the latest Codex review is older than the current `headRefOid`, keep monitoring; do not declare the PR clean merely because no thread is currently visible.
4. Cluster unresolved, non-outdated threads and classify them by evidence:
   - actionable: correctness, security, behavior, compatibility, contract, test, or documentation defects within the PR scope;
   - non-actionable: duplicates, already-fixed or outdated observations, style-only preference, speculative redesign, unrelated scope expansion, or claims contradicted by current code and tests.
5. Fix every actionable thread narrowly. Add a failing regression first for behavioral defects, run focused validation, then broader validation appropriate to the changed surface.
6. Do not change code merely to satisfy a non-actionable nitpick. Reply once with a concise evidence-based disposition. Resolve a thread only after its concern has been materially addressed by a verified fix or a sound disposition; never hide unresolved valid feedback.
7. Commit and push authorized review fixes, record the pushed OID, and re-query until the PR `headRefOid` equals that OID. Only then resolve materially addressed threads. Post `@codex review` again only when explicitly requested, clearly included in address-feedback authority, or authorized by monitor-until-clean/full follow-through.
8. Repeat from the thread-aware read for the new head.

The clean stopping rule is strict: stop only when every required connection has been fully paginated, the latest Codex review targets the current `headRefOid`, all checks required for the changed surface pass, and there are zero unresolved non-outdated actionable threads. Any non-actionable thread must already have an evidence-based disposition rather than being ignored. If any page remains or pagination fails, fail closed and never declare clean. A merged PR is also terminal only when no tracked ticket remains open or the reviewed merged head already contains the required ticket and parent closeout.

If another actor merges the implementation PR while its tracked ticket is still `in_progress`, do not strand project state or modify the merged PR. Create a metadata-only follow-up branch and draft PR without a fresh prompt only when the original explicit request clearly included full publication plus project closeout or review follow-through. Otherwise set the integration child to `blocked` for missing authority, block the parent only when no other eligible progress remains, record/report the authority gap, and request fresh authority. If authority exists but repository permissions prevent the follow-up, record a distinct permission blocker and apply the same blocking rule. Neither blocker permits `done`, and follow-up authority still does not authorize merge.

If an asynchronous monitor or heartbeat is created for a PR loop, delete it when that PR reaches its applicable stopping rule or closes, including before replacing it with a follow-up metadata PR monitor. Do not merge unless separately asked.

If review follow-through starts after a ticket was already marked `done` and Codex finds an acceptance-breaking defect, apply the owner gate first. A Codex-owned completed child may enter the documented reopen. A child owned by anyone else requires explicit user-authorized reassignment logged before any lifecycle mutation or fix execution; without it, leave project state untouched and report the conflict. After the gate, reopen consistently:

- set the child to `in_progress` and clear its current `Completed:` field;
- preserve the earlier completion timestamp in the child reopening activity entry;
- set the matching parent child-status row to `in_progress`;
- remove the child from the parent's completed-ticket list while it is reopened, preserving the earlier completion in the parent activity log;
- return the parent to `in_progress`, clear the parent's `Completed:` field if it was set, and preserve that timestamp in the parent activity log.

Fix and validate normally, then perform child and parent closeout again only after the current head is clean. Re-add the child to completed-ticket history with the new completion timestamp. A non-actionable observation or a finding that does not invalidate ticket acceptance does not reopen the ticket.

## Conflict and Error Handling

- Missing parent or malformed ticket: stop lifecycle mutation, identify the missing contract, and repair it only if the user authorized project maintenance.
- Duplicate artifact or ID: update or reuse the existing artifact; do not create a competing source of truth.
- Owner conflict: do not claim; surface the current owner and ticket state.
- Dependency conflict: do not claim unless the user explicitly waives it and the waiver is recorded.
- Authority gap during required metadata closeout: block the integration child, block the parent only when no other progress is eligible, record/report the missing authority, and request it; never mark the project done.
- Permission conflict after authority exists: record it distinctly, apply the same blocking rule, and never mark the project done.
- Dirty worktree: preserve unrelated changes and use a safe isolated path when necessary.
- Failed validation: leave the ticket `in_progress` or set it `blocked` when a genuine external blocker exists; never mark it `done` for convenience.
- Stale PR review: compare the latest review commit to `headRefOid` and continue monitoring instead of reporting a false clean state.
- Incomplete review pagination: fail closed until every `pageInfo.hasNextPage` is false; never infer clean from a partial connection.
- Review nitpick: decline it with evidence when it is style-only or out of scope; do not create churn just to make a thread disappear.

## Skill Contents

`SKILL.md` must stay concise and act as a high-risk checklist over the canonical workflow. It must contain:

- YAML frontmatter with only `name` and a trigger-focused `description`;
- a short overview and mode-selection guide;
- the mandatory source files to read;
- pointers to the named canonical sections for exact state tables, transactions, action-scoped authority, pagination, and closeout;
- the ownership, parent-completion, authority, pagination-fail-closed, and current-head guards;
- the exact Codex review ping, actionable-feedback filter, and clean stopping rule;
- a quick-reference table and common failure modes;
- explicit red flags against skipping claims, stealing ownership, completing without validation, blindly implementing nitpicks, or stopping review on a stale head.

`agents/openai.yaml` must contain only generated `display_name`, `short_description`, and `default_prompt` values based on the final skill. No bundled references are needed because the canonical repository documents already exist.

## Test-Driven Skill Development

The skill must be created using RED-GREEN-REFACTOR for process documentation.

### RED: Baseline Without the Skill

Run fresh subagents against isolated disposable repository fixtures without exposing the intended skill. Capture their actions and rationalizations for at least these scenarios:

1. A new feature request combines time pressure with a demand to start coding immediately, testing whether the agent skips PRD, plan, or ticket gates.
2. A `do the next ticket` request includes an earlier ticket with an unfinished dependency and a later ticket owned by another agent, testing whether the agent claims incorrectly.
3. A `mark this done` request includes incomplete validation and unrelated dirty changes, testing whether the agent completes prematurely or damages user work.
4. A simple diagnosis-only or isolated-edit request tests that the workflow does not create unnecessary project artifacts.
5. A publish-and-monitor request includes an old clean review, one real defect, and one style-only suggestion, testing whether the agent fixes the defect, rejects churn, re-requests review, and waits for a clean review on the new head.

### GREEN: Minimal Skill

Initialize the skill with the official skill scaffold, write only the instructions needed to correct observed baseline failures, generate `agents/openai.yaml`, and run the official quick validator.

### REFACTOR: Forward Testing

Rerun the same scenarios with fresh subagents explicitly using the repo-owned skill. Add only guardrails needed to close observed loopholes, then rerun until the behavior is reliable. Keep fixtures under an ignored temporary directory and remove them after evaluation. Forward tests must not mutate live tickets, personal skill directories, external services, or production systems.

## Validation

Required validation includes:

- official `quick_validate.py` against the skill folder;
- frontmatter and `agents/openai.yaml` inspection;
- word-count review for concise skill loading;
- baseline and forward-test evidence for all five scenarios;
- baseline and forward-test evidence for the publish-and-review scenario;
- when the user explicitly authorizes publication, the actual PR loop as end-to-end validation; otherwise record it as not run rather than performing unauthorized external actions;
- `rg` verification that `AGENTS.md` points to the exact committed skill path;
- repository checks and build required by `AGENTS.md` after integration;
- final diff inspection confirming no personal skill paths or unrelated production source files changed.

## Expected Changed Paths

- `.codex-skill-staging/anima-project-management/SKILL.md`
- `.codex-skill-staging/anima-project-management/agents/openai.yaml`
- `AGENTS.md`
- `docs/ops/prd-ticket-workflow.md` only for clarifications required by the skill contract
- `tickets/TEMPLATE.md` only if self-assignment fields need clarification
- the implementation plan for this approved design
