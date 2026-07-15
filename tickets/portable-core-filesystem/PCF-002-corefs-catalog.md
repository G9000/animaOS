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
- Updated: 2026-07-15 17:58 MYT
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
- 2026-07-14 22:02 MYT - Addressed the third current-head Codex review pass with a red/green malformed UTF-8 patch regression: update lines now validate ` `, `+`, or `-` through Unicode-safe prefix stripping before extracting content, returning a typed parse error instead of panicking at a non-character byte boundary. Revalidated shared and Animus tests, formatting, clippy, build, and diff checks.
- 2026-07-14 22:35 MYT - Addressed the fourth current-head Codex review pass with a red/green authority-boundary regression: grep now rejects a mismatched `BackendPath` tag before metadata, directory, or file access, including the single-file fast path. Revalidated shared and Animus tests, formatting, clippy, build, and diff checks.
- 2026-07-14 22:52 MYT - Addressed the fifth current-head Codex review pass with a red/green nested/sibling glob regression: resumed pages now trust deterministic walk preorder without a second lexicographic filter. Diagnosed the Linux provenance failure as a test-only cross-channel scheduling assertion, retained both replay/queued-response outcomes without comparing independent observer arrival order, and passed the focused websocket test 100 consecutive times plus all full gates.
- 2026-07-14 23:08 MYT - Addressed the sixth current-head Codex review pass with red/green patch regressions: planner preflight now rejects virtual file/descendant collisions in either order (including moves beneath the source), while HostFS delete/move-source mutations resolve the named directory entry rather than following a final symlink. Added separate entry identity to planner snapshots so deleting a symlink does not mark its target deleted. Revalidated 50 shared tests and 122 Animus tests before the full quality gates.
- 2026-07-14 23:24 MYT - Addressed the seventh current-head Codex review pass with red/green delete-preflight regressions: `PatchSnapshot` now exposes file-entry existence independently from text decoding, HostFS implements it with non-following `symlink_metadata`, binary files and dangling/outside-target symlinks can be deleted safely, and directories are rejected before any earlier best-effort mutation applies. Revalidated focused behavior before the full quality gates.
- 2026-07-15 01:36 MYT - Addressed the eighth current-head Codex review pass with instrumented-reader red/green regressions: grep stops scanning after an output cap plus one bounded validation probe, while text reads and grep both stop immediately when a line exceeds its byte ceiling. Preserved bounded late-binary detection and added explicit read-count assertions for large inputs before the full quality gates.
- 2026-07-15 01:51 MYT - Addressed the ninth current-head Codex review pass with red/green boundary regressions: text reads stop before validating content beyond the requested window while preserving exact-EOF truncation semantics, and patch preflight tracks a deleted symlink entry through regular-file recreation and later same-patch updates. Revalidated 56 shared tests, 125 Animus tests, formatting, clippy, build, and diff checks.
- 2026-07-15 02:16 MYT - Addressed the tenth current-head Codex review pass: reproduced and fixed HostFS partial patch application by preflighting every ordered write parent against filesystem and virtual mutation state before the first write. Verified the separate dangling-symlink listing comment already follows the requested skip behavior at the permission boundary and added a regression documenting readable-sibling continuity. Revalidated 56 shared tests, 127 Animus tests, formatting, clippy, build, and diff checks.
- 2026-07-15 02:31 MYT - Addressed the eleventh current-head Codex review pass: `read_file` now reports truncation with the exact next offset, and HostFS directory enumeration skips non-UTF-8 Unix entries instead of aborting sibling listing/search. Added a red/green truncation regression plus a Unix-specific filename regression; revalidated 56 shared tests, 128 local Animus tests, formatting, clippy, build, and diff checks before standalone Linux CI.
- 2026-07-15 02:54 MYT - Addressed the twelfth current-head Codex review pass with a red/green HostFS traversal regression: walk/search metadata now preserves a requested root symlink's identity after canonical containment authorization, so a directory symlink used as the root is never descended. Added a Windows junction fallback so the traversal regression exercises the behavior without elevated symlink privileges; revalidated 56 shared tests, 129 Animus tests, formatting, clippy, build, and diff checks.
- 2026-07-15 12:19 MYT - Started PCF-002's second reviewable slice from merged `main` in an isolated worktree. Scope is the bounded authenticated `.acore` object envelope, deterministic versioned catalog codec, and typed `anima-core` PyO3 boundary only; folders/policy, `fs/HEAD`, publication transactions, rotation, APIs/tools, and benchmark work remain deferred. Fresh dependency setup and the merged Rust baseline (`anima-file-tools`, `anima-corefs`, `anima-core`, `animus`) passed before implementation.
- 2026-07-15 13:02 MYT - Completed PCF-002's second implementation slice with red/green coverage: streaming AES-256-GCM metadata/body framing, strict authenticated bounds and range reads, canonical encrypted catalogs with generation-derived keys and opaque names, and byte-oriented PyO3 operations that retain Rust key ownership. Exact Rust 1.75, focused PyO3, combined Rust tests, CoreFS formatting/clippy, and scoped new-FFI checks passed. PCF-002 remains `in_progress` for folders/policy, catalog publication and `fs/HEAD`, rotation, APIs/tools, and benchmarks.
- 2026-07-15 13:34 MYT - Hardened the second slice against the final format and boundary requirements with red/green coverage: the V1 object header now declares and validates the `object-dek` domain, object-key epoch, and bounded UTF-8 object ID; opaque metadata uses a closed body-encoding enum and recursively rejects catalog-owned placement keys; PyO3 now exposes file-like streaming encrypt, full decrypt, and authenticated range reads with a conservative 16-MiB cap on byte convenience APIs; and catalog version tests assert typed payload and encrypted-header rejection. PCF-002 remains `in_progress` for the deferred filesystem/publication layers.
- 2026-07-15 14:11 MYT - Completed the third slice-two hardening pass with red/green regressions: object and stable IDs are canonical uppercase Crockford ULIDs; native JSON emission and FFI inputs are bounded before allocation/parsing; catalog decrypt and naming share one exact-length header parser; nonce generation retries collisions and fails closed; and native/PyO3 streaming errors document or enforce discard/rollback semantics, including terminal authenticated hash failures. PCF-002 remains `in_progress` for folders/policy, catalog publication and `fs/HEAD`, rotation, APIs/tools, and benchmarks.
- 2026-07-15 14:26 MYT - Closed the final slice-two allocation review finding with a test-first capped counting serializer: native metadata and catalog values now complete an allocation-free serialized-size preflight before any proportional clone or canonicalization allocation. A clone-tracking regression proves oversized input never reaches `Clone`; native envelope and catalog limit regressions preserve typed errors and deterministic canonical bytes. Rust 1.75 CoreFS passed 35 tests and the combined native run passed 253 tests. PCF-002 remains `in_progress` for the deferred filesystem/publication layers.
- 2026-07-15 15:29 MYT - Addressed PR #94 Codex review feedback test-first: backend FFI crypto fixtures now use canonical opaque ULIDs and retain an updated stable AAD vector, while catalog encoding sorts caller-owned public payload entries before canonical validation without weakening strict encoded-byte decoding. The focused Python suite passed 7 tests, Rust 1.75 CoreFS passed 36 tests, and the combined native run passed 254 tests.
- 2026-07-15 15:44 MYT - Claimed PCF-002 Step 6 from merged `main` in isolated worktree `codex/pcf-002-catalog-head`. Scope is first-class folders, inherited policy validation, complete typed immutable catalog entries, and the authenticated `fs/HEAD` record; the Core-wide commit coordinator, failure injection, rotation, logical operations/APIs, grants, and benchmarks remain later steps. Dependency setup and the merged native baseline passed 254 tests before implementation.
- 2026-07-15 17:50 MYT - Completed PCF-002 Step 6 with first-class portable folders, closed ownership/access and role namespaces, sticky-deny policy inheritance, strict complete V2 catalogs, bounded linear graph validation, lifecycle reference invariants, V2-specific key derivation, and authenticated canonical `fs/HEAD`. Review hardening made principal/role issuance fail closed until the capability broker exists, denied clients without device-local grants, kept privileged plaintext promotion and irreversible cutover issuance crate-private, and added allocation-free catalog-size preflight. Step 6 passed independent requirements and code-quality review; PCF-002 remains `in_progress` for the Step 7 commit coordinator and later slices.
- 2026-07-15 17:58 MYT - Published the Step 6 catalog/HEAD slice as draft PR #96 (`codex/pcf-002-catalog-head`) against `main`. The PR description records the completed Step 6 boundary, 86 Rust 1.75 CoreFS tests, 304 combined native tests, workspace build, and the explicitly deferred Step 7+ work.

## Validation

- Commands:
  - `cargo test -p anima-corefs --tests` (required red compile failure before the envelope/catalog modules existed)
  - `cargo +1.75.0 test --locked -p anima-corefs` (23 tests, including 12 new catalog/envelope integration tests)
  - `cargo test --locked -p anima-corefs -p anima-core` (241 tests)
  - `cargo check --locked -p anima-core --features python --tests`
  - `$env:PATH='<uv-python-home>;' + $env:PATH; $env:PYO3_PYTHON='<worktree>/.venv/Scripts/python.exe'; cargo test --locked -p anima-core --features python --lib corefs_envelope_and_catalog_bindings_roundtrip_bytes_without_exposing_keys` (1 focused PyO3 test)
  - `cargo fmt -p anima-corefs -- --check`; `cargo fmt -p anima-core -- --check` remains blocked by pre-existing formatter drift across nine unrelated `anima-core` files, which was intentionally left untouched after inspecting the new FFI hunk separately
  - `cargo clippy --locked -p anima-corefs --all-targets -- -D warnings`
  - `cargo clippy --locked -p anima-core --features python --all-targets` plus scoped verification of zero diagnostics in the new CoreFS FFI line range; repository-wide `-D warnings` remains blocked by 107 pre-existing `anima-core` lints outside this slice
  - `git diff --check`
  - `cargo test --locked -p anima-corefs --test envelope --test catalog` (required compile-red on the new V1 header/metadata contract, then 14 focused tests passed)
  - `cargo check --locked -p anima-core --features python --tests` (required compile-red on the new streaming boundary, then passed with existing warnings only)
  - `$env:PATH='<Python 3.13 home>;' + $env:PATH; cargo test --locked -p anima-core --features python --lib ffi::python::tests::corefs_streaming_bindings_roundtrip_range_and_enforce_convenience_cap -- --exact` (1 focused PyO3 test covering `io.BytesIO`, full/range streaming, the convenience cap, and `PyOSError` mapping)
  - `cargo +1.75.0 test --locked -p anima-corefs` (25 tests); `cargo test --locked -p anima-corefs -p anima-core` (243 tests)
  - `cargo fmt -p anima-corefs -- --check`; `cargo clippy --locked -p anima-corefs --all-targets -- -D warnings`; `cargo clippy --locked -p anima-core --features python --all-targets` with zero diagnostics in the new CoreFS FFI ranges; `git diff --check`
  - `cargo test --locked -p anima-corefs --test opaque_id --test catalog --test envelope` (required compile-red on the canonical-ID API and fallible catalog entry construction, then passed)
  - `cargo test --locked -p anima-corefs --lib` (required compile-red on bounded serialization and injectable nonce generation, then passed)
  - `cargo check --locked -p anima-core --features python --tests` (required compile-red on FFI bounds/fallible catalog construction, then passed with existing warnings only)
  - `cargo +1.75.0 test --locked -p anima-corefs` (32 tests)
  - `cargo test --locked -p anima-corefs -p anima-core` (250 tests)
  - `$env:PATH='<Python 3.13 home>;' + $env:PATH; cargo test --locked -p anima-core --features python --lib ffi::python::tests::corefs_` (4 focused PyO3 tests covering bounds, append-only writer validation, and rollback after late failures)
  - `cargo fmt -p anima-corefs -- --check`; `cargo clippy --locked -p anima-corefs --all-targets -- -D warnings`; `cargo clippy --locked -p anima-core --features python --all-targets` with no new diagnostics in changed production FFI ranges; `git diff --check`
  - `cargo test --locked -p anima-corefs --lib bounded::tests::oversized_json_is_rejected_before_clone -- --exact` (required compile-red before the capped preflight helper existed, then passed with zero clone calls)
  - focused native envelope/catalog oversize regressions passed; `cargo +1.75.0 test --locked -p anima-corefs` (35 tests); `cargo test --locked -p anima-corefs -p anima-core` (253 tests)
  - `cargo fmt -p anima-corefs -- --check`; `cargo clippy --locked -p anima-corefs --all-targets -- -D warnings`; `git diff --check`
  - Codex review follow-up: canonical Python object-ID fixtures (7 tests), unsorted public catalog canonicalization regression, `cargo +1.75.0 test --locked -p anima-corefs` (36 tests), and `cargo test --locked -p anima-corefs -p anima-core` (254 tests)
  - PCF-002 Step 6: `cargo +1.75.0 test --locked -p anima-corefs` (86 tests, including compile-fail authority-boundary coverage)
  - PCF-002 Step 6: `cargo test --locked -p anima-corefs -p anima-core` (304 tests; existing `anima-core` warnings only)
  - PCF-002 Step 6: `cargo check --locked -p anima-core --features python --tests` (passed; existing unrelated warnings only)
  - PCF-002 Step 6: `cargo fmt -p anima-corefs -- --check`; `cargo clippy --locked -p anima-corefs --all-targets -- -D warnings`; `git diff --check`
  - `cargo +1.75.0 test --locked -p anima-file-tools` (56 tests)
  - `cargo test --locked -p animus` (128 local tests; 129 on Unix)
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
  - `packages/anima-corefs/src/envelope.rs` and `packages/anima-corefs/tests/envelope.rs`
  - `packages/anima-corefs/src/catalog/` and `packages/anima-corefs/tests/catalog.rs`
  - `packages/anima-corefs/src/{lib.rs,crypto.rs,id.rs,bounded.rs}` and `packages/anima-corefs/Cargo.toml`
  - `packages/anima-corefs/src/{folders.rs,policy.rs,head.rs}` and `packages/anima-corefs/src/catalog/v2.rs`
  - `packages/anima-corefs/tests/{folders.rs,policy.rs,catalog_entries.rs,head.rs}`
  - `packages/anima-corefs/tests/opaque_id.rs`
  - `packages/anima-core/src/ffi.rs`
  - `apps/server/tests/test_corefs_crypto.py`
  - `Cargo.lock`
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
  - Reviews: https://github.com/G9000/animaOS/pull/91, https://github.com/G9000/animaOS/pull/94, https://github.com/G9000/animaOS/pull/96
