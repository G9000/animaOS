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
- Updated: 2026-08-12 20:00 MYT
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
  - `cargo fmt --check -p anima-corefs` and `git diff --check` (passed)
  - `cargo clippy -p anima-corefs --lib -- -A clippy::too_many_arguments -D warnings` (passed; the unmodified strict invocation stops on the pre-existing `prepare_object_inner` argument-count warning at `preparation.rs:1649`)
  - `bun run check:repo` (passed)
  - `cargo test -p anima-core --lib` (all `218` tests passed across the final affected run)
  - `bun test apps/desktop/tests/journal-corefs.test.ts apps/desktop/tests/journal-draft-migration.test.ts apps/desktop/tests/journal-html.test.ts` (`8 passed` in the final affected run)
  - `bun run build` (passed)
  - `bun run lint:server` (passed)
  - `bun run check:repo` and `git diff --check` (passed)
- Changed paths:
  - `Cargo.lock`
  - `apps/desktop/src/pages/Journal.tsx`
  - `apps/desktop/src/pages/journal/{draft-migration.ts,html.ts}`
  - `apps/desktop/tests/{journal-corefs.test.ts,journal-draft-migration.test.ts,journal-html.test.ts}`
  - `apps/server/src/anima_server/api/routes/diary.py`
  - `apps/server/src/anima_server/schemas/diary.py`
  - `apps/server/src/anima_server/services/corefs/{diary_migration.py,formats.py,writing-sanitizer-v1.json}`
  - `apps/server/src/anima_server/services/sessions.py`
  - `apps/server/tests/{conftest.py,test_corefs_diary_migration.py,test_corefs_indexer.py,test_corefs_notes.py,test_diary_api.py}`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
  - `packages/anima-core/{Cargo.toml,src/ffi.rs}`
  - `packages/anima-corefs/src/{catalog/v2.rs,id.rs,transaction.rs}`
  - `packages/anima-corefs/src/crypto.rs`
  - `packages/anima-corefs/src/transaction/{preparation.rs,preparation_tests.rs}`
  - `packages/anima-corefs/src/logical/{backend.rs,mod.rs,path.rs,service.rs,wire.rs}`
  - `packages/anima-corefs/src/transaction/converter.rs`
  - `packages/anima-corefs/tests/{logical_path.rs,logical_snapshot.rs,opaque_id.rs,validation_batch.rs}`
  - `packages/api-client/src/{client.ts,types.ts}`
  - `tickets/portable-core-filesystem/{PCF-000-portable-core-filesystem.md,PCF-004-diary-notes.md}`
- Notes:
  - Legacy SQLCipher remains authoritative and `VALIDATION_HEAD` remains inactive; no partial or authoritative cutover occurred.
  - Task 4 is complete. It publishes only the inactive validation catalog; authoritative `HEAD` remains untouched. Task 5 abandonment, quarantine, FRK rotation, and bounded session semantics is next.
  - A full `bun run test` was attempted twice but remained compute-active beyond five-minute tool bounds without a summary; no repository-wide pass is claimed.
  - Strict Clippy remains blocked only by documented untouched baseline warnings outside the PCF-004 diff.
  - The protocol direction, independently reviewed written spec, user approval, and independent implementation-plan review are complete. The reviewed plan is ready for execution-mode handoff.
