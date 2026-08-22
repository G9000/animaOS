# PCF-005 - Canonical threads, messages, and transcript merge

- Status: done
- Priority: P0
- Scope: `apps/server` chat/thread/transcript services
- Parent: `PCF-000`
- Depends on: `PCF-003`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-5-canonical-threads-messages-and-transcript-merge`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-13 11:36 MYT
- Started: 2026-08-13 04:00 MYT
- Completed: 2026-08-13 11:36 MYT

## Goal

Make versioned encrypted message segments canonical and merge active PostgreSQL, legacy SQLCipher, and transcript history safely.

## Deliverables

- Message event/CAS/segment implementation.
- Canonical visible-message projection excluding internal execution state.
- Thread APIs and display backed by CoreFS.
- Idempotent active/archive merge with conflict quarantine.
- Inactive shadow-catalog validation until PCF-008; after activation Runtime messages retain references/operational metadata, not duplicate plaintext visible bodies.
- Unique `core.conversations` root binding with default `owner=shared`/`agentAccess=manage` and stable-folder resolution across rename, move, and restart.

## Acceptance

- Rollover, ordering, edit/delete conflict, terminal deletion, corruption, and concurrency tests pass.
- User-visible history and attachments survive migration.
- Tool calls, thinking, prompts, traces, and retrieval internals are absent from canonical messages.
- Pre-cutover writes remain on legacy authority; no unmarked CoreFS mutation can bypass the global cutover marker.
- Thread list, display, append, edit/delete, and reactivation resolve the same `core.conversations` folder ID after rename, move, and restart.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 18:58 MYT - Added the `core.conversations` root, shared/manage defaults, and rename/move/restart acceptance coverage.
- 2026-08-13 04:00 MYT - Codex claimed PCF-005 after rechecking that PCF-003 is done and no existing branch, worktree, owner, or activity entry claims the ticket. Started on local stacked branch `codex/pcf-005-conversations` from published PCF-004 head `83f2490058f9e8c6565716cc5eee0b44bed530d3` in `/Users/julio/animaOS`; PCF-004 remains independently open on its cost-deferred package-evidence gate.
- 2026-08-13 11:36 MYT - Completed PCF-005 locally. Canonical visible-message projection, immutable event/CAS semantics, bounded hash-chained segments, exact degraded ranges, active/archive/legacy deduplication with encrypted conflict quarantine, shared/manage `core.conversations` validation policy, stable-role reads across rename/move/restart, combined inactive shadow preparation, and fail-closed authority routing are implemented. The complete focused band passed `101`, adjacent PCF-004 regressions passed `50`, native validation passed `8`, strict CoreFS Clippy, scoped rustfmt, server lint/build, complete repository build, native checks, and diff hygiene passed. Legacy authority remains unchanged until PCF-008 authenticates the global marker and enables the already-frozen public mutation boundary; PCF-006 owns final gallery placement and asset-link reconciliation. No PCF-005 publication, PR creation, review request, monitoring, or merge was authorized or performed.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_messages.py apps/server/tests/test_corefs_conversation_migration.py apps/server/tests/test_multi_thread.py apps/server/tests/test_p5_transcript_archive.py -q` - `101 passed`, one upstream Starlette deprecation warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_diary_api.py -q` - `50 passed`, one upstream Starlette deprecation warning.
  - `cargo test -p anima-corefs --test validation_batch` - `8 passed`.
  - `cargo clippy -p anima-corefs --all-targets -- -D warnings` - passed.
  - `cargo check -p anima-corefs -p anima-core` - passed.
  - `rustfmt --edition 2021 --check` over all touched Rust files - passed.
  - `bun run lint:server` - passed.
  - `bun run build:server` - passed.
  - `bun run build` - server, desktop, and Animus passed; Vite reported only its existing large-chunk advisory.
  - `git diff --check` - passed.
- Changed paths:
  - `apps/server/src/anima_server/api/routes/chat.py`
  - `apps/server/src/anima_server/api/routes/threads.py`
  - `apps/server/src/anima_server/services/agent/conversation_search.py`
  - `apps/server/src/anima_server/services/agent/eager_consolidation.py`
  - `apps/server/src/anima_server/services/agent/persistence.py`
  - `apps/server/src/anima_server/services/agent/service.py`
  - `apps/server/src/anima_server/services/agent/thread_manager.py`
  - `apps/server/src/anima_server/services/agent/transcript_archive.py`
  - `apps/server/src/anima_server/services/agent/transcript_search.py`
  - `apps/server/src/anima_server/services/corefs/conversation_authority.py`
  - `apps/server/src/anima_server/services/corefs/conversation_migration.py`
  - `apps/server/src/anima_server/services/corefs/diary_migration.py`
  - `apps/server/src/anima_server/services/corefs/messages.py`
  - `apps/server/src/anima_server/services/corefs/writing_source.py`
  - `apps/server/src/anima_server/services/sessions.py`
  - `apps/server/tests/test_corefs_conversation_migration.py`
  - `apps/server/tests/test_corefs_messages.py`
  - `packages/anima-core/src/ffi.rs`
  - `packages/anima-corefs/src/transaction.rs`
  - `packages/anima-corefs/src/transaction/cache_tests.rs`
  - `packages/anima-corefs/src/transaction/converter.rs`
  - `packages/anima-corefs/src/transaction/preparation.rs`
  - `packages/anima-corefs/tests/validation_batch.rs`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
  - `tickets/portable-core-filesystem/PCF-005-conversations.md`
  - `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`
- Notes:
  - Residual risks/follow-ups: none within PCF-005's inactive-shadow and fail-closed authority scope. PCF-008 remains the explicit authenticated global cutover/public-mutation enablement boundary; PCF-006 owns final `core.gallery` placement and attachment-link reconciliation. The local PCF-005 branch is not published because no separate PCF-005 publication authority has been granted.
