# CoreFS legacy retirement and maintenance implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement the tasks with regression and independent review gates.

**Goal:** Complete PCF-009 and PCF-010: retire unsupported pre-release persistence and provide authenticated maintenance for the current Portable Core format.

**Architecture:** Preserve SQLCipher Soul identity and memory, native CoreFS authored content, and disposable Runtime. Follow the approved greenfield amendment in the existing Portable Core design; unsupported formats fail explicitly rather than activating legacy database/file readers. Current-format encrypted data, transaction recovery, transfer, rollback, and keys remain supported. Maintenance protects HEAD, the last known good predecessor, 30 days of history, and operation/backup pins.

**Tech stack:** Python/FastAPI/SQLAlchemy/Alembic/SQLCipher, Rust CoreFS/PyO3, React/TypeScript.

## Authority and execution

- User approved removing all legacy support and continuing the remaining CoreFS work on 2026-10-09. This renews local PCF-009/010 execution authority, not publication, merge, or deletion of the actual user's Core.
- The user selected a 30-day restore window. Permanent purge and key retirement still require authenticated, bound runtime confirmation and verified current backups.
- The approved 2026-08-16 greenfield amendment supersedes observation/legacy-conversion requirements; current-format schema evolution must preserve existing supported records.
- Implement and validate in the managed `corefs-legacy-retirement` worktree from `685e5a13`, then integrate the verified changes through a backed-up three-way merge that preserves the original desktop edits. Only the parent agent updates ticket lifecycle.

## Task 1: Retire legacy account, credentials, and Soul schema (PCF-009)

- [x] Add regressions proving unsupported legacy unlock/recovery/import fail closed and fresh/current-format auth, password/recovery rotation, and transfer still work.
- [x] Replace app/account `User`/`UserKey` mappings with an opaque `SoulOwner` numeric compatibility identity and versioned Soul keyslots; read profile fields from authenticated CoreFS account profiles. Update active consumers and retained foreign keys.
- [x] Introduce a forward Alembic revision with verified lossless retained-row/FK handling. Remove obsolete app tables and mappings, persisted vectors/embedding caches, and bootstrap schema repair fallbacks. Preserve historical migrations needed to reach the new supported head.
- [x] Before writing that revision, enumerate actual retained tables/FKs/embedding columns and provenance conversions. Retained identity/self-model/profile/emotions, memory items/claims/evidence/tags/episodes, user-profile evidence, procedural experiences/skills, foresight, graph, latent traces/tendencies/reconsolidation/initiative/dream records, SoulOwner and SoulKeyslot must preserve counts and deterministic hashes. Cross-compartment thread/message/run references become authenticated stable Core URIs, never dangling FKs. Run SQLCipher vacuum only on disposable verified fixtures or through the authenticated maintenance gate after lossless checks.
- [x] Remove old shared-database discovery, vault/capsule import/export, plaintext Soul/device migrations, transcript database fallback, legacy credential upgrade/rewrap, and other pre-release readers. Keep current-format transfer snapshot helpers in a small focused module.
- [x] Run focused auth/recovery/Soul-purity tests, migrate a disposable supported-format fixture, compare retained counts/hashes, inspect foreign keys, then review requirements and correctness independently.

## Task 2: Authenticated retention and garbage collection (PCF-010)

- [x] Add adversarial native tests for retained/pinned reachability, corrupt catalogs, unsafe paths, concurrent HEAD changes, interruption, and idempotent resume before implementation.
- [x] Implement dry-run inventory and native maintenance under the same Core-wide commit lock. Authenticate catalogs and preserve protected generations; quarantine unreferenced candidates with crash-safe resume and revalidation before sweep. Persist no private logical names in journals.
- [x] Expiring historical catalogs never permanently purges current encrypted trash: current trash remains reachable until the distinct confirmed purge transaction. Incomplete/corrupt catalog, operation-pin, backup, or journal evidence blocks deletion; it never proves orphanhood.
- [x] Implement user-only trash purge with recent reauthentication and a one-use challenge bound to Core, trash revision, recursive inventory, explicit retention waiver, and known backup warnings. Deny agent/client callers and active operations.
- [x] Purge publishes an authenticated trash-free catalog, prunes only explicitly waived historical references, proves zero retained wrapped Object-DEK references, then retires the target object's key material. Report SSD wear-leveling and exported/known backup copies honestly; local purge cannot erase those copies.
- [x] Retire decrypt-only FRK slots only after zero retained references/pins, verified active-generation backup, and both password/recovery reopen checks; restore the pre-retirement manifest if post-check fails. Never retire a shared FRK to purge one object.
- [x] Add authenticated maintenance API, client contracts, and a Security settings surface with inventory, progress, resume/cancel, backup blockers, and explicit confirmations. Default retention is 30 days.
- [x] Verify current-format transfer, restore, rotation, Runtime rebuild, and maintenance failure-injection tests, then independent spec/correctness review.

## Task 3: Integrated validation and tracked closeout

- [x] Run focused server/native/desktop checks followed by required build, lint, server tests, health, auth/chat/memory/settings smoke where a disposable runtime can be started.
- [x] Record exact environmental limitations without weakening encryption or altering the user's actual Core.
- [x] Apply the isolated, verified changes to the original checkout without overwriting unrelated dirt; repeat any integration-sensitive checks.
- [x] Record paths, validation, risks, and matching parent/child completion only when all required acceptance is met. No push, PR, or merge is authorized.

## Current validation and outstanding work

- 2026-10-09 12:04 MYT: PCF-009, PCF-010, and the parent initiative are complete locally. The explicitly approved final helper removal is applied in the original checkout. Fresh complete backend verification passes 3529 cases across 224 modules, with one platform skip and 11 configured exclusions; build, required Ruff, organization, scoped documentation, and independent review pass. Current native authentication/keyslots and real Core data are preserved. Previous native/desktop/maintenance evidence remains recorded below.
- The integrated original checkout passes the active-app build, required source/test Ruff, repository organization check, and 402 desktop/client tests. Four scoped current architecture/plan documents have zero consistency findings; exact current storage inventory and 26 maintenance policy/API tests pass.
- Disposable encrypted supported Full/Soul upgrade fixtures preserve retained counts, deterministic hashes, FK chains, and independently reopened encrypted recovery originals. Interrupted VACUUM retry passes; seven actual encrypted reopen/snapshot/portability checks pass with zero skips.
- The extended real FRK rotation/checkpoint/prune/Runtime-loss rebuild/retirement API sequence passes in the original checkout (258.41 seconds): a rotated Full archive restores independently with clean inventory; pruned native history and metadata survive empty Runtime and production rebuild without plaintext body/JSON rows; both full Soul/password/recovery credentials reopen after old FRK retirement, old sessions revoke, authored bytes/HEAD remain unchanged, and health returns 200. Adjacent archive/activation/transfer/maintenance tests: 88 passed.
- Complete configured backend coverage passes 3,527 distinct cases across all 224 modules, with one POSIX-permissions test skipped on Windows and 11 configured retrieval/golden exclusions. Three shards passed 1,085/828/758; shard 3's earlier 520 passing cases plus one skip and resumed 324 passing cases cover its modules. Twelve added independent regressions pass in the final integrated 190-case memory domain (69.98 seconds). Current conversation, tool continuation, Runtime loss/rebuild, document capture, native ingestion, and Windows descriptor writers have passing evidence. Source/test Ruff and Alembic heads (`20261009_0001`, one head) pass.
- Independent final review found no remaining actionable defect in implemented scope. Native-index tests require filesystem access outside the command sandbox; an interrupted sandboxed rerun was replaced by the successful integrated native-access run. These local checks do not establish hosted CI, live-provider behavior, or signed cross-platform release readiness.
- The prior automatic approval-review blocker is cleared by the user's explicit 'finish it' instruction in response to the identified patch. Unused numeric-owner wrapping/legacy SQLCipher derivation/Vault parameters and the discarded registration wrapping step are removed. Registration generates independent 32-byte domain secrets for current AAD-bound keyslot publication; current native credential/keyslot encryption is retained. The saved proposal remains as implementation evidence.
- The backed-up three-way integration applied 274 files with zero merge conflicts, preserving all 136 unrelated original file hashes and cleanly merging five overlapping user-edited files. Final memory fixes and tracking updates are synchronized with explicit hash guards. No real user Core was opened or maintained.

## Complete changed-path manifest

The following paths record the complete local PCF-009/010 continuation scope. Removed production readers have no replacement legacy runtime path; test-only fixtures are explicitly marked in their modules.

| Path | Change |
|---|---|
| `.gitignore` | modified |
| `apps/desktop/src/components/layout/LayoutSidebar.tsx` | modified |
| `apps/desktop/src/lib/portablePreferences.ts` | modified |
| `apps/desktop/src/lib/userIdentity.ts` | added |
| `apps/desktop/src/pages/Profile.tsx` | modified |
| `apps/desktop/src/pages/auth/Login.tsx` | modified |
| `apps/desktop/src/pages/auth/Register.tsx` | modified |
| `apps/desktop/src/pages/settings/CoreMaintenancePanel.tsx` | added |
| `apps/desktop/src/pages/settings/SecuritySettings.tsx` | modified |
| `apps/desktop/src/pages/settings/maintenanceReview.ts` | added |
| `apps/desktop/tests/corefs-maintenance.test.ts` | added |
| `apps/desktop/tests/corefs-owner-identity.test.ts` | added |
| `apps/desktop/tests/portable-preferences.test.ts` | modified |
| `apps/desktop/tests/settings-storage-classification.test.ts` | modified |
| `apps/server/alembic_core/versions/20261009_0001_retire_legacy_soul.py` | added |
| `apps/server/src/anima_server/api/routes/auth.py` | modified |
| `apps/server/src/anima_server/api/routes/chat.py` | modified |
| `apps/server/src/anima_server/api/routes/consciousness.py` | modified |
| `apps/server/src/anima_server/api/routes/core.py` | modified |
| `apps/server/src/anima_server/api/routes/corefs_maintenance.py` | added |
| `apps/server/src/anima_server/api/routes/db.py` | modified |
| `apps/server/src/anima_server/api/routes/diary.py` | modified |
| `apps/server/src/anima_server/api/routes/documents.py` | modified |
| `apps/server/src/anima_server/api/routes/images.py` | modified |
| `apps/server/src/anima_server/api/routes/knowledge.py` | modified |
| `apps/server/src/anima_server/api/routes/presence.py` | modified |
| `apps/server/src/anima_server/api/routes/soul.py` | modified |
| `apps/server/src/anima_server/api/routes/tasks.py` | modified |
| `apps/server/src/anima_server/api/routes/telegram.py` | modified |
| `apps/server/src/anima_server/api/routes/threads.py` | modified |
| `apps/server/src/anima_server/api/routes/users.py` | modified |
| `apps/server/src/anima_server/api/routes/ws.py` | modified |
| `apps/server/src/anima_server/cli/db.py` | modified |
| `apps/server/src/anima_server/contracts/auth.py` | modified |
| `apps/server/src/anima_server/db/__init__.py` | modified |
| `apps/server/src/anima_server/db/session.py` | modified |
| `apps/server/src/anima_server/db/user_store.py` | modified |
| `apps/server/src/anima_server/main.py` | modified |
| `apps/server/src/anima_server/models/__init__.py` | modified |
| `apps/server/src/anima_server/models/agent_runtime.py` | modified |
| `apps/server/src/anima_server/models/consciousness.py` | modified |
| apps/server/src/anima_server/models/links.py | removed |
| apps/server/src/anima_server/models/presence.py | removed |
| `apps/server/src/anima_server/models/provenance.py` | added |
| `apps/server/src/anima_server/models/soul_consciousness.py` | modified |
| `apps/server/src/anima_server/models/soul_owner.py` | added |
| apps/server/src/anima_server/models/task.py | removed |
| apps/server/src/anima_server/models/user.py | removed |
| apps/server/src/anima_server/models/user_key.py | removed |
| `apps/server/src/anima_server/schemas/corefs_maintenance.py` | added |
| `apps/server/src/anima_server/services/agent/agent_experience.py` | modified |
| `apps/server/src/anima_server/services/agent/attachments.py` | modified |
| `apps/server/src/anima_server/services/agent/biography_preview.py` | modified |
| `apps/server/src/anima_server/services/agent/compaction.py` | modified |
| `apps/server/src/anima_server/services/agent/derived_soul_state.py` | added |
| `apps/server/src/anima_server/services/agent/distillation.py` | modified |
| `apps/server/src/anima_server/services/agent/document_tools.py` | modified |
| `apps/server/src/anima_server/services/agent/eager_consolidation.py` | modified |
| `apps/server/src/anima_server/services/agent/embedding_contract.py` | modified |
| `apps/server/src/anima_server/services/agent/embeddings.py` | modified |
| `apps/server/src/anima_server/services/agent/episodes.py` | modified |
| `apps/server/src/anima_server/services/agent/forgetting.py` | modified |
| `apps/server/src/anima_server/services/agent/inner_monologue.py` | modified |
| `apps/server/src/anima_server/services/agent/latent_traces.py` | modified |
| `apps/server/src/anima_server/services/agent/memory_blocks.py` | modified |
| `apps/server/src/anima_server/services/agent/persistence.py` | modified |
| `apps/server/src/anima_server/services/agent/proactive.py` | modified |
| `apps/server/src/anima_server/services/agent/provenance.py` | modified |
| `apps/server/src/anima_server/services/agent/service.py` | modified |
| `apps/server/src/anima_server/services/agent/sleep_agent.py` | modified |
| `apps/server/src/anima_server/services/agent/soul_writer.py` | modified |
| `apps/server/src/anima_server/services/agent/thread_manager.py` | modified |
| `apps/server/src/anima_server/services/agent/tools.py` | modified |
| apps/server/src/anima_server/services/agent/transcript_archive.py | removed |
| `apps/server/src/anima_server/services/agent/transcript_search.py` | modified |
| `apps/server/src/anima_server/services/agent/user_profile.py` | modified |
| `apps/server/src/anima_server/services/auth.py` | modified |
| `apps/server/src/anima_server/services/core.py` | modified |
| `apps/server/src/anima_server/services/corefs/account_profile.py` | modified |
| `apps/server/src/anima_server/services/corefs/archive_transfer.py` | modified |
| `apps/server/src/anima_server/services/corefs/asset_authority.py` | modified |
| apps/server/src/anima_server/services/corefs/asset_migration.py | removed |
| `apps/server/src/anima_server/services/corefs/asset_mutations.py` | modified |
| `apps/server/src/anima_server/services/corefs/catalog_reader.py` | added |
| `apps/server/src/anima_server/services/corefs/client_access.py` | modified |
| `apps/server/src/anima_server/services/corefs/conversation_authority.py` | modified |
| apps/server/src/anima_server/services/corefs/conversation_migration.py | removed |
| `apps/server/src/anima_server/services/corefs/conversation_mutations.py` | modified |
| `apps/server/src/anima_server/services/corefs/credentials.py` | modified |
| apps/server/src/anima_server/services/corefs/diary_migration.py | removed |
| `apps/server/src/anima_server/services/corefs/formats.py` | modified |
| `apps/server/src/anima_server/services/corefs/gc.py` | added |
| `apps/server/src/anima_server/services/corefs/greenfield.py` | modified |
| `apps/server/src/anima_server/services/corefs/image_authority.py` | modified |
| `apps/server/src/anima_server/services/corefs/indexer.py` | modified |
| `apps/server/src/anima_server/services/corefs/keyslots.py` | modified |
| `apps/server/src/anima_server/services/corefs/knowledge_authority.py` | modified |
| apps/server/src/anima_server/services/corefs/legacy_soul.py | removed |
| `apps/server/src/anima_server/services/corefs/maintenance_coordination.py` | added |
| `apps/server/src/anima_server/services/corefs/messages.py` | modified |
| `apps/server/src/anima_server/services/corefs/preferences.py` | modified |
| `apps/server/src/anima_server/services/corefs/rotation.py` | modified |
| `apps/server/src/anima_server/services/corefs/sealed_runtime.py` | modified |
| `apps/server/src/anima_server/services/corefs/soul_provenance.py` | added |
| `apps/server/src/anima_server/services/corefs/soul_store.py` | modified |
| `apps/server/src/anima_server/services/corefs/task_authority.py` | modified |
| `apps/server/src/anima_server/services/corefs/task_mutations.py` | modified |
| `apps/server/src/anima_server/services/corefs/transactions.py` | modified |
| `apps/server/src/anima_server/services/corefs/transfer.py` | modified |
| `apps/server/src/anima_server/services/corefs/transfer_jobs.py` | modified |
| `apps/server/src/anima_server/services/corefs/writing_authority.py` | modified |
| `apps/server/src/anima_server/services/corefs/writing_mutations.py` | modified |
| apps/server/src/anima_server/services/corefs/writing_source.py | removed |
| `apps/server/src/anima_server/services/crypto.py` | modified |
| apps/server/src/anima_server/services/diary.py | removed |
| `apps/server/src/anima_server/services/documents/__init__.py` | modified |
| `apps/server/src/anima_server/services/documents/pdf_workflow.py` | modified |
| `apps/server/src/anima_server/services/documents/store.py` | modified |
| `apps/server/src/anima_server/services/eval_reset.py` | modified |
| apps/server/src/anima_server/services/images/backfill.py | removed |
| apps/server/src/anima_server/services/images/deletion.py | removed |
| `apps/server/src/anima_server/services/images/store.py` | modified |
| `apps/server/src/anima_server/services/ingestion/adapters/documents.py` | modified |
| `apps/server/src/anima_server/services/ingestion/adapters/images.py` | modified |
| `apps/server/src/anima_server/services/ingestion/adapters/text.py` | modified |
| `apps/server/src/anima_server/services/ingestion/adapters/web.py` | modified |
| `apps/server/src/anima_server/services/ingestion/retrieval.py` | modified |
| `apps/server/src/anima_server/services/ingestion/sources.py` | modified |
| `apps/server/src/anima_server/services/integration_registry.py` | modified |
| `apps/server/src/anima_server/services/presence_config.py` | modified |
| `apps/server/src/anima_server/services/recovery.py` | modified |
| `apps/server/src/anima_server/services/regeneration_work.py` | modified |
| `apps/server/src/anima_server/services/sessions.py` | modified |
| apps/server/src/anima_server/services/vault.py | removed |
| `apps/server/tests/corefs_asset_test_support.py` | added |
| `apps/server/tests/corefs_catalog_test_support.py` | added |
| `apps/server/tests/corefs_conversation_test_support.py` | added |
| `apps/server/tests/corefs_document_test_support.py` | added |
| `apps/server/tests/corefs_ingestion_test_support.py` | added |
| `apps/server/tests/corefs_writing_fixture_support.py` | added |
| `apps/server/tests/corefs_writing_test_support.py` | modified |
| `apps/server/tests/test_agent_anthropic_client.py` | modified |
| `apps/server/tests/test_agent_biography_preview.py` | modified |
| `apps/server/tests/test_agent_compaction.py` | modified |
| `apps/server/tests/test_agent_consolidation.py` | modified |
| `apps/server/tests/test_agent_episodes.py` | modified |
| `apps/server/tests/test_agent_experience.py` | modified |
| `apps/server/tests/test_agent_memory_blocks.py` | modified |
| `apps/server/tests/test_agent_openai_compatible_client.py` | modified |
| `apps/server/tests/test_agent_reflection.py` | modified |
| `apps/server/tests/test_agent_service.py` | modified |
| `apps/server/tests/test_api_db.py` | modified |
| `apps/server/tests/test_approval_reentry.py` | modified |
| `apps/server/tests/test_auth.py` | modified |
| `apps/server/tests/test_background_dirty_checks.py` | modified |
| `apps/server/tests/test_batch_segmenter.py` | modified |
| `apps/server/tests/test_chat.py` | modified |
| `apps/server/tests/test_chat_attachments.py` | modified |
| `apps/server/tests/test_chat_document_context.py` | modified |
| `apps/server/tests/test_chat_image_assets.py` | modified |
| `apps/server/tests/test_cognitive_pipeline.py` | modified |
| `apps/server/tests/test_consciousness.py` | modified |
| `apps/server/tests/test_contextual_rerank.py` | modified |
| apps/server/tests/test_corefs_account_migration.py | removed |
| `apps/server/tests/test_corefs_active_core_registry.py` | modified |
| `apps/server/tests/test_corefs_archive_transfer.py` | modified |
| `apps/server/tests/test_corefs_archive_windows_paths.py` | added |
| `apps/server/tests/test_corefs_assets.py` | modified |
| `apps/server/tests/test_corefs_authority.py` | modified |
| `apps/server/tests/test_corefs_capture_current.py` | added |
| `apps/server/tests/test_corefs_client_access.py` | modified |
| `apps/server/tests/test_corefs_content_retirement.py` | added |
| `apps/server/tests/test_corefs_conversation_migration.py` | modified |
| apps/server/tests/test_corefs_device_state_migration.py | removed |
| `apps/server/tests/test_corefs_diary_migration.py` | modified |
| `apps/server/tests/test_corefs_document_migration.py` | modified |
| `apps/server/tests/test_corefs_gc.py` | added |
| `apps/server/tests/test_corefs_gc_current_core.py` | added |
| `apps/server/tests/test_corefs_indexer.py` | modified |
| `apps/server/tests/test_corefs_keyslots.py` | modified |
| `apps/server/tests/test_corefs_knowledge_sources.py` | modified |
| `apps/server/tests/test_corefs_legacy_retirement.py` | added |
| `apps/server/tests/test_corefs_live_provenance.py` | added |
| `apps/server/tests/test_corefs_maintenance_api.py` | added |
| `apps/server/tests/test_corefs_migration.py` | modified |
| `apps/server/tests/test_corefs_notes.py` | modified |
| `apps/server/tests/test_corefs_preferences.py` | modified |
| `apps/server/tests/test_corefs_runtime_privacy.py` | modified |
| `apps/server/tests/test_corefs_runtime_retirement.py` | added |
| `apps/server/tests/test_corefs_soul_purity.py` | added |
| `apps/server/tests/test_corefs_state_inventory.py` | modified |
| `apps/server/tests/test_corefs_transfer.py` | modified |
| `apps/server/tests/test_corefs_writing_generation.py` | modified |
| `apps/server/tests/test_crypto.py` | modified |
| `apps/server/tests/test_diary_api.py` | modified |
| `apps/server/tests/test_document_rag.py` | modified |
| `apps/server/tests/test_document_reparse.py` | modified |
| `apps/server/tests/test_document_store.py` | modified |
| `apps/server/tests/test_document_tools.py` | modified |
| `apps/server/tests/test_documents_api.py` | modified |
| `apps/server/tests/test_embedding_contract.py` | modified |
| `apps/server/tests/test_encrypted_core_regression.py` | modified |
| `apps/server/tests/test_eval_harness.py` | modified |
| `apps/server/tests/test_evidence_retrieval.py` | modified |
| `apps/server/tests/test_foresight.py` | modified |
| `apps/server/tests/test_forgetting.py` | modified |
| `apps/server/tests/test_health_checks.py` | modified |
| `apps/server/tests/test_html_ingestion.py` | modified |
| `apps/server/tests/test_hybrid_retrieval.py` | modified |
| `apps/server/tests/test_image_assets.py` | modified |
| apps/server/tests/test_image_backfill.py | removed |
| `apps/server/tests/test_image_deletion.py` | modified |
| `apps/server/tests/test_image_indexing.py` | modified |
| `apps/server/tests/test_image_retrieval_context.py` | modified |
| `apps/server/tests/test_inner_life_ambient_dream.py` | modified |
| `apps/server/tests/test_inner_life_distillation.py` | modified |
| `apps/server/tests/test_inner_life_dream_edge.py` | modified |
| `apps/server/tests/test_inner_life_e2e.py` | modified |
| `apps/server/tests/test_inner_life_held_thought.py` | modified |
| `apps/server/tests/test_inner_life_initiative.py` | modified |
| `apps/server/tests/test_inner_life_latent.py` | modified |
| `apps/server/tests/test_inner_life_reconsolidation.py` | modified |
| `apps/server/tests/test_knowledge_api.py` | modified |
| `apps/server/tests/test_knowledge_autocompile.py` | modified |
| `apps/server/tests/test_knowledge_graph.py` | modified |
| `apps/server/tests/test_knowledge_retrieval.py` | modified |
| `apps/server/tests/test_llm_wiki_compiler.py` | modified |
| `apps/server/tests/test_memory_candidates.py` | modified |
| `apps/server/tests/test_memory_item_evidence.py` | modified |
| `apps/server/tests/test_memory_multilingual_baseline.py` | modified |
| `apps/server/tests/test_memory_provenance_backfill.py` | modified |
| `apps/server/tests/test_memory_recall_regressions.py` | modified |
| `apps/server/tests/test_memory_retrieval_rebuild.py` | modified |
| `apps/server/tests/test_memory_scored_retrieval.py` | modified |
| `apps/server/tests/test_multi_thread.py` | modified |
| `apps/server/tests/test_p3_self_model_split.py` | modified |
| `apps/server/tests/test_p5_transcript_archive.py` | modified |
| `apps/server/tests/test_pattern_synthesis.py` | modified |
| `apps/server/tests/test_pdf_workflow_checkpoints.py` | modified |
| `apps/server/tests/test_pending_memory_ops.py` | modified |
| `apps/server/tests/test_phase3_storage.py` | modified |
| `apps/server/tests/test_proactive_image_memory.py` | modified |
| `apps/server/tests/test_recovery.py` | modified |
| `apps/server/tests/test_retrieval_scoring.py` | modified |
| `apps/server/tests/test_retry_hygiene.py` | modified |
| `apps/server/tests/test_runtime_db.py` | modified |
| `apps/server/tests/test_single_user_memory_baseline_probes.py` | modified |
| `apps/server/tests/test_sleep_agent.py` | modified |
| `apps/server/tests/test_soul_block_locking.py` | modified |
| `apps/server/tests/test_soul_writer.py` | modified |
| `apps/server/tests/test_source_ingestion_adapters.py` | modified |
| `apps/server/tests/test_sqlite_fk_enforcement.py` | modified |
| `apps/server/tests/test_tasks_api.py` | modified |
| `apps/server/tests/test_telegram_routes.py` | modified |
| `apps/server/tests/test_threads_api.py` | modified |
| `apps/server/tests/test_ttft_optimizations.py` | modified |
| `apps/server/tests/test_user_profile.py` | modified |
| `apps/server/tests/test_users.py` | modified |
| `docs/CHANGELOG.md` | modified |
| `docs/architecture/system/anima-core-filesystem.md` | modified |
| `docs/architecture/system/database-schema.md` | modified |
| `docs/architecture/system/portable-state-inventory.md` | modified |
| `docs/prds/portable-core-filesystem-v1.md` | modified |
| `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md` | modified |
| `docs/superpowers/plans/2026-10-09-corefs-retirement-maintenance.md` | added |
| `docs/superpowers/specs/2026-07-12-portable-core-filesystem-design.md` | modified |
| `packages/anima-auth-contracts/src/index.ts` | modified |
| `packages/anima-core/src/core_archive.rs` | modified |
| `packages/anima-core/src/ffi.rs` | modified |
| `packages/anima-corefs/src/publication.rs` | modified |
| `packages/anima-corefs/src/transaction.rs` | modified |
| `packages/anima-corefs/src/transaction/maintenance.rs` | added |
| `packages/anima-corefs/src/transaction/maintenance/archive.rs` | added |
| `packages/anima-corefs/src/transaction/preparation.rs` | modified |
| `packages/api-client/src/client.ts` | modified |
| `packages/api-client/src/types.ts` | modified |
| `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md` | modified |
| `tickets/portable-core-filesystem/PCF-009-soul-cleanup.md` | modified |
| `tickets/portable-core-filesystem/PCF-010-catalog-gc-key-retirement.md` | modified |
