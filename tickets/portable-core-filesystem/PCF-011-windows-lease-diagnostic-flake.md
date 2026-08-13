# PCF-011 - Windows lease-diagnostic CI flake (resource-budget invariant)

- Status: in_progress
- Priority: P3
- Scope: `packages/anima-corefs/tests/catalog_benchmark.rs`, `.github/workflows/corefs-provenance.yml`
- Parent: `PCF-000`
- Depends on: none
- Owner: Codex
- PRD: none
- Spec: none
- Plan: none
- Created: 2026-07-30 15:50 MYT
- Updated: 2026-08-13 12:44 MYT
- Started: 2026-08-13 12:44 MYT
- Completed:

## Goal

Document and eventually harden a CI flake observed on PR #128 (2026-07-30):
the `windows-native-lease` job failed
`object_lease_diagnostic_records_ordered_boundaries_and_required_mutations`
with

```
DiagnosticInvariant("production lease did not retain its exact resource budget")
```

on a docs-only commit, then passed on an unmodified re-run. The identical
job had passed 35 minutes earlier on the same branch with identical Rust
code. Signature: a timing-sensitive lease resource-budget invariant on a
shared Windows runner — not caused by the PR under test.

Filed so the next person who hits it doesn't re-diagnose from scratch; the
fix belongs to whoever owns the lease-characterization work (PCF-002 scope),
e.g. widening the budget tolerance under CI, isolating the diagnostic's
runner resources, or gating the invariant on a quiescent-environment check.

## Deliverables

- Either a hardened invariant (deterministic under shared-runner noise) or
  an explicit retry/quarantine policy for this test in CI, chosen by the
  PCF-002 owner.

## Acceptance

- The test no longer fails spuriously on untouched Rust code, or spurious
  failures are automatically retried/quarantined with the flake tracked.

## Activity Log

- 2026-07-30 15:50 MYT - Filed from the PR #128 CI investigation (failure on
  docs-only commit `e336ece`, green on re-run; prior green run 35 minutes
  earlier at identical Rust code).
- 2026-08-13 12:44 MYT - Claimed by Codex on local branch
  `codex/pcf-011-windows-lease-flake` from PCF-006 completion head `290e8c62`.
  PCF-007 remains dependency-ineligible on the user-deferred PCF-004 paid
  package evidence, so PCF-011 is the next dependency-free child. Automatic
  paid workflow triggers remain disabled; no external action was authorized.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
