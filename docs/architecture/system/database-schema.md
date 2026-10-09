---
title: Database Schema
description: Current Soul, CoreFS, and Runtime authority and supported schema upgrades
category: architecture
last_edited: 2026-10-09
---

# Database Schema

[Back to Index](../README.md)

## Storage Boundaries

| Compartment | Authority | Location |
|---|---|---|
| Soul | Agent identity, memories, learned user model, relationships, growth and enduring cognitive state | Per-Core SQLCipher `soul/soul.db` |
| CoreFS | Private account profile, diary, notes, tasks, threads/messages, media, sources and portable preferences | Authenticated encrypted catalogs and objects under the Core's `fs/` |
| Runtime | Runs, steps, candidates, operational state, document projections and retrieval caches | Machine-local PostgreSQL outside the Core |
| Derived Soul vectors | Rebuildable embeddings and experience centroids | Unlock-scoped process memory |

App records and disposable embeddings are absent from the current Soul schema. Runtime rows and numeric IDs do not establish canonical content authority. Private account profile fields are read through CoreFS, not from a Soul account table.

## Soul Ownership and Sessions

`apps/server/src/anima_server/models/soul_owner.py` defines `SoulOwner`: the preserved numeric local owner ID, opaque Core owner UUID, and timestamps. `SoulKeyslot` stores purpose-separated wrapped data keys. Manifest password/recovery records wrap the SQLCipher root and versioned Filesystem Root Keys using authenticated native credentials.

Request access resolves the unlock token, then opens the exact owner's canonical Soul through `get_user_session_factory(user_id)`. Background cognition uses an owner-scoped factory. `SessionLocal()` resolves the current owner lazily; it no longer creates a shared account database. Untracked existing schemas are rejected rather than stamped as current.

`db/session.py` caches engines and factories under a lock and applies SQLCipher keys, WAL, busy timeout and foreign-key enforcement per connection. Engines are disposed on final lock, shutdown, relocation, and supported restore. Explicit development-only unencrypted tests do not establish encrypted release behavior.

A valid Soul-only restore remains unlockable with `filesystem_missing`. Account responses expose the opaque owner plus `profileAvailable=false` and `filesystemAvailable=false`; desktop profile settings show the recovery state without inventing private profile values.

## Retained Soul Tables

| Family | Tables | Source |
|---|---|---|
| Owner and keys | `soul_owners`, `soul_keyslots`, `soul_schema_archives` | Owner/keyslot models |
| Durable memory | `memory_items`, `memory_item_tags`, `memory_item_evidence`, `memory_claims`, `memory_claim_evidence`, `memory_episodes` | Agent runtime models |
| Learned user model | `user_profile_fields`, `user_profile_field_evidence` | Agent runtime models |
| Enduring cognition | `foresight_signals`, `agent_experiences`, `agent_skills`, `latent_traces`, `tendency_contributions`, `reconsolidation_log`, `initiative_log`, `dream_journal` | Agent runtime models |
| Identity and emotional development | `agent_profile`, `self_model_blocks`, `emotional_signals`, `identity_blocks`, `growth_log`, `core_emotional_patterns` | Consciousness models |
| Promotion and forgetting | `pending_memory_ops`, `forget_audit_log` | Pending-operation and agent models |

Durable self-references, ownership constraints, memory supersession and evidence are retained. Schema retirement rebuilds owner FKs against `soul_owners` and validates `PRAGMA foreign_key_check` after every upgrade. `emotional_signals` are retained Soul records, distinct from disposable emotion caches.

## Provenance and Derived State

Source-bearing records use stable `source_thread_uri`, `source_message_uri`, `source_message_uris`, or `source_run_uri` fields as appropriate. Supported schema conversion resolves these through an authenticated owner-bound CoreFS inventory. Missing historical sources retain original numeric IDs in `provenance_metadata` with `provenanceState=source_unavailable`; conversion never guesses a Core URI from a Runtime ID.

`services/agent/derived_soul_state.py` rebuilds vectors from retained text and the configured embedding fingerprint. Vectors and checksums are not SQLAlchemy columns. Core switch, final owner lock, expiry and embedding configuration changes invalidate this state. `memory_vectors` and `experience_cluster_state` are removed. Runtime PostgreSQL embedding projections remain disposable.

Memory supersession preserves old durable facts; forgetting records counts and invalidates affected derived state. Canonical app content is not recreated from an obsolete Soul ORM mapping.

## Supported Schema Upgrade

Soul revisions live in `apps/server/alembic_core/versions/`; Runtime revisions live in `apps/server/alembic_runtime/versions/`. Native catalog/object formats are versioned in Rust and are not Alembic-managed.

Revision `20261009_0001` follows the supported encrypted Soul head `20260812_0001`. Before retiring existing app rows, authentication verifies the current-format manifest owner and the encrypted Soul source proof. `services/corefs/soul_store.py` creates an independently reopened SQLCipher original in a sibling `<Core-name>.schema-recovery` directory, compares table hashes/counts, and supplies a scoped proof receipt to the migration. The active Soul keeps the opaque archive ID and inventory evidence.

The revision checks authenticated CoreFS coverage when available, rebuilds retained FKs, converts provenance deterministically, drops app tables and disposable columns, and checks retained records before committing. SQLite batch rebuilds temporarily disable FK enforcement outside the transaction to avoid cascade deletion; enforcement is restored and violations reject the upgrade. Supported Soul-only recovery retains unavailable authored bytes in the encrypted original and records missing provenance honestly.

Fresh current-format registration initializes the canonical locations directly. Pending wrapped roots are durable before encrypted writes so an interrupted registration resumes with the same keys. Pre-release archive readers, shared-database conversion and untracked schema stamping are unsupported.

See [CoreFS architecture](anima-core-filesystem.md), [approved design](../../superpowers/specs/2026-07-12-portable-core-filesystem-design.md), and [retirement/maintenance plan](../../superpowers/plans/2026-10-09-corefs-retirement-maintenance.md) for recovery and maintenance gates. Historical Alembic files remain ordered schema history; they are not pre-release product importers.
