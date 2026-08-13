# PCF-007 - Account profile, tasks, preferences, and credentials

- Status: in_progress
- Priority: P0
- Scope: account/auth/tasks/settings/credentials across server, desktop/Tauri, local daemon, and anima-mod
- Parent: `PCF-000`
- Depends on: `PCF-004`, `PCF-006`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-7-account-profile-tasks-preferences-and-credentials`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-13 17:33 MYT
- Started: 2026-08-13 15:58 MYT
- Completed:

## Goal

Move portable account/tasks/preferences to Core objects, machine settings outside Core, and secrets into the OS credential store.

## Deliverables

- Checked row/field/browser/device state inventory.
- Keyslot-based unlock and encrypted account profile.
- Core-backed tasks/preferences/presence choices.
- Machine-local provider/integration config and fail-closed OS secret storage.
- Unified Python, Tauri/local-daemon, and anima-mod credential boundary; mod config stores references and Google OAuth uses a dedicated secret store.
- Copy-verify-delete migration for `.anima/runtime-config.json` and legacy plaintext `users/<id>/soul.md` into their approved destinations.
- CoreFS Access settings for reviewing verified package ID/publisher/digest, approving, narrowing, and revoking client/mod folder-scoped capabilities, with clear update/transfer reapproval state.

## Acceptance

- Every persisted table/field/key has an approved destination.
- No plaintext username index, secret, or reusable token remains in browser/Core bootstrap storage.
- Legacy daemon file/localStorage tokens plus anima-mod SQLite/YAML secret and OAuth values are migrated, verified, and scrubbed; missing OS credential support fails closed.
- Pre-unlock UI exposes no private profile data.
- No runtime config or plaintext Soul/persona file remains under the portable Core.
- Manifest device/runtime-engine fields are moved to the instance-local registry; mixed AgentProfile/SelfModel fields follow their app/runtime classifications.
- Clients cannot grant themselves access, claim reserved roles, retain access after lock/revocation, or lose a valid same-device grant merely because the user renamed/moved its folder; a transferred Core requires destination package verification and reapproval.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 17:34 MYT - Added user-controlled client/mod folder capability settings.
- 2026-08-13 15:58 MYT - Claimed by Codex on local branch
  `codex/pcf-007-account-settings` from approved evidence-sequencing head
  `031f5857`. PCF-004 and PCF-006 are both done, no competing claim is visible,
  and the paid PCF-008 package workflow remains triggerless. No external action
  is authorized for this ticket.
- 2026-08-13 16:26 MYT - Completed Task 7 Steps 1-2 locally. The checked
  inventory now classifies every SQLCipher/Runtime field plus browser, app-data,
  anima-mod, runtime-config, and credential state. Native and Python converters
  prepare bounded encrypted account-profile, preferences, and task objects while
  excluding the password hash and plaintext username index. Legacy SQL remains
  authoritative until the PCF-008 cutover marker. No external action was taken.
- 2026-08-13 16:56 MYT - Completed Step 3 locally. Python now owns a
  fail-closed OS credential service plus authenticated loopback, audience-bound,
  one-shot mod capabilities; the supervised dev launcher shares a rotating
  process-pair bootstrap only with server/anima-mod. Runtime provider secrets,
  schema-declared mod secrets, Google OAuth tokens, and the daemon control token
  now use verified OS storage. Legacy runtime-config, SQLite, daemon-file, and
  browser token copies use copy-verify-scrub migration, with no generic browser
  secret-read route or plaintext fallback. No external action was taken.
- 2026-08-13 17:07 MYT - Completed Step 4 locally. Versioned login now unwraps
  manifest keyslots, opens SQLCipher through the opaque owner binding, unwraps
  Soul domain keys, derives active CoreFS keys, and hydrates the authenticated
  encrypted account-profile before returning private identity. Legacy manifests
  are scrubbed of `user_index`; registration and crash recovery use the opaque
  single-owner locator. SQLCipher fallback remains only for the one pre-PCF-008
  upgrade login where no prepared account-profile exists yet. No external
  action was taken.
- 2026-08-13 17:14 MYT - Completed Step 5 locally. Legacy task CRUD preserves
  its public schema and synchronously rebuilds/verifies the encrypted task
  shadow after each committed mutation. An exact authenticated global marker
  switches reads to the marker-pinned CoreFS catalog and immediately blocks all
  further SQL task mutation; the still-frozen public CoreFS mutation facade
  remains reserved for the PCF-008 first-write activation adapter. Task priority
  validation now matches the public `1..5` API across Python and Rust. No
  external action was taken.
- 2026-08-13 17:33 MYT - Completed Step 6 locally. Unlock now hydrates one
  bounded encrypted preference document and copy-verifies legacy theme,
  language, ASCII, clock, dashboard layout, bundled-BGM, and portable
  background values before exact source removal. Concurrently changed legacy
  values remain for retry. Host background and custom-BGM media references are
  copied and verified into explicitly device-local keys; browser data-URL media
  persistence is refused, and only explicit `corefs://object/...` attachment
  references may enter portable background config. Presence commits rebuild and
  authenticate the same preference shadow. PCF-004 already owns the encrypted
  draft handoff and source-first cleanup protocol. No external action was taken.

## Validation

- Commands:
  - `uv run pytest apps/server/tests/test_corefs_account_migration.py apps/server/tests/test_corefs_preferences.py apps/server/tests/test_corefs_state_inventory.py -q` (`10 passed`)
  - affected CoreFS migration regression band (`73 passed`)
  - `cargo test -p anima-corefs validates_bounded_account_preferences_and_task_documents` (`1 passed`)
  - focused Ruff, desktop storage-classification (`2 passed`), Rust formatting, and diff hygiene passed
  - credential API/service (`6 passed`), credential plus inventory (`11 passed`),
    desktop credential/storage (`4 passed`), anima-mod config/Google (`12 passed`),
    shared native credential store (`3 passed`), local daemon test/check, desktop
    native check, anima-mod build, focused dev broker-environment test, Ruff,
    Rust formatting, and diff hygiene passed
  - stable authentication band (`13 passed`, `2 deselected`), focused opaque-owner/
    keyslot crash and encrypted-profile coverage (`6 passed`), account migration
    (`2 passed`), Ruff, and diff hygiene passed; the two deselected unrelated
    health/LLM tests retain the known native background-index teardown segfault
  - task CRUD/shadow/cutover plus account migration (`7 passed`), native bounded
    account/preferences/task validation (`1 passed`), Ruff, and diff hygiene passed
  - portable preference API/presence shadow plus account/task regressions (`12 passed`),
    desktop migration/storage classification (`6 passed`), API client (`27 passed`),
    focused Ruff, TypeScript, and production desktop build passed
- Changed paths:
  - `docs/architecture/system/portable-state-inventory.md`
  - `apps/server/src/anima_server/services/corefs/{formats.py,writing_source.py}`
  - `packages/anima-corefs/src/{transaction.rs,transaction/converter.rs}`
  - `packages/anima-core/src/ffi.rs`
  - `apps/server/tests/test_corefs_{account_migration,preferences,state_inventory}.py`
  - `apps/desktop/tests/settings-storage-classification.test.ts`
  - `packages/anima-credential-store/{Cargo.toml,src/lib.rs}`
  - `apps/server/src/anima_server/{services/credentials.py,api/routes/credentials.py,config.py,main.py}`
  - `apps/anima-mod/src/security/credential-broker.ts`
  - `apps/anima-mod/src/{core,management}` and Google credential integrations
  - `apps/desktop/src-tauri/src/lib.rs`, `apps/desktop/src/lib/daemon.ts`
  - `apps/local-runtime-daemon/src/main.rs`
  - `scripts/dev-root{,-lib}.mjs`
  - `apps/server/src/anima_server/services/corefs/account_profile.py`
  - `apps/server/src/anima_server/{db/user_store.py,services/core.py,api/routes/auth.py}`
  - `apps/server/src/anima_server/services/vault.py`
  - `apps/server/src/anima_server/services/corefs/task_authority.py`
  - `apps/server/src/anima_server/api/routes/tasks.py`
  - `apps/server/src/anima_server/{schemas/preferences.py,api/routes/preferences.py}`
  - `apps/server/src/anima_server/services/corefs/preferences.py`
  - `apps/server/src/anima_server/api/routes/presence.py`
  - `apps/desktop/src/lib/{portablePreferences,theme,background,preferences}.ts`
  - desktop ASCII/clock/BGM/background/dashboard consumers and focused tests
  - `packages/api-client/src/{client,types}.ts`
- Notes:
  - PCF-004 and PCF-006 are done. PCF-008 remains responsible for the deferred
    final signed-package evidence before cutover or release publication.
  - Steps 7-9 remain open; this checkpoint does not activate CoreFS authority.
