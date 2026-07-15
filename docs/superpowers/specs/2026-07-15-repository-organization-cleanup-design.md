# Repository Organization Cleanup Design

**Date:** 2026-07-15
**Status:** Approved for implementation planning
**Scope:** Repository metadata, documentation, workflow artifacts, and validation only

## Context

AnimaOS has grown into a polyglot monorepo with Bun, Nx, uv, and Cargo workspaces. The top-level `apps/` and `packages/` split remains useful, but the repository's documentation and workflow metadata no longer describe it consistently. The live tree contains six applications and eight shared packages, while the directory guide still references the removed `apps/api` project and old test and route counts. Repository history is split between `docs/audit/` and `docs/audits/`, ticket files use several status spellings outside the documented lifecycle, and `debug.log` is tracked.

At the same time, source-level hotspot refactors are already in progress on another pull request. This cleanup must improve repository navigation and guardrails without moving or editing production source code.

## Goals

1. Make the current app, package, workspace, and documentation layout easy to understand from canonical repository docs.
2. Use one documented ticket-status vocabulary and make the ticket index reflect active and completed initiatives accurately.
3. Remove tracked development noise and clearly mark legacy workflow areas.
4. Add a read-only repository validator so organizational drift is caught early.
5. Preserve existing source paths and avoid conflicts with in-progress refactoring work.

## Non-Goals

- Refactoring large or hotspot source files.
- Moving files under `apps/*/src`, `packages/*/src`, or Tauri Rust source directories.
- Changing API contracts, imports, package names, runtime behavior, or database schemas.
- Forcing every language project into Nx. Bun/package manifests, uv, Cargo, and Nx remain authoritative for their existing scopes.
- Moving completed ticket folders or rewriting historical activity logs.
- Changing the semantics of the existing root `build`, `lint`, or `test` commands.

## Selected Approach

Use a boundary-preserving staged cleanup. Stable source and ticket paths remain in place. Documentation, metadata, and validation are corrected around those paths. This provides most of the navigation benefit of a large reorganization without merge conflicts or broad path churn.

## Design

### 1. Canonical Repository Map

Update `docs/architecture/system/directory-structure.md` to describe the live top-level layout:

- all applications under `apps/`, including `anima-mod`, `animus`, `desktop`, `local-runtime-daemon`, `server`, and `site`;
- all shared packages under `packages/` and their language/runtime role;
- the purpose of `docs/`, `scripts/`, `tests/`, `third_party/`, `tickets/`, and the legacy `scratchboard/`;
- the division of responsibility between Bun workspaces, Nx project orchestration, the uv workspace, and the Cargo workspace;
- which directories are runtime-generated or machine-local and should not be committed.

The document must avoid volatile facts such as exact test, route, table, or tool counts. Those values age quickly and do not help explain directory ownership.

Update `AGENTS.md` where its project-structure summary omits current applications or packages. Add a short architecture/documentation link from the root `README.md` so a new contributor can reach the canonical map without searching.

### 2. Documentation Location Cleanup

Use `docs/audit/` as the canonical audit-history directory. Move the single tracked file currently under `docs/audits/` into an appropriate path under `docs/audit/`, update every tracked reference to the old path, and remove the empty plural directory.

This is the only documentation move in scope. Dated specs and plans retain their current paths because they are historical artifacts and their naming already follows repository conventions.

### 3. Root and Legacy-Workflow Hygiene

Add `/debug.log` to `.gitignore` and remove the tracked file from the Git index. No other local runtime directory is moved or deleted.

Add `scratchboard/README.md` explaining that the directory exists only for older workstreams already tied to it. New product work must follow the canonical PRD to plan to ticket flow. Existing scratchboard files stay untouched so historical references remain valid.

### 4. Ticket Metadata and Index

Normalize ticket metadata to the four statuses documented in `docs/ops/prd-ticket-workflow.md`:

| Existing value | Canonical value |
| --- | --- |
| `backlog` | `backlog` |
| `todo` | `backlog` |
| `in_progress` | `in_progress` |
| `in-review` | `in_progress`, or `done` when that ticket already has a non-empty `Completed:` field |
| `in_review` | `in_progress`, or `done` when that ticket already has a non-empty `Completed:` field |
| `blocked` | `blocked` |
| `done` | `done` |

Authoritative current-state locations are the top metadata `Status:` line in each ticket and the child-status column in parent tracker tables. Normalize ticket metadata first, then synchronize each parent table row from the corresponding child ticket. Historical prose, activity logs, and completed-ticket narratives are records of what happened and must not be rewritten merely because they contain a legacy term.

This is primarily a vocabulary migration, not a new lifecycle event. It does not append activity entries or rewrite existing timestamps. One consistency rule applies: a non-empty `Completed:` field is durable evidence that the ticket reached `done`, so its current metadata status and parent-table row must become `done`. This repairs the known Agent Runtime Hardening contradiction without erasing its completion timestamp. A ticket without a non-empty `Completed:` field follows the mechanical mapping above; child completion is not inferred from Git history or prose.

Rebuild `tickets/README.md` as a concise initiative index derived from normalized parent tracker metadata. A parent with `Status: done` is completed; every other canonical parent status is active. A non-empty parent `Completed:` field must first be reconciled to `Status: done`. Child states do not automatically complete a parent. An initiative without a conforming parent tracker is listed separately as legacy or unclassified instead of being guessed. Ticket folders stay where they are to preserve links.

### 5. Read-Only Repository Validator

Add `scripts/check-repo-organization.ts` and expose it through a nonbreaking root command named `check:repo`.

The validator performs these checks:

1. Every authoritative ticket metadata status and parent child-status table cell belongs to `backlog`, `in_progress`, `blocked`, or `done`.
2. Every ticket with a non-empty `Completed:` field has `Status: done`.
3. Every direct child of `apps/` and `packages/` contains at least one recognized project manifest: `package.json`, `project.json`, `pyproject.toml`, or `Cargo.toml`.
4. The deprecated `docs/audits/` directory does not exist and `docs/audit/` does exist.
5. `debug.log` is not tracked by Git.
6. `scratchboard/README.md` exists and identifies the directory as legacy.

The validator is read-only. It reports all discovered violations in one run, groups them by check, prints actionable paths, and exits with code 1 when any violation exists. A clean run prints a short success summary and exits with code 0. Unexpected filesystem or Git failures produce a distinct error message and nonzero exit code rather than being treated as an organizational violation.

The validator deliberately does not enforce exact project counts, exact README contents, or Nx membership for Rust and Python crates. Those checks would encode volatile or incorrect assumptions about the polyglot workspace.

## Data Flow

The repository validator reads the working tree and tracked-file list, applies deterministic checks, then renders one report:

```text
apps/ + packages/ manifests ---+
tickets/ metadata -------------+--> check:repo --> grouped report --> exit 0 or 1
docs/ layout ------------------+
git tracked-file list ---------+
scratchboard marker -----------+
```

It does not modify files, normalize statuses automatically, or maintain a generated cache. The committed repository remains the sole source of truth.

## Testing and Validation

Add focused Bun tests under `tests/repo-organization.test.ts` for:

- acceptance of each canonical ticket status;
- rejection of legacy and unknown statuses;
- synchronization checks for parent child-status table cells while ignoring activity-log prose;
- rejection of a non-`done` ticket with a non-empty `Completed:` field;
- detection of an app or package without a recognized manifest;
- detection of the plural audit directory;
- detection of tracked `debug.log` through an injectable tracked-file set;
- aggregation of multiple failures in a single report;
- clean success behavior.

Implementation validation must include:

```powershell
bun test tests/repo-organization.test.ts
bun run check:repo
bunx nx show projects
bun run build
```

Targeted checks must confirm there are no remaining ticket status variants in metadata or parent child-status tables and no tracked references to `docs/audits/`. Historical activity prose is explicitly excluded from the status-vocabulary assertion. The final Git diff must contain no production source changes and no unrelated work from another pull request.

## Rollout and Safety

Implement the cleanup in this order:

1. Add validator tests and the validator.
2. Normalize ticket metadata, synchronize parent child-status tables, reconcile non-empty `Completed:` fields to `done`, and rebuild the ticket index.
3. Consolidate the audit directory and repair links.
4. Update the repository documentation and scratchboard marker.
5. Untrack and ignore `debug.log`.
6. Run focused and repository-wide validation.

Each step is reversible through ordinary Git history. Stable application, package, spec, plan, and ticket paths are preserved except for the single `docs/audits/` consolidation. If the root build fails because of concurrent source work, record that separately and still verify that the organization-only diff is internally clean.

## Expected Changed Paths

- `.gitignore`
- `AGENTS.md`
- `README.md`
- `package.json`
- `debug.log` (removed from tracking)
- `docs/architecture/system/directory-structure.md`
- `docs/audit/**`
- `docs/audits/**` (removed after consolidation)
- tracked references to the moved audit document
- `docs/ops/prd-ticket-workflow.md` only if clarification is required
- `scratchboard/README.md`
- `tickets/README.md`
- ticket Markdown files whose status vocabulary is noncanonical
- `scripts/check-repo-organization.ts`
- `tests/repo-organization.test.ts`
