# PCF-002 - Shared file tools, immutable objects, catalogs, and CoreFS

- Status: in_progress
- Priority: P0
- Scope: `packages/anima-file-tools`, `packages/anima-corefs`, `packages/anima-core`, `apps/animus`, `apps/server` Core Filesystem/API/agent tools, `apps/desktop` release packaging, `.github/workflows`, `scripts`, and `third_party`
- Parent: `PCF-000`
- Depends on: `PCF-001`
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-2-shared-file-tools-immutable-object-store-catalog-and-corefs-contract`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-14 21:51 MYT
- Started: 2026-07-14 19:45 MYT
- Completed:

## Goal

Create production-grade shared Rust file-operation contracts, reuse them explicitly in Animus HostFS and CoreFS, and implement encrypted immutable objects, first-class folders/policy, full catalog generations, atomic `fs/HEAD`, trash/restore, and catalog-bound rotation.

## Deliverables

- Chunk-authenticated, bounded-stream `.acore` envelope; catalog; commit coordinator; logical operations; API; and agent tools.
- `corefs_write` plus every required file-like operation.
- Targeted object/FRK catalog rotation and recovery.
- Reproducible reference catalog benchmark artifact.
- `anima-file-tools` backend traits, bounded streams/walk/glob/grep, stable pagination, output caps, and typed apply-patch parser/planner.
- Animus HostFS adapter preserving containment/permission behavior without claiming CoreFS transactions.
- First-class empty/custom folders with stable IDs/roles, `user|anima|shared` ownership, inherited `none|read|write|manage` ANIMA access, and explicit-deny precedence.
- Portable namespaced client roles/metadata plus device-local grants bound to verified installed-package/Core/instance/folder/scope identity, with transfer reapproval and immediate lock/revocation enforcement.
- Recoverable trash/restore; user-authorized permanent purge remains PCF-010.
- Apache-2.0 Cargo metadata for `anima-file-tools`, source headers, a per-file `THIRD_PARTY_NOTICES.md` inventory, complete Apache-2.0 license text, and applicable upstream Codex NOTICE pinned to audited commit `9e552e9d15ba52bed7077d5357f3e18e330f8f38`.
- Pull-request CI that proves attribution, locked Cargo metadata, builds, and tests in a standalone animaOS checkout with no sibling Codex tree.
- Desktop release staging and artifact checks that package exact-hash copies of `THIRD_PARTY_NOTICES.md`, Apache-2.0, and the applicable Codex NOTICE.
- Core-session authentication that resolves user, ANIMA, and installed-client principals distinctly; owner scope is limited to user-only operations.

## Acceptance

- Crash injection never exposes a partial mutation.
- Path/revision/security contract tests pass.
- Multi-process OS-lock tests exclude simultaneous open/commit and survive crash/PID reuse; chunk truncation/reordering/range-read/size-bound tests pass.
- Catalog benchmark records live/tombstone/total counts and serialized size, meets p95 <= 100 ms for 5,000 live plus 500 tombstones, keeps 25,000 live plus 2,500 tombstones at or below 16 MiB and p95 <= 250 ms, and meets p95 <= 250 ms for a separate 16-MiB fixture when the maximum-live fixture is smaller; otherwise the design is revised before cutover.
- Host and CoreFS tools never auto-route; cross-backend paths/URIs fail closed.
- CoreFS multi-file patches preflight all paths/policy/revisions/formats and publish one catalog generation or none.
- Shared limits enforce 1-MiB read chunks, depth 64, 10,000 directories, 50,000 entries, and 4-MiB model-visible responses.
- CoreFS NFC/case-sensitive lookup is deterministic across machines; HostFS preserves declared host semantics. Streaming literal/linear-time-regex grep enforces binary, cancellation, match, line, and output bounds.
- The per-principal operation matrix is enforced: client/ANIMA `manage` is structural only, while policy, grants, reserved roles, purge, and key retirement stay user-only.
- The capability broker derives identity from canonical installed manifest plus computed payload digest and optional trusted-publisher signature; spoofing, substitution, collision, replay, update-without-reapproval, and destination-transfer tests fail closed.
- Attribution/dependency validation passes in a clean standalone animaOS checkout with no sibling Codex directory; source and release artifacts include the required license/NOTICE files and Cargo metadata has no external path dependency.
- `.github/workflows/corefs-provenance.yml` executes the standalone-checkout gate, and the release-notice checker verifies both staged legal-file hashes and the Tauri resource mapping.
- Generic CoreFS API tests prove an authenticated client is evaluated as its installation principal rather than rejected or elevated to owner, while policy/grant/reserved-role/purge/key-retirement routes remain owner-only.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 17:34 MYT - Added the shared Rust file-tool/CoreFS architecture, customizable folder policy, client grants, trash, Codex provenance, and atomic multi-file patch requirement.
- 2026-07-12 18:58 MYT - Assigned clean-checkout CI, desktop legal-resource packaging, and distinct Core-session principal authorization to this ticket.
- 2026-07-13 20:47 MYT - Expanded scope metadata to every owned provenance/release surface and locked the benchmark fixture matrix so tombstones cannot consume the advertised live-entry capacity.
- 2026-07-14 19:45 MYT - Claimed PCF-002 from merged `main` on `codex/pcf-002-file-tools`. Began the first reviewable slice: shared bounded Rust file-operation contracts and the Animus HostFS adapter; encrypted CoreFS objects/catalogs remain sequenced behind this foundation.
- 2026-07-14 21:12 MYT - Completed the first PCF-002 implementation slice: added the MSRV-compatible `anima-file-tools` crate, bounded backend-neutral read/walk/glob/grep/text/patch engines, migrated Animus HostFS tools onto the shared contracts, added explicit HostFS best-effort patch atomicity, and established pinned Codex attribution plus standalone release-notice CI. PCF-002 remains `in_progress` for encrypted CoreFS objects/catalogs and later slices.
- 2026-07-14 21:22 MYT - Published the first slice as PR #91 (`codex/pcf-002-file-tools`) and requested a substantive Codex review focused on backend separation, path containment, boundedness, patch semantics, atomicity reporting, and provenance.
- 2026-07-14 21:34 MYT - Addressed both current-head Codex review findings with red/green regressions: explicit file-root grep now bypasses directory walking, and walk/grep cursors resume by deterministic preorder position rather than lexicographic path comparison. Added the derived nested-file match-cursor case and revalidated the full shared/Animus suites and build.
- 2026-07-14 21:51 MYT - Addressed the second current-head Codex review pass with red/green regressions: `apply_patch` approval cannot leak into a session-wide wildcard, HostFS patch keys follow the workspace volume's detected case semantics including case-insensitive APFS, and update hunks preserve a missing final newline. Revalidated all shared and Animus tests, formatting, clippy, build, and diff checks.

## Validation

- Commands:
  - `cargo +1.75.0 test --locked -p anima-file-tools` (44 tests)
  - `cargo test --locked -p animus` (121 tests)
  - `cargo test --locked -p anima-corefs -p anima-core` (229 tests)
  - `cargo fmt -p anima-file-tools -p animus -- --check`
  - `cargo clippy --locked -p anima-file-tools --all-targets -- -D warnings`
  - `cargo clippy --locked -p animus --bin animus -- -D warnings`
  - `bun run build`
  - `uv run ruff check scripts/check_codex_attribution.py scripts/check_corefs_release_notices.py`
  - `uv run python scripts/check_codex_attribution.py`
  - `bun run scripts/prepare-desktop-release.ts --legal-only`
  - `uv run python scripts/check_corefs_release_notices.py`
  - `cargo metadata --locked --no-deps --format-version 1`
  - workflow YAML parse and `git diff --check`
- Changed paths:
  - `packages/anima-file-tools/`
  - `apps/animus/src/tools/files.rs`
  - `apps/animus/src/tools/files/`
  - `apps/animus/src/tools/{mod.rs,process.rs,secrets.rs,shell.rs}`
  - `apps/animus/src/approvals.rs`
  - `Cargo.toml`, `Cargo.lock`, and `apps/animus/Cargo.toml`
  - `THIRD_PARTY_NOTICES.md` and `third_party/`
  - `scripts/check_codex_attribution.py`, `scripts/check_corefs_release_notices.py`, and `scripts/prepare-desktop-release.ts`
  - `.github/workflows/corefs-provenance.yml`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
  - `tickets/portable-core-filesystem/{PCF-000-portable-core-filesystem.md,PCF-002-corefs-catalog.md}`
- Notes:
  - PCF-001 is complete. PCF-002 is being delivered through reviewable PR slices while retaining this ticket as the milestone tracker.
  - The normal parallel Animus run initially exposed a pre-existing shared secrets-fixture race. A red/green test-only fixture consolidation removed the race; the unchanged single-thread suite had already passed all 116 tests.
  - Tauri already maps `resources/.anima/` into the bundle, so staging `.anima/legal` required no `tauri.conf.json` change.
  - Review: https://github.com/G9000/animaOS/pull/91
