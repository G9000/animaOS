# PCF-000 - Portable Core Filesystem

- Status: in_progress
- Priority: P0
- Scope: `apps/server`, `apps/desktop`, `apps/animus`, `apps/local-runtime-daemon`, `apps/anima-mod`, `packages/anima-file-tools`, `packages/anima-corefs`, `packages/anima-core`, `packages/api-client`, migrations, architecture docs
- Parent: none
- Depends on: none
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-17 18:59 MYT
- Started: 2026-07-13 21:27 MYT
- Completed:

## Goal

Define ANIMA CORE as animaOS's portable encrypted Soul-plus-CoreFS subsystem, make encrypted Core objects canonical for portable app content, reserve SQLCipher for ANIMA's internal continuity, and move disposable PostgreSQL state outside `.anima/`.

## Child Tickets

| Ticket | Title | Status | Depends on |
|---|---|---|---|
| PCF-001 | Filesystem key hierarchy and credential generations | done | none |
| PCF-002 | Shared file tools, immutable objects, catalogs, and CoreFS | in_progress | PCF-001 |
| PCF-003 | Machine-local Runtime and progressive indexing | backlog | PCF-002 |
| PCF-004 | Diary, folders, drafts, and notes | backlog | PCF-003 |
| PCF-005 | Canonical threads, messages, and transcript merge | backlog | PCF-003 |
| PCF-006 | Gallery, attachments, documents, and knowledge sources | backlog | PCF-003, PCF-005 |
| PCF-007 | Account profile, tasks, preferences, and credentials | backlog | PCF-004, PCF-006 |
| PCF-008 | Cutover, transfer, and first-release validation | backlog | PCF-001 through PCF-007 |
| PCF-009 | Later-release Soul cleanup and legacy retirement | backlog | PCF-008 plus observation/approval gate |
| PCF-010 | Retained-catalog GC and key retirement | backlog | PCF-008 plus retention/backup approval gate |

## Deliverables

- Approved PRD, storage/security specifications, and implementation plan.
- Linked full target-architecture graph covering topology, startup/indexing, tools, operations, authorization, and local transfer/recovery.
- Ten independently reviewable implementation slices.
- Local cold/live ANIMA CORE transfer, full/Soul/CoreFS recovery, removable-media streaming, and clean-machine rebuild validation.
- Separate later-release destructive cleanup gate.
- Separate retained-catalog pruning and cryptographic key-retirement gate.
- Dedicated Rust `anima-file-tools` and `anima-corefs` libraries shared safely with Animus HostFS through explicit backends.
- Customizable stable-ID folders, ownership/access policy, recoverable trash, and folder-scoped client extension grants.

## Acceptance

- Every child ticket is `done`.
- Copied `.anima/` restores Soul plus portable user-owned content without Runtime.
- SQLCipher contains only approved Soul tables after PCF-009; retained objects/catalogs and decrypt-only keys retire only through PCF-010.
- Full backend, desktop, migration, transfer, lock, recovery, and health validation is recorded.
- Product and technical naming consistently distinguish animaOS, ANIMA CORE, Soul, CoreFS, and Runtime.
- Codex-derived production patterns are selectively adapted with pinned provenance/Apache-2.0 notices; CoreFS improves multi-file patching to one-generation atomic publication.

## Completed Tickets

- PCF-001 - Filesystem key hierarchy and credential generations (latest PR #90 review follow-up completed 2026-07-14 18:27 MYT).

## Activity Log

- 2026-07-12 06:07 MYT - Parent tracker and child backlog created from the approved implementation plan.
- 2026-07-12 15:45 MYT - Locked animaOS as the product and ANIMA CORE as the portable subsystem/export family, with independent Soul/CoreFS local recovery.
- 2026-07-12 16:01 MYT - Updated PCF-001/PCF-008 after independent review to define scoped recovery keys and crash-safe local transfer publication.
- 2026-07-12 17:34 MYT - Locked the Rust/Python boundary, shared Animus file-tool library, customizable stable-role folders, least-privilege client grants, and pinned Codex production reference.
- 2026-07-12 17:34 MYT - Closed final review gaps for structural-only `manage`, broker-derived package identity, user-reauthenticated trash purge, complete Apache license/NOTICE distribution, and Animus submodule staging.
- 2026-07-12 18:00 MYT - Added the linked full target-architecture graph for topology, startup/indexing, tool routing, operations, authorization, and transfer/recovery.
- 2026-07-12 18:58 MYT - Closed the final execution-readiness gaps for standalone CI/release notices, stable Notes/Conversations roots, and principal-aware CoreFS API authorization.
- 2026-07-13 20:47 MYT - Reconciled the initiative with merged main: preserved current rich Journal HTML/media behavior, inventoried new document parsing/tool/context/compiler persistence boundaries, expanded migration tests/docs, and made catalog benchmark fixtures unambiguous.
- 2026-07-13 21:27 MYT - Began PCF-001 implementation in an isolated worktree after a green Rust and crypto/recovery baseline.
- 2026-07-14 12:00 MYT - Completed PCF-001 after six implementation/review hardening commits, clean requirements and quality re-reviews, full credential/recovery regressions, native crypto verification, migration/build/lint checks, and independent final hardening tests. PCF-002 is now unblocked.
- 2026-07-14 15:45 MYT - Reopened PCF-001 for a current-head review fix covering cross-Core vault import key-hierarchy rebinding; PCF-002 remains blocked until the follow-up is validated and reviewed.
- 2026-07-14 16:03 MYT - Completed and validated the PCF-001 cross-Core vault follow-up with a 25-test vault suite plus lint/build gates; PCF-002 is unblocked again.
- 2026-07-14 17:09 MYT - Reopened PCF-001 for a current-head cross-Core import review regression covering destination user identity, manifest-index consistency, and password-rotation safety; PCF-002 is blocked pending validation and re-review.
- 2026-07-14 17:12 MYT - Completed and validated destination-account rebinding for cross-Core imports with a red/green different-username regression, all 25 vault tests, lint, build, and diff gates; PCF-002 is unblocked again.
- 2026-07-14 17:25 MYT - Reopened PCF-001 for a current-head registration-crash follow-up covering orphaned Soul keyslots across legacy password rotation; PCF-002 is blocked pending validation and re-review.
- 2026-07-14 18:02 MYT - Completed and validated atomic orphaned-backfill cleanup during legacy password rotation with red/green crash recovery coverage, 91 credential tests, lint, build, and diff gates; PCF-002 is unblocked again.
- 2026-07-14 18:15 MYT - Reopened PCF-001 for a current-head stale-native-extension startup compatibility follow-up; PCF-002 is blocked pending validation and re-review.
- 2026-07-14 18:27 MYT - Completed and validated native-first manifest publication with a durable Python fallback for stale extensions: 48 CoreFS keyslot tests, 10 binding/permission tests, lint, build, and diff gates passed; PCF-002 is unblocked again.
- 2026-07-14 19:45 MYT - Started PCF-002 from merged `main`; the first reviewable slice establishes shared bounded file-operation contracts and migrates Animus HostFS onto them before CoreFS catalog work.
- 2026-07-14 21:12 MYT - Completed and validated PCF-002's first reviewable slice: shared bounded file tools, explicit Animus HostFS reuse, typed preflighted patch planning, and standalone Codex attribution/release gates. PCF-002 remains in progress for the CoreFS object/catalog implementation.
- 2026-07-14 21:22 MYT - Opened PCF-002 slice-one PR #91 and requested substantive Codex review; the parent remains in progress pending review/merge and later PCF-002 slices.
- 2026-07-14 21:34 MYT - Addressed PR #91's first Codex review pass with file-root grep and preorder-safe walk/grep cursor regressions; full shared/Animus tests, clippy, build, and diff checks passed before the follow-up push.
- 2026-07-14 21:51 MYT - Addressed PR #91's second Codex review pass by preventing reusable wildcard patch approvals, honoring case-insensitive HostFS volumes beyond Windows, and preserving missing final newlines during patch updates; all focused and full validation gates passed before the follow-up push.
- 2026-07-14 22:02 MYT - Addressed PR #91's third Codex review pass by making malformed multi-byte update-line prefixes fail with a typed parse error instead of panicking; the focused regression plus full shared/Animus, clippy, build, and diff gates passed.
- 2026-07-14 22:35 MYT - Addressed PR #91's fourth Codex review pass by enforcing backend authority before grep metadata access, with a regression proving mismatched paths cannot touch storage; all focused and full validation gates passed.
- 2026-07-14 22:52 MYT - Addressed PR #91's fifth Codex review pass by removing the redundant lexicographic glob cursor filter, and stabilized the failing Linux provenance test by removing an undefined ordering assertion across independent UI/server observer channels while preserving both protocol outcome checks; 100 focused repetitions and all full gates passed.
- 2026-07-14 23:08 MYT - Addressed PR #91's sixth Codex review pass by rejecting file/descendant patch plans before mutation and making HostFS symlink deletion/removal operate on the named entry without corrupting its target; entry-vs-target planner state is covered separately, and the full shared/Animus suites passed before final gates.
- 2026-07-14 23:24 MYT - Addressed PR #91's seventh Codex review pass by separating delete-entry metadata preflight from text snapshots: binary and dangling-symlink entries can be deleted without reading targets, while directory deletes fail before any prior best-effort mutation. Focused red/green regressions cover all three cases.
- 2026-07-15 01:36 MYT - Addressed PR #91's eighth Codex review pass by bounding grep work after the page fills and returning immediately on overlong text/grep lines; instrumented readers prove large inputs are not streamed to EOF, while the existing late-binary guard remains covered.
- 2026-07-15 01:51 MYT - Addressed PR #91's ninth Codex review pass by stopping text validation at the requested line boundary without falsely marking exact EOF as truncated, and by preserving virtual entry identity when a symlink is deleted then recreated as a regular file within one patch. All focused regressions and full shared/Animus quality gates passed.
- 2026-07-15 02:16 MYT - Addressed PR #91's tenth Codex review pass by preflighting HostFS write-parent shape across the full ordered mutation plan before applying any best-effort write. Confirmed dangling symlinks are already skipped before metadata lookup while readable siblings remain available, and added focused coverage for both behaviors; full shared/Animus quality gates passed.
- 2026-07-15 02:31 MYT - Addressed PR #91's eleventh Codex review pass by exposing `read_file` continuation offsets whenever output is truncated and skipping non-UTF-8 directory entries on Unix without dropping readable siblings. Focused platform coverage and the full local shared/Animus quality gates passed before standalone Linux CI.
- 2026-07-15 02:54 MYT - Addressed PR #91's twelfth Codex review pass by preserving final symlink identity for HostFS walk/search roots after containment authorization, preventing a root directory symlink from being traversed. Added cross-platform red/green coverage and revalidated the full shared/Animus quality gates.
- 2026-07-15 12:19 MYT - Began PCF-002's second isolated PR slice for the Rust `.acore` envelope, deterministic catalog codec, and PyO3 format boundary after a clean merged-main Rust baseline. Canonical catalog publication and the remaining PCF-002 filesystem layers stay in later slices.
- 2026-07-15 13:02 MYT - Completed and validated PCF-002's second isolated implementation slice: bounded streaming `.acore` object framing, deterministic generation-keyed encrypted catalog payloads, opaque physical catalog names, and typed Rust-owned PyO3 operations. Exact Rust 1.75, focused PyO3, combined Rust tests, CoreFS format/clippy, and scoped FFI gates passed; PCF-002 remains in progress for the deferred filesystem/publication layers.
- 2026-07-15 13:34 MYT - Completed final requirements hardening for PCF-002 slice two: V1 object headers now bind object identity, object-key epoch, and the closed `object-dek` key domain; metadata cannot smuggle catalog placement authority; Python callers have bounded file-like streaming APIs; and typed catalog version rejection covers both plaintext and encrypted headers. Focused red/green regressions, Rust 1.75, 243 combined native tests, PyO3 streaming, format, clippy, and diff gates passed; PCF-002 remains in progress for later publication/filesystem slices.
- 2026-07-15 14:11 MYT - Completed the third hardening pass for PCF-002 slice two: canonical Crockford ULID identities, bounded native/FFI serialization inputs, one strict catalog header parser, nonce-collision retry/failure behavior, and rollback-safe PyO3 streams are covered by focused regressions. Rust 1.75 CoreFS, 250 combined native tests, four focused PyO3 tests, format, clippy, and diff gates passed; PCF-002 remains in progress for later publication/filesystem slices.
- 2026-07-15 14:26 MYT - Closed PCF-002 slice two's final allocation review finding: metadata and catalog canonicalization now perform an allocation-free capped serialization pass before cloning caller-owned trees. Clone-tracking and native oversize regressions passed with 35 Rust 1.75 CoreFS tests and 253 combined tests; PCF-002 remains in progress for later publication/filesystem slices.
- 2026-07-15 15:29 MYT - Addressed both actionable PR #94 review threads: canonical ULID fixtures now exercise the backend FFI AAD contract, and public catalog encoding canonicalizes entry order before validation while decode remains strict. Focused Python, Rust 1.75 CoreFS, and combined native gates passed; PCF-002 remains in progress for later publication/filesystem slices.
- 2026-07-15 15:44 MYT - Began PCF-002 Step 6 from merged `main` on `codex/pcf-002-catalog-head`: first-class folder and policy contracts, complete typed immutable catalog generations, and `fs/HEAD`. The publication coordinator and subsequent mutation, rotation, API/tool, grant, and benchmark slices remain deferred.
- 2026-07-15 17:50 MYT - Completed PCF-002 Step 6 after requirements and code-quality review: portable folder/policy contracts, strict complete V2 catalogs, authenticated `fs/HEAD`, bounded linear graph/lifecycle validation, fail-closed authority issuance, and coordinator-only cutover promotion are covered by 86 Rust 1.75 CoreFS tests and 304 combined native tests. PCF-002 remains `in_progress`; Step 7 is the Core-wide commit coordinator.
- 2026-07-15 17:58 MYT - Published PCF-002 Step 6 as draft PR #96 (`codex/pcf-002-catalog-head`) against `main`; the parent remains `in_progress` while review and the later PCF-002 slices continue.
- 2026-07-15 19:29 MYT - Began PCF-002 Step 7 from merged `origin/main` in `codex/pcf-002-commit-coordinator`; the parent remains `in_progress` while the Core-wide commit coordinator is implemented and validated separately from Step 8 failure injection.
- 2026-07-15 20:55 MYT - Completed PCF-002 Step 7 with a review-clean Core-wide commit coordinator: exact prepared object/wrapped-key binding, root-anchored kernel exclusion, pinned-layout validation, complete mutation preconditions, shadow-only validation publication, authenticated irreversible cutover receipts, ordered catalog/HEAD publication, and post-unlock invalidation. Exact Rust 1.75, combined native, Python-feature, strict clippy, workspace build, provenance, and release-notice gates passed. The initiative and PCF-002 remain `in_progress`; Step 8 crash-boundary failure injection is next.
- 2026-07-16 00:26 MYT - Synchronized `Owner: Codex` from the recorded PCF-002 claim/start history and active PR lineage; preserved the parent and PCF-002 `in_progress` state without inventing or transferring ownership.
- 2026-07-16 22:51 MYT - Began PCF-002 Step 8 from merged `origin/main` at `14b23da6` in isolated worktree `codex/pcf-002-failure-injection`. This slice is limited to deterministic crash/failure injection at every durable CoreFS publication boundary and restart proofs that only the prior authoritative `fs/HEAD` or the complete next generation is visible. Rotation, logical operations/APIs, client grants, and benchmarks remain deferred. The Rust 1.75 CoreFS baseline passed 127 tests with two helper-process entries intentionally ignored by the parent harness.
- 2026-07-16 23:22 MYT - Completed PCF-002 Step 8 with ordered durable-boundary hooks and 69 Windows child-process crash points covering immutable objects, catalogs, validation and authoritative pointers, cutover receipt/completion, invalidation, and both current and legacy recovery paths. Authenticated post-HEAD receipt/completion markers make interrupted first-cutover finalization retryable while preserving `fs/HEAD` as the single irreversible event and upgrading legacy receipt-only or higher-generation states without rollback. Exact Rust 1.75 CoreFS, stable combined native/Python-feature checks, Windows and Linux-target compilation, strict clippy/format, workspace build, repository organization, provenance, release notices, locked metadata, and diff gates passed. PCF-002 remains `in_progress`; Step 9 key rotation is next.
- 2026-07-16 23:37 MYT - Closed the independent Step 8 protocol review findings: new receipts now publish only after durable authoritative `fs/HEAD`; post-HEAD marker I/O failures return committed outcomes with explicit recovery-pending state; every mixed unlocked cutover observation is re-read under the kernel lock before permanent corruption is classified; and the normative design plus architecture graph now document the persistent markers and recovery contract. Focused red/green regressions cover ordering, torn reads, and ordinary finalization failures.
- 2026-07-17 10:34 MYT - PR #102 merged PCF-002 Step 8 into `main` at `f2f56825` after standalone CI passed and Codex reviewed the exact head with zero actionable threads. Began Step 9 catalog-bound key rotation from that merged head in isolated worktree `codex/pcf-002-key-rotation`. Scope is targeted object-key rotation, FRK catalog rewrap, pending-FRK `fs/HEAD` recovery/finalization, retained old-catalog decryption, and explicit retirement gates; blind-token switching remains deferred to Task 3 and physical key pruning remains gated by PCF-010.
- 2026-07-17 11:24 MYT - Completed PCF-002 Step 9 with authenticated streaming Object-DEK replacement for live and recoverably trashed objects, exact-next-version FRK catalog rewrap across all retained wrappers, keyring-aware cutover recovery and later commits, explicit backup/retention retirement gates, and deterministic concurrent/crash regressions. Exact Rust 1.75 CoreFS, combined native and Python-feature checks, strict CoreFS clippy/format, workspace build, attribution, packaged notices, locked metadata, diff hygiene, and independent Critical/Important review passed. PCF-002 and the parent initiative remain `in_progress`; Step 10 logical CoreFS operations and bounded agent tools are next.

- 2026-07-17 12:13 MYT - PR #103 merged PCF-002 Step 9 at `7d4cae3f`, and Step 10 began from that exact `origin/main` head in isolated worktree `codex/pcf-002-logical-tools`. Scope is logical CoreFS operations, bounded shared traversal/grep, explicit search-index readiness, internal atomic mutation planning, and migration-frozen Python agent wrappers; generic client API/grants and catalog benchmarking remain Steps 11 and 12. The exact Rust 1.75 baseline passed 211 tests with 3 subprocess helpers intentionally ignored.
- 2026-07-17 15:36 MYT - PCF-002 Step 10 completed its Rust logical read and internal mutation layers in `codex/pcf-002-logical-tools`: live-only bounded logical operations, V1 wire accounting, catalog-bound range reads, folder-trash authenticated lifecycle and hiding, sealed one-generation validation-head mutation planning, public frozen write facade, and cross-policy preflight rejection. Exact Rust 1.75 CoreFS tests and strict clippy passed; independent review found no remaining Critical or Important issues. PCF-002 remains `in_progress` for Python/PyO3 wrappers, client API/grants, and benchmarks.
- 2026-07-17 15:57 MYT - PCF-002 Step 10 completed its Python/PyO3 logical tool boundary: validation-snapshot selection, selected generation/catalog-hash guards on every read wrapper, shared CoreFS V1 wire output, server-side Python adapters, and migration-frozen public mutator wrappers. Focused PyO3, Python adapter, Python lint, Python-feature compile, and diff hygiene checks passed. PCF-002 remains `in_progress` for Step 11 client API/grants and Step 12 benchmarks.
- 2026-07-17 16:42 MYT - Addressed PR #106 current-head Codex review feedback for PCF-002 Step 10 by wiring logical glob/grep continuation cursors through the PyO3 and server Python wrappers, with focused Rust/Python validation passing. The parent remains `in_progress` for Step 11 client API/grants and Step 12 benchmarks.
- 2026-07-17 17:18 MYT - Addressed PR #106's second current-head Codex review pass for PCF-002 Step 10: logical reads now respect response-budget clamping before raw backend open, and trashed objects retain historical restore metadata even when their original parent folder is later trashed. The parent remains `in_progress` for Step 11 client API/grants and Step 12 benchmarks.
- 2026-07-17 18:24 MYT - Addressed PR #106's third current-head Codex review pass for PCF-002 Step 10: catalog validation now rejects hidden original-parent cycles for trashed folders, and CoreFS package initialization no longer eagerly imports native logical bindings. The parent remains `in_progress` for Step 11 client API/grants and Step 12 benchmarks.
- 2026-07-17 18:36 MYT - Addressed PR #106's fourth current-head Codex review pass for PCF-002 Step 10: migration-frozen server mutation wrappers now preserve the native variadic frozen-write contract for realistic mutation inputs. The parent remains `in_progress` for Step 11 client API/grants and Step 12 benchmarks.
- 2026-07-17 18:59 MYT - Addressed PR #106's fifth current-head Codex review pass for PCF-002 Step 10: trashed folders now allow historical original-parent references to parents that are later trashed, while keeping live-trash-folder and hidden descendant-cycle validation fail-closed. The parent remains `in_progress` for Step 11 client API/grants and Step 12 benchmarks.

## Validation

- Commands:
  - `cargo +1.75.0 test --locked -p anima-corefs` (23 tests)
  - `cargo test --locked -p anima-corefs -p anima-core` (241 tests)
  - focused PyO3 binding test with the worktree CPython runtime (1 test)
  - `cargo fmt -p anima-corefs -- --check` plus scoped new-FFI format inspection; the existing `anima-core` package-wide format check remains blocked by unrelated baseline drift
  - `cargo clippy --locked -p anima-corefs --all-targets -- -D warnings`
  - scoped new-CoreFS-FFI clippy check and `git diff --check`
  - final slice-two hardening: 14 focused envelope/catalog tests, 25 Rust 1.75 CoreFS tests, 243 combined native tests, one focused `io.BytesIO` PyO3 streaming/cap/error-mapping test, CoreFS format and strict clippy, and zero scoped new-FFI clippy diagnostics
  - third slice-two hardening: canonical-ID/bounds/header/nonce/rollback compile-red regressions, 32 Rust 1.75 CoreFS tests, 250 combined native tests, four focused PyO3 tests, CoreFS format and strict clippy, and zero new diagnostics in the changed production FFI ranges
  - final slice-two allocation follow-up: compile-red clone-tracking preflight regression, focused native envelope/catalog oversize regressions, 35 Rust 1.75 CoreFS tests, 253 combined native tests, CoreFS format and strict clippy, and `git diff --check`
  - PR #94 review follow-up: 7 canonical-ID backend FFI tests, unsorted public catalog canonicalization regression, 36 Rust 1.75 CoreFS tests, and 254 combined native tests
  - PCF-002 Step 7: 104 exact Rust 1.75 CoreFS test entries, combined `anima-corefs`/`anima-core`, Python-feature check, CoreFS format/strict clippy, workspace build, provenance/release notices, `cargo metadata --locked`, and `git diff --check`
  - PCF-002 Step 8: exact Rust 1.75 CoreFS suite (138 passed, 3 helper entries ignored), 69 injected Windows process crashes, stable combined native suite (356 passed), Python-feature check, Rust 1.75 Linux-target check, strict CoreFS format/clippy, workspace build, repository organization, provenance/release notices, locked metadata, and `git diff --check`
  - PCF-002 Step 9: exact Rust 1.75 CoreFS suite (153 passed, 3 subprocess-helper entries ignored), targeted-object and FRK crash matrices, combined `anima-file-tools`/`anima-corefs`/`anima-core`/`animus` tests, Python-feature check, strict CoreFS clippy/format, workspace build, attribution/release notices, locked metadata, independent review, and `git diff --check`
  - PCF-002 Step 10 Layer 1/2: exact Rust 1.75 merged-head baseline (`anima-file-tools` + `anima-corefs`) passed 211 tests with 3 subprocess-helper entries ignored; focused logical mutation tests passed 6 tests; the cross-policy move/restore/patch regression passed; full `cargo +1.75.0 test --locked -p anima-corefs` passed; strict `cargo +1.75.0 clippy --locked -p anima-corefs --all-targets -- -D warnings` passed
  - PCF-002 Step 10 Layer 3: `cargo check --locked -p anima-core --features python`; focused PyO3 `cargo test --locked -p anima-core --features python corefs_`; `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; .venv/Scripts/python.exe -m pytest apps/server/tests/test_corefs_logical.py -q`; scoped ruff check; `git diff --check`
  - PR #106 cursor follow-up: Python logical wrapper test, `cargo check --locked -p anima-core --features python`, `cargo +1.75.0 test --locked -p anima-file-tools -p anima-corefs`, focused PyO3 CoreFS tests, scoped Ruff, and `git diff --check`
  - PR #106 second review pass: red/green focused regressions, full `cargo +1.75.0 test --locked -p anima-corefs`, strict CoreFS clippy, and `git diff --check` passed; touched files were manually aligned with rustfmt output while unrelated pre-existing CoreFS format drift remains outside this PR fix.
  - PR #106 third review pass: hidden-original-parent and native-binding package-import regressions, full CoreFS tests, strict CoreFS clippy, Python package/logical tests, scoped Ruff, and `git diff --check` passed.
  - PR #106 fourth review pass: mutation-wrapper argument-forwarding regression, focused Python logical tests, scoped Ruff, and `git diff --check` passed.
  - PR #106 fifth review pass: red/green `folder_trash_graph_invariants_fail_closed` regression, full `cargo +1.75.0 test --locked -p anima-corefs`, strict `cargo +1.75.0 clippy --locked -p anima-corefs --all-targets -- -D warnings`, and `git diff --check` passed.
  - scoped Markdown link/anchor, plan action/path, and docs-drift checks
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_diary_api.py apps/server/tests/test_document_parsing.py apps/server/tests/test_document_tools.py apps/server/tests/test_contextual_rerank.py apps/server/tests/test_html_ingestion.py apps/server/tests/test_structured_document.py apps/server/tests/test_web_fetch.py apps/server/tests/test_knowledge_autocompile.py apps/server/tests/test_retrieval_eval.py apps/server/tests/test_pdf_workflow_checkpoints.py -q`
  - `bun test apps/desktop/tests/journal-content.test.ts apps/desktop/tests/journal-html.test.ts`
- Changed paths:
  - `packages/anima-corefs/src/{envelope.rs,catalog/,crypto.rs,lib.rs,id.rs,bounded.rs}`
  - `packages/anima-corefs/tests/{envelope.rs,catalog.rs,opaque_id.rs}`
  - `packages/anima-corefs/src/{transaction.rs,publication.rs}` and `packages/anima-corefs/tests/{transaction.rs,publication.rs}`
  - `packages/anima-core/src/ffi.rs`
  - `apps/server/tests/test_corefs_crypto.py`
  - `packages/anima-corefs/Cargo.toml` and `Cargo.lock`
  - `docs/prds/portable-core-filesystem-v1.md`
  - `docs/superpowers/specs/2026-07-12-portable-core-filesystem-design.md`
  - `docs/superpowers/specs/2026-07-12-portable-core-key-hierarchy-design.md`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
  - `docs/prds/README.md`
  - `docs/CHANGELOG.md`
  - `docs/architecture/README.md`
  - `docs/architecture/system/anima-core-filesystem.md`
  - `tickets/portable-core-filesystem/`
  - `packages/anima-corefs/src/logical/`
  - `packages/anima-corefs/src/catalog/v2.rs`
  - `packages/anima-corefs/src/transaction.rs`
  - `packages/anima-corefs/src/transaction/failure_tests.rs`
  - `packages/anima-corefs/tests/{catalog_entries.rs,logical_snapshot.rs}`
  - `packages/anima-core/{Cargo.toml,src/ffi.rs}` and `Cargo.lock`
  - `apps/server/src/anima_server/services/corefs/{__init__.py,logical.py}`
  - `apps/server/tests/test_corefs_logical.py`
- Notes:
  - PCF-001 is complete; PCF-002 is the next implementation slice.
  - Merged-main reconciliation validation: 155 backend tests and 5 desktop Journal tests passed; all 13 scoped Markdown links/anchors and all plan action/path declarations passed. The repository-wide docs checker still reports pre-existing drift plus expected missing paths for planned PCF files; the PCF scope has no broken-link or non-planned-path finding.
