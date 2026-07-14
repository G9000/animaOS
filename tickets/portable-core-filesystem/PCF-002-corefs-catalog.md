# PCF-002 - Shared file tools, immutable objects, catalogs, and CoreFS

- Status: backlog
- Priority: P0
- Scope: `packages/anima-file-tools`, `packages/anima-corefs`, `packages/anima-core`, `apps/animus`, `apps/server` Core Filesystem/API/agent tools, `apps/desktop` release packaging, `.github/workflows`, `scripts`, and `third_party`
- Parent: `PCF-000`
- Depends on: `PCF-001`
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-2-shared-file-tools-immutable-object-store-catalog-and-corefs-contract`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-13 20:47 MYT
- Started:
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

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim after PCF-001 is done.
