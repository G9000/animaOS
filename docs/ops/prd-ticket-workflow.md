# PRD, Plan, and Ticket Workflow

This repository uses three planning artifacts for new work:

1. `docs/prds/` for product requirements and version scope
2. `docs/superpowers/plans/` for implementation sequencing
3. `tickets/` for local issue-style units of work

Agents should not collapse these into one file type.

## Artifact Roles

### PRD

Use a PRD when defining what a feature/version should deliver.

Path pattern:

- docs/prds/<domain>/<name>.md
- docs/prds/<name>.md for top-level umbrella docs

PRDs answer:

- what this version delivers
- what users see
- rules and constraints
- success metrics
- what is explicitly out of scope

PRDs are not execution checklists.

### Implementation Plan

Use a plan when the work is approved enough to sequence engineering tasks.

Path pattern:

- docs/superpowers/plans/YYYY-MM-DD-<slug>.md

Plans answer:

- what files/services change
- task order
- test and verification steps
- migration or rollout order

Plans are execution maps, not ticket queues.

### Tickets

Use `tickets/` for issue-style units that can be claimed, progressed, blocked, and completed over time.

Tickets answer:

- what single unit of work exists
- what parent initiative it belongs to
- who or what claimed it
- whether it is blocked or active
- what changed
- how completion was validated

## Required Lifecycle

When a user asks to create a new initiative, the agent should create artifacts in this order when applicable:

1. PRD if product scope is not already defined
2. Plan if implementation is large enough to need sequencing
3. Parent ticket for the initiative
4. Child tickets for concrete work units

When a user asks to execute a ticket, the agent should:

1. open the target ticket file
2. set `Status: in_progress`
3. set `Started:` if empty
4. update `Updated:`
5. append an `Activity Log` entry with timestamp and action
6. do the implementation work
7. record validation and changed paths
8. set `Status: done` or `Status: blocked`
9. set `Completed:` when done
10. update `Updated:` again

Agents should not silently work a ticket without claiming it in the file first unless the user explicitly asks for a draft-only change.

## Parent Ticket Model

Each initiative folder under `tickets/` should contain one parent tracker ticket plus child tickets.

Recommended parent ticket id:

- `<prefix>-000`

Parent ticket responsibilities:

- state the initiative goal
- list child tickets in order
- track child status in one table
- maintain a completed-ticket section
- summarize overall progress and blockers

## Ticket Status Model

Allowed values:

- `backlog`
- `in_progress`
- `blocked`
- `done`

Meaning:

- `backlog`: not started
- `in_progress`: currently claimed by an agent or human
- `blocked`: cannot progress without a missing decision, dependency, or external change
- `done`: acceptance met and validation recorded

## Timestamp Rules

Use one timestamp format everywhere in ticket files:

- `YYYY-MM-DD HH:MM MYT`

Required fields:

- `Created:`
- `Updated:`
- `Started:`
- `Completed:`

Rules:

- `Created` is set when the ticket file is created
- `Updated` changes on every material edit
- `Started` is set only once, the first time work begins
- `Completed` is set only when status becomes `done`

## Required Ticket Sections

Every ticket should contain:

- title with ticket id
- metadata block
- goal
- deliverables
- acceptance
- activity log
- validation

Recommended metadata fields:

- `Status:`
- `Priority:`
- `Scope:`
- `Parent:`
- `Depends on:`
- `Owner:`
- `PRD:`
- `Plan:`
- `Created:`
- `Updated:`
- `Started:`
- `Completed:`

## Completion Contract

A ticket is not complete just because code was written.

Before marking `done`, the ticket should include:

- final validation commands or checks
- changed file paths
- any follow-up risks or notes

If part of the acceptance is still unmet, leave the ticket `in_progress` or `blocked`.

## Agent Pickup Rules

If the user says "do the next ticket" or similar, the agent should:

1. read the parent ticket first
2. choose the first child ticket in initiative order whose status is `backlog`
3. confirm dependencies are `done` or intentionally waived by the user
4. claim it by updating the child ticket file
5. update the parent tracker status table
6. execute and update the child ticket before final response

If the user names a specific ticket, use that ticket directly.

## Parent Updates on Completion

When a child ticket changes status, also update the parent ticket:

1. refresh the child status row
2. append to `Completed Tickets` if the child became `done`
3. update parent `Updated:`
4. append a parent `Activity Log` entry if the change is material

## File Templates

Use [tickets/TEMPLATE.md](../../tickets/TEMPLATE.md) for new child ticket files.

When creating a new initiative folder under `tickets/`, also create:

- `tickets/<initiative>/README.md` with short folder purpose
- `tickets/<initiative>/<prefix>-000-<initiative>.md` as the parent tracker
- child ticket files using the template

## Example Activity Log

```markdown
## Activity Log

- 2026-06-26 17:40 MYT - Claimed by Codex, set status to `in_progress`.
- 2026-06-26 18:05 MYT - Added auth package boundary and updated route imports.
- 2026-06-26 18:20 MYT - Ran focused auth tests and recorded results.
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
  - Desktop unlock flow kept on compatibility shim.
```
