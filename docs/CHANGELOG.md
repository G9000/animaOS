# Docs Changelog

## 2026-05-17

- reconciled memory and agent runtime docs with the dual-store runtime architecture, `MemoryItemEvidence` provenance, tool-driven `search_long_memory` recall, metadata-aware latest/count/temporal retrieval, extraction failure preservation, and legacy/eval provenance backfill

## 2026-03-16

- added `docs/architecture/core-ownership-model.md` documenting the single-owner provisioning model: manifest-based `owner_user_id` as the provisioning gate, UUID v7 `core_id` as the Core's permanent identity, user slot allocation starting at 0, and the distinction between new machine (new `data_dir`) vs. new user (new slot)
- removed `user_id` config field and `ANIMA_USER_ID` env var — new machine simulation now uses `ANIMA_DATA_DIR` instead
- replaced filesystem heuristic in `is_provisioned()` with manifest read of `owner_user_id`; `register_account()` now stamps `owner_user_id` in the manifest after creating the first user
- switched `core_id` generation from UUID v4 to UUID v7 (time-ordered, Rust-backed via `uuid_utils`)
- restored full multi-user infrastructure (`list_user_ids` scans all slots, `allocate_user_id` increments) while keeping the single-owner provisioning guard intact

## 2026-03-14

- clarified the runtime identity layering in docs: `persona` is now documented as a thin seed, `soul` as the user-specific charter, and `self_identity` as the evolving adaptive layer
- added `docs/agent-runtime-improvements.md`, a detailed assessment of the live Python agent runtime with prioritized improvements around turn atomicity, prompt budgeting, streaming/tool hardening, and self-model governance
- synced database docs to the current SQLite-default server behavior and clarified that Postgres is now an override, not the default path
- rewrote memory and roadmap docs to reflect the current hybrid Core state: structured memory in SQLite, encrypted-on-write `soul.md`, optional SQLCipher, and the live reflection/search pipeline
- fixed stale repo-path references in the agent migration, implementation-plan, build, and legacy graph docs
- synced runtime and packaging docs with the current monorepo shape: `apps/server` is the active local backend, the packaged desktop app still bundles the legacy `apps/api` sidecar, and Telegram/Discord webhook docs now call out that legacy boundary explicitly
- corrected stale architecture wording in the whitepaper, implementation plan, and migration docs so they match the current SQLite Core, database-backed soul migration, and live prompt/runtime feature set
- added `docs/python-backend-fix-plan.md`, a backend-only hardening plan covering provider/config correctness, orchestrator reliability, memory quality, and encrypted-Core follow-up work

## 2026-03-15

- added `docs/structured-memory-claims-design.md`, a long-term backend design for moving from English-biased string memories to structured multilingual memory claims with explicit schema, resolution rules, and migration phases
