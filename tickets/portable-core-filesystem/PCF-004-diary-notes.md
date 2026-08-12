# PCF-004 - Diary, folders, drafts, and notes

- Status: in_progress
- Priority: P1
- Scope: `apps/server` diary/CoreFS, `apps/desktop` Journal
- Parent: `PCF-000`
- Depends on: `PCF-003`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Spec: `docs/superpowers/specs/2026-08-02-corefs-resumable-preparation-design.md`
- Plan: `docs/superpowers/plans/2026-08-02-corefs-resumable-preparation.md`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-13 03:45 MYT
- Started: 2026-08-02 04:06 MYT
- Completed:

## Goal

Make encrypted sanitized-HTML diary objects plus CoreFS folder, draft, and note objects canonical while preserving the existing rich Journal API and UI behavior without leaving embedded media inline.

## Deliverables

- Versioned sanitized-HTML diary codec, Markdown/sanitized-HTML note codecs, and idempotent SQLCipher conversion; plain diary text becomes escaped HTML paragraphs without lossy Markdown conversion.
- First-class empty/custom folder support; unique `core.journal` and `core.notes` stable-role bindings; default `owner=user`/`agentAccess=write`; and attachment CoreFS URIs.
- Inline `data:` media decoded under MIME/size limits, deduplicated into encrypted CoreFS binary objects, and replaced with stable CoreFS URIs before atomic publication.
- Journal drafts migrated out of plaintext localStorage.
- Backend and Bun desktop tests covering current `Journal.tsx`, content selection, HTML sanitization, covers, and attachments.

## Acceptance

- Existing diary data, folders, covers, and attachments round-trip with stable IDs/hashes.
- Current Tiptap formatting, attachment-only entries, cover-only entries, and valid inline images round-trip; canonical diary HTML contains no base64 `data:` URLs.
- Plain-text and HTML legacy bodies use the same versioned sanitization contract, and malformed/oversized embedded media cannot partially publish a diary revision.
- Empty folders survive migration.
- Journal still resolves after its root is renamed/moved, and ANIMA can read/write private diary content unless the user explicitly lowers access.
- Standalone Notes resolve through the same stable folder ID after rename, move, and restart; their root defaults to `owner=user`/`agentAccess=write`.
- Journal drafts are encrypted Core objects and UI behavior remains functional.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 17:34 MYT - Added stable Journal role and explicit private-diary ownership/access defaults.
- 2026-07-12 18:58 MYT - Added the `core.notes` root, ownership defaults, and rename/move/restart acceptance coverage.
- 2026-07-13 20:47 MYT - Reconciled migration with the merged Tiptap Journal: canonicalized sanitized HTML, extracted inline media to CoreFS objects, and added the current content/sanitizer helpers and tests to scope.
- 2026-08-02 04:06 MYT - Claimed PCF-004 from clean `main` at `51678d08680747d90ff0c03c0e091331456ae837` after PCF-003 completion. Executing the approved Task 4 plan directly in the main checkout per user direction, with test-first backend and Journal slices followed by independent specification and quality review.
- 2026-08-02 04:19 MYT - The first native integration pass proved the planned converter entry point did not exist: public mutation is intentionally frozen, the sequential crate-private shadow mutator cannot atomically publish a writing graph, and PyO3 exposes read/validation only. Removed the parallel encrypted-filesystem prototype, retained the green portable codec/migration groundwork in `7ac84178`, and corrected Task 4 to add one sealed session-scoped validation-batch API, deterministic native IDs, exact-head CAS, and role resolution while leaving public mutation frozen until PCF-008.
- 2026-08-02 17:01 MYT - Completed the bounded PCF-004 implementation and repeated independent specification/quality loops through `e30179bb`. Atomic inactive-catalog publication, production SQLCipher preparation, metadata/API parity, portable names, draft/media staging, sanitizer parity, stable-role lifecycle, exact reruns, 100 MiB attachments, and the public 20,000,000-character contract are green. Blocked on a material native security-protocol decision for legitimate writing corpora above 1 GiB: current Python/PyO3/Rust transport materializes the whole corpus, while prepared object tokens contain wrapped DEKs and physical identities only in memory. Safe bounded preparation requires an authenticated persistent preparation journal/head, restart recovery, abandonment/GC, rotation, and session-close semantics followed by one exact-head atomic finalization. Do not weaken atomicity or raise/remove bounds without an approved design.
- 2026-08-02 17:10 MYT - User approved the recommended authenticated persistent preparation protocol, clearing the design-decision blocker. Resumed PCF-004 without changing its original `Started:` timestamp and drafted the repository spec for bounded per-object preparation, encrypted `PREPARATION_HEAD` state, exact-CAS single-generation finalization, crash recovery, explicit abandonment, retention-gated GC, rotation exclusion, and bounded session close. Implementation remains gated on independent written-spec review and user approval of the committed document.
- 2026-08-02 17:24 MYT - Independent written-spec audit found six material gaps in the first committed draft `40021e63`: source changes after seal, corrupt-head rotation deadlock, preparation descriptors exceeding catalog-sized snapshots, ciphertext verification contradicting no-reread wording, abandonment crash idempotence, and accidental reuse of whole-graph PyO3 containers. The design now uses a SQLCipher source mutation fence, separately bounded encrypted manifests, explicit bounded ciphertext revalidation, deterministic terminal receipts, operator-only corrupt-head quarantine with old-FRK retention, and a new one-object native input. A focused re-review remains before user document approval.
- 2026-08-02 17:28 MYT - Focused re-review accepted four of the six repairs and caught two residual contradictions: the ready snapshot still claimed to embed the segmented final intent, and corrupt-pointer quarantine was nested beneath an unauthenticated preparation ID. The ready snapshot now authenticates only bounded segment roots/indexes, and quarantine is Core-global, hash-addressed, and forbidden from using unauthenticated pointer fields. The final pass/fail re-check approved commit `70f247a9`; only user approval of the reviewed document remains before implementation planning.
- 2026-08-02 18:45 MYT - The user approved the independently reviewed resumable-preparation design. Added the dedicated test-first implementation plan at `docs/superpowers/plans/2026-08-02-corefs-resumable-preparation.md`, covering encrypted preparation formats, durable CAS/recovery, one-object native preparation, exact finalization, terminal/rotation semantics, bounded PyO3 operations, SQLCipher mutation fencing, streaming Python orchestration, and final review/evidence. Implementation remains gated only on the reviewed-plan execution handoff.
- 2026-08-02 18:58 MYT - Independent plan review found five substantive execution gaps: browser localStorage drafts were incorrectly implied to share the SQLCipher fence, aggregate API removal preceded caller migration, completion-receipt ownership was split, corrupt-pointer key retention was not conservative enough, and one Cargo command used two filters. The plan now gives drafts an explicit ID/revision/hash handoff CAS, retires the aggregate API only after migration, owns completion recovery in finalization, retains the full trusted keyring snapshot for quarantine, and uses valid focused commands. Focused re-review is pending.
- 2026-08-02 19:00 MYT - Focused plan re-review accepted four repairs and found one remaining execution omission: the draft handoff CAS had no assigned production desktop/API work. Task 8 now includes the diary schema, API client contract, Journal draft-migration implementation, and a deterministic concurrent-edit test that preserves a newer local draft when an older completion token arrives. Final focused review is pending.
- 2026-08-02 19:00 MYT - The final focused plan pass approved `433586e8` with zero remaining consequential findings. PCF-004 is ready for the implementation execution handoff; the child remains `in_progress`, legacy SQLCipher remains authoritative, and no remote publication or merge authority is implied.
- 2026-08-02 19:52 MYT - Completed reviewed preparation Task 1 in `26781300` plus quality fixes `e75d0c4d`. Added the FRK-generation-bound preparation HKDF domain, five closed independently bounded encrypted record formats, complete contextual AEAD binding, canonical semantic hash/order enforcement, opaque authenticated sealed-envelope provenance, and capability-rooted immutable/fixed-head publication foundations. TDD RED proved the missing subkey/module and three later semantic/publication gaps; final focused verification passed 11 format tests, 10 crypto tests, and 227 CoreFS library tests with 1 ignored. One intermediate full-library run hit the known Windows lease flake and its exact test passed immediately; the independent spec review and final quality re-review approved with zero Critical or Important findings. No Task 2 state machine or authority change was introduced.
- 2026-08-02 20:25 MYT - Completed reviewed preparation Task 2 in `cdcc16ca` plus tests-only recovery hardening `16876f6b`. Added the authenticated Core-scoped preparation layout, deterministic same-source begin/resume, bounded status/reconciliation cursors, one-lock exact pointer-hash/snapshot-sequence CAS, immutable-snapshot-before-pointer durability, and fail-closed wrong/missing/corrupt/replayed state handling. TDD RED proved the missing API/state machine; focused begin/resume `5` and crash-boundary `1` tests passed. Spec review required target-specific Unix publication phases and exact prior/next semantic restart tuples; those repairs passed re-review. Independent quality verification passed all `233` CoreFS library tests with `1` intentionally ignored and found zero Critical/Important issues. A minor torn-pointer diagnostic classification remains fail-closed and was dispositioned as non-blocking. No Task 3 object preparation or authority change was introduced.
- 2026-08-12 19:18 MYT - Reconciled the previously unrecorded preparation Task 3 implementation already landed on `main` in `840dfc1c`, `a6d37c29`, and `1c93e9bb`. The code provides one-reader bounded object preparation, durable authenticated descriptor segments, deterministic exact-revision resume/conflict handling, byte/count-bounded paged reconciliation, complete graph metadata retention, and a synthetic logical corpus above 1 GiB without corpus-wide body ownership. Current focused verification passed `10` prepare-object tests, `1` bounded-large-corpus test, and `3` converter tests. Task 3 is complete; PCF-004 remains `in_progress`, Task 4 exact seal/finalize is next, and legacy SQLCipher plus inactive `VALIDATION_HEAD` authority remain unchanged.
- 2026-08-12 19:25 MYT - Resumed Task 4 test-first. The initial RED proved no durable final-intent staging boundary existed. Added a private exact-CAS staging slice that validates canonical entry hashes/ordering/uniqueness, splits intent across independently bounded encrypted segments, publishes the immutable segments before the next authenticated snapshot/head, rejects stale CAS, invalidates staged intent on later object/source reconciliation, and verifies intent roots plus cross-segment ordinals on restart. The preparation remains `collecting`; this slice cannot publish `VALIDATION_HEAD`, and graph sealing, exact finalization, and post-head completion recovery remain open. Focused seal/finalize tests passed `2`, the complete preparation module passed `30`, the CoreFS library passed `217` with `1` intentionally ignored, rustfmt and diff hygiene passed, and Clippy passed after allowing only the documented pre-existing `prepare_object_inner` too-many-arguments warning.
- 2026-08-12 20:00 MYT - Completed resumable-preparation Task 4. Added exact-CAS graph sealing with separately bounded encrypted final-intent segments, complete descriptor/role/policy/name/reference/revision/source/head validation, a body-free durable converter path, and one-lock reconstruction that authenticates ciphertext plus envelope metadata without materializing or decrypting object bodies before publishing exactly one `VALIDATION_HEAD` generation. Added HKDF-keyed deterministic encrypted completion receipts and idempotent recovery across pre/post-head, receipt, and pointer-clear seams; a different head fails closed and preserves Ready state, while a changed source explicitly returns Ready to Collecting. Required gates passed: seal/finalize `11`, post-head recovery `3`, validation-batch integration `7`, CoreFS library `229 passed`/`1 ignored`, adjusted strict Clippy, rustfmt, diff hygiene, and repository organization. Task 4 is complete; PCF-004 remains `in_progress`, and Task 5 terminal/rotation/session semantics is next.
- 2026-08-12 20:34 MYT - Published the exact Task 4 commit `9f1d78cbb1f268176ca216395b3c0d9d3db2580b` to draft PR #142 on `codex/pcf-004-resumable-preparation`; the queried GitHub head matched the pushed OID. No review request, monitoring, or merge action was taken.
- 2026-08-12 20:34 MYT - Completed resumable-preparation Task 5 locally. Exact-CAS abandonment now publishes a deterministic authenticated receipt before clearing the live pointer and never deletes prepared objects; corrupt raw pointers require an explicit hash-addressed operator quarantine whose authenticated receipt conservatively captures every trusted keyring generation. FRK activation fails closed for live, corrupt, incomplete-quarantine, or missing-retained-key state, and retirement accepts authenticated preparation-retention inventory. Session close rejects new terminal calls while in-flight calls retain the existing operation guard. The initial missing-API compile RED preceded implementation; final terminal `8`, quarantine `1`, rotation `14`, and CoreFS library `238 passed`/`1 ignored`, with adjusted strict Clippy, rustfmt, diff hygiene, and repository organization green. Task 6 bounded PyO3 exposure is next; this Task 5 commit remains local and unpushed.
- 2026-08-12 20:46 MYT - Published Task 5 commit `3c388643bfa7af1abb1be5e099d3a7ef88135e63` to the existing `codex/pcf-004-resumable-preparation` branch and PR #142; GitHub reported that exact head. No PR metadata update, review request, monitoring, or merge action was taken. Task 6 continues locally.
- 2026-08-12 21:11 MYT - Completed resumable-preparation Task 6 locally. Added seven versioned session-guarded PyO3 operations for begin/resume, bounded status/reconciliation, exactly-one-buffer object preparation, seal, finalize, abandon, and operator quarantine; their public Rust adapter and wire dictionaries omit physical names, wrapped DEKs, key material, and authenticated preparation secrets. Python receives typed conflict/corruption/source-fence errors, every operation is rejected after close admission stops, and active guards still drain before close releases the lease. The initial source-contract RED proved the methods and one-body transport were absent. Final gates passed: default boundary `2`, Python-feature boundary/integration `9`, targeted close-drain `1`, CoreFS library `238 passed`/`1 ignored`, Python feature check, adjusted strict Clippy, rustfmt, diff hygiene, and repository organization. The complete Python-feature session band remains `18/19` because the pre-existing pinned-root symlink case now rejects that symlink as an invalid CoreFS layout; all lifecycle/session accounting cases passed. Task 7 SQLCipher writing-source generation is next; legacy SQLCipher remains authoritative and no Task 6 publication is authorized.
- 2026-08-12 21:30 MYT - Completed resumable-preparation Task 7 locally. Linear Core migration `20260812_0001` adds a per-user monotonic writing-source generation plus INSERT/UPDATE/DELETE and ownership-reassignment triggers for folders, entries, and attachments; trigger effects share the writer transaction, roll back with it, count cascades, and isolate users. The migration revision advanced from the plan's now-occupied `20260802_0001` reservation rather than creating a duplicate or second head. A legacy unversioned create-all repair installs the same authoritative triggers after head stamping. The initial `5`-failure RED proved the revision/table/head and API writer fence were absent, and a later focused RED proved legacy stamped databases had the table but no triggers. Final gates passed: complete Task 7 migration/CoreFS migration/diary API band `32`, fresh/prior-head/downgrade-upgrade coverage, two independent encrypted SQLCipher connections under deterministic `BEGIN IMMEDIATE` exclusion, exactly one Alembic head `20260812_0001`, full server Ruff, diff hygiene, and repository organization. Task 8 streaming orchestration is next; legacy SQLCipher remains authoritative and no Task 7 publication is authorized.
- 2026-08-12 22:24 MYT - Completed resumable-preparation Task 8 locally. Production writing migration now inventories metadata without retaining corpus bodies, prepares exactly one verified SQLCipher/current-CoreFS/staged object at a time, reconciles durable matches after restart, seals the exact source digest, and holds a dedicated `BEGIN IMMEDIATE` source fence through native exact-CAS finalization and bounded metadata verification. Python/PyO3 aggregate publication and `PreparedWritingObject.content` are retired. Browser drafts use a durable ID/client-revision/body-hash handoff token and an exact localStorage re-read before deletion, so concurrent or hashing-window edits are retained at a higher revision. Native preparation records now accept the manifest's UUID Core identity while rejecting ambiguous colon-delimited identities. Fault injection covers crashes after every generated object, finalize failure before publication, post-publication retry, and a synthetic logical aggregate above 1 GiB with one yielded body. Final focused gates passed: affected Python `53`, API-client/desktop `30`, Python-feature preparation bindings `9`, UUID Core identity `1`, scoped Ruff, Rust formatting, and diff hygiene. Task 9 full validation and independent implementation review are next; legacy SQLCipher remains authoritative and no Task 8 publication is authorized.
- 2026-08-13 01:04 MYT - Completed Task 9 validation and independent review through implementation commit `8664cbcca83e7607a9af327169b7fecce36fca43`. Review-driven RED/GREEN hardening now validates PyO3 body bounds before copying, authenticates every durable object on no-op, reconciles published Ready state, streams corrupt-pointer quarantine, prevents contradictory completed/abandoned terminal receipts, preserves vault/CoreFS binary boundaries, and removes every known browser compare/delete data-loss seam. Final affected gates passed: CoreFS library `243 passed`/`1 ignored`, affected Python `103 passed`, desktop `17 passed`, build, format, diff hygiene, health `2 passed`, and independent focused re-review found no remaining code correctness or data-loss issue. An earlier repository-wide run passed `3457` with `2` intentional skips before the final review fixes; every subsequently changed surface passed its full affected gate.
- 2026-08-13 01:04 MYT - Blocked PCF-004 at the approved plaintext-draft acceptance boundary. localStorage provides no atomic compare-and-delete, and a legacy open tab does not honor the new Web Lock; deleting after import can therefore erase an uncooperative concurrent edit. The safe code leaves the plaintext legacy body key untouched and advances imports with a non-sensitive source-digest/monotonic-revision sidecar. This prevents data loss but does not satisfy “Journal drafts are migrated out of plaintext localStorage.” Clearance requires either an approved cleanup protocol that can prove legacy writers are excluded, or an explicit PRD/spec/acceptance revision assigning plaintext cleanup to later work. Parent ownership and legacy SQLCipher/authoritative `HEAD` remain unchanged.
- 2026-08-13 01:25 MYT - Completed the required independent review of the proposed plaintext-draft cleanup addendum and Task 10 after three focused correction passes. The approved proposal limits cleanup authority to verified replacement-only MSI, signed PKG, DEB, and RPM installations; requires an OS reboot, a pre-WebView process-lifetime launch gate, installed-target verification and process census at one-shot capability consumption; retains orphan sidecars unless removed after authorized source-first cleanup; and gives Linux a signed installed-manifest trust anchor. Review found no remaining consequential no-loss, plaintext-removal, lifecycle, replay, privacy, feasibility, or testability gap. PCF-004 remains `blocked` pending explicit user approval of this reviewed design; no Task 10 implementation, release change, or external publication has begun.
- 2026-08-13 01:56 MYT - The user explicitly approved the independently reviewed Task 10 design, clearing the only recorded PCF-004 blocker. Resumed the child and parent row as `in_progress` on branch `codex/pcf-004-resumable-preparation` at `aed28abd4fed566595d9b4abbfc83eaae23d9bc7`, preserving the original `Started:` timestamp. Implementation will follow the reviewed replacement-only package, reboot epoch, launch gate/census, one-shot authority, and source-first cleanup plan test-first; no post-Task-5 publication is authorized.
- 2026-08-13 03:45 MYT - Completed Task 10 implementation locally and passed independent final review with no remaining substantive correctness, no-loss, security/privacy, bounded-memory, loaded-image identity, legacy compatibility, or acceptance-testability finding. Replacement-only MSI/signed-PKG/DEB/RPM packaging, protected predecessor/current identity evidence, the reboot-bound pre-WebView launch gate, native process census, one-shot post-WebView cleanup authority, and exact source-first renderer cleanup are implemented. Native unit `11/11`, process `5/5`, strict desktop Clippy, desktop release-contract `6/6`, cleanup-authority `9/9`, affected Bun `24/24`, desktop build, workflow YAML parsing, repository organization, and diff hygiene passed. PCF-004 remains `in_progress`: closure still requires the protected workflow's actual Windows, macOS, DEB, and RPM signed-package results plus recorded artifact digests, and no post-Task-5 publication or workflow execution is authorized.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_diary_api.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_notes.py -q` (focused bands passed throughout; latest affected PCF-004 band `28 passed`)
  - `cargo test -p anima-corefs --lib --no-fail-fast` (`216 passed`, `1 ignored`)
  - `cargo test -p anima-corefs preparation_tests::formats -- --nocapture` (Task 1 final: `11 passed`)
  - `cargo test -p anima-corefs crypto::tests -- --nocapture` (Task 1 final: `10 passed`)
  - `cargo test -p anima-corefs --lib --no-fail-fast` (Task 1 independent final: `227 passed`, `1 ignored`; one earlier known Windows lease flake passed on exact rerun)
  - `cargo test -p anima-corefs preparation_tests::begin_resume -- --nocapture` (Task 2 final: `5 passed`)
  - `cargo test -p anima-corefs preparation_tests::crash_boundaries -- --nocapture` (Task 2 final: `1 passed`)
  - `cargo test -p anima-corefs --lib --no-fail-fast` (Task 2 independent final: `233 passed`, `1 ignored`)
  - `cargo test -p anima-corefs preparation_tests::prepare_object -- --nocapture` (Task 3 reconciliation audit: `10 passed`)
  - `cargo test -q -p anima-corefs preparation_tests::bounded_large_corpus` (Task 3 reconciliation audit: `1 passed`)
  - `cargo test -q -p anima-corefs transaction::converter::tests` (Task 3 reconciliation audit: `3 passed`)
  - `cargo test -p anima-corefs preparation_tests::seal_finalize -- --nocapture` (Task 4 staging slice: `2 passed` after the required missing-method RED)
  - `cargo test -q -p anima-corefs preparation_tests` (Task 4 staging slice: `30 passed`)
  - `cargo test -q -p anima-corefs --lib --no-fail-fast` (Task 4 staging slice: `217 passed`, `1 ignored`)
  - `cargo test -p anima-corefs preparation_tests::seal_finalize -- --nocapture` (Task 4 final: `11 passed`)
  - `cargo test -p anima-corefs preparation_tests::post_head_recovery -- --nocapture` (Task 4 final: `3 passed`)
  - `cargo test -p anima-corefs --test validation_batch --no-fail-fast` (Task 4 final: `7 passed`)
  - `cargo test -p anima-corefs --lib --no-fail-fast` (Task 4 final: `229 passed`, `1 ignored`)
  - `cargo test -p anima-corefs preparation_tests::terminal -- --nocapture` (Task 5 final: `8 passed`)
  - `cargo test -p anima-corefs preparation_tests::quarantine -- --nocapture` (Task 5 final: `1 passed`)
  - `cargo test -p anima-corefs --test rotation --no-fail-fast` (Task 5 final: `14 passed`)
  - `cargo test -p anima-corefs --lib --no-fail-fast` (Task 5 final: `238 passed`, `1 ignored`)
  - `cargo test -p anima-core --lib corefs_preparation -- --nocapture` (Task 6 default boundary: `2 passed`)
  - `cargo test -p anima-core --features python --lib corefs_preparation -- --nocapture` with embedded CPython link settings (Task 6 boundary/integration: `9 passed`)
  - `cargo test -p anima-core --features python --lib corefs_session::operation_guard_drains_before_close_releases_lease -- --nocapture` with embedded CPython link settings (Task 6 close-drain: `1 passed`)
  - `cargo test -p anima-core --lib corefs_session -- --nocapture` (passed with `0` selected because the session cases are Python-feature-gated); the stronger feature-enabled session band passed `18/19`, with only the pre-existing pinned-root symlink layout case failing before session creation
  - `PYO3_PYTHON=.venv/bin/python cargo check -p anima-core --features python` (passed)
  - `cargo clippy -p anima-corefs --lib -- -A clippy::too_many_arguments -D warnings` (Task 6 passed)
  - `PYO3_PYTHON=.venv/bin/python cargo clippy -p anima-core --features python --lib -- -A clippy::too_many_arguments -A clippy::derivable_impls -A clippy::suspicious_open_options -A clippy::incompatible_msrv -D warnings` (Task 6 passed; the added allowances cover untouched baseline warnings outside the Task 6 diff)
  - `cargo fmt -p anima-corefs -p anima-core --check`, `git diff --check`, and `bun run check:repo` (Task 6 passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_writing_generation.py apps/server/tests/test_corefs_migration.py apps/server/tests/test_diary_api.py -q` (Task 7 final: `32 passed`, one Starlette deprecation warning)
  - `uv run alembic -c apps/server/alembic_core.ini heads` (Task 7: exactly `20260812_0001 (head)`)
  - `bun run lint:server`, `git diff --check`, and `bun run check:repo` (Task 7 passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false .venv/bin/pytest apps/server/tests/test_corefs_writing_generation.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_notes.py apps/server/tests/test_diary_api.py -q` (Task 8 final: `53 passed`, one Starlette deprecation warning)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/journal-draft-migration.test.ts` (Task 8 final: `30 passed`)
  - `PYTHONHOME=... PYO3_PYTHON=... RUSTFLAGS='-L native=... -C link-arg=-lpython3.12' cargo test -p anima-core --features python --lib corefs_preparation -- --nocapture` (Task 8 final: `9 passed`)
  - `cargo test -p anima-corefs preparation_records_accept_the_manifest_uuid_core_identity -- --nocapture` (Task 8 final: `1 passed`)
  - `.venv/bin/ruff check` on the Task 8 server modules/tests, `cargo fmt -p anima-corefs -p anima-core --check`, and `git diff --check` (passed)
  - `cargo fmt --check -p anima-corefs` and `git diff --check` (passed)
  - `cargo clippy -p anima-corefs --lib -- -A clippy::too_many_arguments -D warnings` (passed; the unmodified strict invocation stops on the pre-existing `prepare_object_inner` argument-count warning at `preparation.rs:1649`)
  - `bun run check:repo` (passed)
  - `cargo test -p anima-core --lib` (all `218` tests passed across the final affected run)
  - `bun test apps/desktop/tests/journal-corefs.test.ts apps/desktop/tests/journal-draft-migration.test.ts apps/desktop/tests/journal-html.test.ts` (`8 passed` in the final affected run)
  - `bun run build` (passed)
  - `bun run lint:server` (passed)
  - `bun run check:repo` and `git diff --check` (passed)
  - `cargo test -p anima-corefs --lib --no-fail-fast` (Task 9 final: `243 passed`, `1 ignored`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_diary_api.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_notes.py apps/server/tests/test_corefs_writing_generation.py apps/server/tests/test_corefs_migration.py apps/server/tests/test_vault.py -q` (Task 9 final affected band: `103 passed`)
  - `bun test apps/desktop/tests/journal-draft-migration.test.ts apps/desktop/tests/journal-html.test.ts apps/desktop/tests/diary-turn-into-atom.test.ts` (Task 9 final: `17 passed`)
  - `bun run test` (repository-wide Task 9 pass before final focused review repairs: `3457 passed`, `2 skipped`; all subsequently changed surfaces passed their complete affected gates)
  - `bun run build`, `bun run lint:server`, `cargo fmt --check -p anima-corefs -p anima-core`, `bun run check:repo`, and `git diff --check` (passed)
  - `ANIMA_DATA_DIR=... uv run pytest apps/server/tests/test_health.py -q` (isolated health smoke: `2 passed`)
  - `cargo test -p desktop --lib draft_cleanup` (Task 10 native authority: `11 passed`)
  - `cargo test -p desktop --test draft_cleanup_process` (Task 10 native process/lock authority: `5 passed`)
  - `cargo clippy -p desktop --all-targets -- -D warnings` (Task 10 strict desktop Clippy passed)
  - `bun test apps/desktop/tests/desktop-release-contract.test.ts apps/desktop/tests/journal-draft-cleanup-authority.test.ts apps/desktop/tests/journal-draft-migration.test.ts` (Task 10 affected desktop band: `24 passed`)
  - `bun run --cwd apps/desktop build`, protected-workflow YAML parsing, `bun run check:repo`, and `git diff --check` (Task 10 local gates passed)
  - Independent Task 10 final review reproduced native unit `11/11`, process `5/5`, strict Clippy, desktop release-contract `6/6`, cleanup-authority `9/9`, and diff hygiene with no remaining substantive finding
- Changed paths:
  - `Cargo.lock`
  - `apps/desktop/src/pages/Journal.tsx`
  - `apps/desktop/src/pages/journal/{draft-migration.ts,html.ts}`
  - `apps/desktop/src/features/diary/{DiaryWorkspace.tsx,editor/BlockDragHandle.tsx,lib/draftMigration.ts}`
  - `apps/desktop/tests/{journal-corefs.test.ts,journal-draft-migration.test.ts,journal-html.test.ts}`
  - `apps/server/src/anima_server/api/routes/diary.py`
  - `apps/server/alembic_core/versions/20260812_0001_add_corefs_writing_source_generation.py`
  - `apps/server/src/anima_server/db/session.py`
  - `apps/server/src/anima_server/models/{__init__.py,agent_runtime.py}`
  - `apps/server/src/anima_server/schemas/diary.py`
  - `apps/server/src/anima_server/services/corefs/{diary_migration.py,formats.py,writing-sanitizer-v1.json}`
  - `apps/server/src/anima_server/services/corefs/writing_source.py`
  - `apps/server/src/anima_server/services/vault.py`
  - `apps/server/src/anima_server/services/sessions.py`
  - `apps/server/tests/{conftest.py,test_corefs_diary_migration.py,test_corefs_indexer.py,test_corefs_notes.py,test_diary_api.py}`
  - `apps/server/tests/corefs_writing_test_support.py`
  - `apps/server/tests/{test_corefs_migration.py,test_corefs_writing_generation.py}`
  - `apps/server/tests/test_vault.py`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
  - `packages/anima-core/{Cargo.toml,src/ffi.rs}`
  - `packages/anima-core/src/lib.rs`
  - `packages/anima-corefs/src/{catalog/v2.rs,id.rs,transaction.rs}`
  - `packages/anima-corefs/src/rotation.rs`
  - `packages/anima-corefs/src/crypto.rs`
  - `packages/anima-corefs/src/transaction/{preparation.rs,preparation_tests.rs}`
  - `packages/anima-corefs/src/logical/{backend.rs,mod.rs,path.rs,service.rs,wire.rs}`
  - `packages/anima-corefs/src/transaction/converter.rs`
  - `packages/anima-corefs/tests/{logical_path.rs,logical_snapshot.rs,opaque_id.rs,rotation.rs,validation_batch.rs}`
  - `packages/api-client/src/{client.ts,types.ts}`
  - `.github/workflows/desktop-draft-cleanup-authority.yml`
  - `apps/desktop/src-tauri/{Cargo.toml,build.rs,tauri.conf.json,tauri.linux.conf.json}` and `apps/desktop/src-tauri/install/`
  - `apps/desktop/src-tauri/src/{lib.rs,draft_cleanup.rs}` and `apps/desktop/src-tauri/tests/draft_cleanup_process.rs`
  - `apps/desktop/src/{lib/draftCleanupAuthority.ts,components/database/hooks/useLocalStorage.ts,features/diary/DiaryWorkspace.tsx,features/diary/lib/draftMigration.ts}`
  - `apps/desktop/tests/{desktop-release-contract.test.ts,journal-draft-cleanup-authority.test.ts}`
  - `scripts/{build-macos-pkg.ts,desktop-package-environment.ts,package-desktop.ts,prepare-desktop-release.ts,prepare-linux-install-identity.ts,verify-desktop-release-contract.ts}`
  - `docs/superpowers/{plans/2026-08-02-corefs-resumable-preparation.md,specs/2026-08-02-corefs-resumable-preparation-design.md}`
  - `tickets/portable-core-filesystem/{PCF-000-portable-core-filesystem.md,PCF-004-diary-notes.md}`
- Notes:
  - Legacy SQLCipher remains authoritative and `VALIDATION_HEAD` remains inactive; no partial or authoritative cutover occurred.
  - Tasks 5 through 10 are implemented and independently reviewed locally. Authoritative `HEAD` remains untouched.
  - Repository-wide validation passed `3457` tests with `2` intentional skips before the final focused review repairs; the final changed Rust, Python, desktop, build, formatting, and diff surfaces all passed their complete affected gates.
  - Strict Clippy remains blocked only by documented untouched baseline warnings outside the PCF-004 diff.
  - Required closeout evidence: run the protected replacement-install workflow against signed Windows, macOS, DEB, and RPM packages, record all four results and artifact digests, and only then mark plaintext cleanup accepted or PCF-004 complete. That external publication/workflow action is not currently authorized.
