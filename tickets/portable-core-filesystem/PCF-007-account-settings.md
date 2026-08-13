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
- Updated: 2026-08-13 16:26 MYT
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

## Validation

- Commands:
  - `uv run pytest apps/server/tests/test_corefs_account_migration.py apps/server/tests/test_corefs_preferences.py apps/server/tests/test_corefs_state_inventory.py -q` (`10 passed`)
  - affected CoreFS migration regression band (`73 passed`)
  - `cargo test -p anima-corefs validates_bounded_account_preferences_and_task_documents` (`1 passed`)
  - focused Ruff, desktop storage-classification (`2 passed`), Rust formatting, and diff hygiene passed
- Changed paths:
  - `docs/architecture/system/portable-state-inventory.md`
  - `apps/server/src/anima_server/services/corefs/{formats.py,writing_source.py}`
  - `packages/anima-corefs/src/{transaction.rs,transaction/converter.rs}`
  - `packages/anima-core/src/ffi.rs`
  - `apps/server/tests/test_corefs_{account_migration,preferences,state_inventory}.py`
  - `apps/desktop/tests/settings-storage-classification.test.ts`
- Notes:
  - PCF-004 and PCF-006 are done. PCF-008 remains responsible for the deferred
    final signed-package evidence before cutover or release publication.
  - Steps 3-9 remain open; this checkpoint does not activate CoreFS authority.
