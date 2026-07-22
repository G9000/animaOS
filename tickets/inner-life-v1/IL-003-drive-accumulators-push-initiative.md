# IL-003 - Drive accumulators and push initiative channel

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent/inner_life`, `apps/server/src/anima_server/services/agent/proactive.py`, `apps/server/src/anima_server/models/presence.py`, `apps/desktop`
- Parent: `IL-000`
- Depends on: `IL-001`, `IL-002`
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-21 07:27 MYT
- Started: 2026-07-20 13:42 MYT
- Completed: 2026-07-21 07:27 MYT

## Goal

Let the companion send an unprompted message when a named drive pressure crosses threshold — off by default, strictly gated, and fully provenance-logged.

## Deliverables

- Pressure accumulators per PRD IL3 table (`unresolved_thread`, `pattern_insight`, `relational`, `novelty`, `dream_residue`) with growth/reset rules and persisted state.
- Gate chain: presence_config opt-in (default off), quiet hours, adaptive cooldown (24 h base, closeness-scaled, back-off on unanswered), rate caps (1/day, 3/week), idle-only.
- Outbound delivery via OS notification (Tauri shell) with adapter seam for other channels.
- Drive-tagged message generation (small LLM call) that must speak from the accumulated material; generic check-in filler prohibited in prompt and validated in tests.
- Initiative provenance log: drive, pressure snapshot, gate states, generated text.
- presence_config UI fields for opt-in, quiet hours, caps.

## Acceptance

- Zero initiatives possible while disabled, in quiet hours, or above caps (tests).
- Every fired initiative traceable to a named drive with recorded pressures.
- Unanswered initiatives increase cooldown; any user turn resets `relational`.

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-20 MYT - Implemented: pure drive accumulators (`inner_life/drives.py`), five-gate chain + edge wiring (`inner_life/initiative.py`), delivery seam + pollable default + fetch/ack route (`inner_life/delivery.py`), drive-tagged generation prompt, soul migration `20260720_0001` (InitiativeLog + PresenceConfig columns) and runtime migration `030` (DriveState + PendingInitiative), vault export/import + eval-reset, per-user presence-tick integration.
- 2026-07-20 MYT - Review gate: task review (Approved, 1 Important fixed) + adversarial whole-branch review. Whole-branch review caught a Critical wiring bug — the presence tick passed the shared `SessionLocal` instead of resolving the per-user soul store, leaving IL3 inert in the SQLite deployment; fixed with a `user_id -> factory` resolver + regression test. Two-phase soul/runtime commit hardened so the provenance log never over-claims delivery.
- 2026-07-21 MYT - Codex PR review on #115: 8 further rounds of findings, each fixed with a regression test — active-DEK gate before firing (unencrypted `generated_text` / ciphertext-to-LLM risk when the session is locked), UTC-explicit `fired_at` (SQLite drops tzinfo, silently shifting rate-cap windows in non-UTC deployments), vault restore now clears pre-existing `initiative_log` rows before import, due/occurred foresight statuses kept feeding `unresolved_thread`, an explicit reset signal when the foresight source closes, unresolved-thread material scoped to the same horizon gate as its grow signal, master Presence pause honored (not just the initiative opt-in), per-user soul-resolver failures isolated, material-less dominant drives reset and re-selected instead of stalling, local (not UTC) tick date for foresight horizons, SQLite autoincrement fixed for runtime BigInteger PKs, delivered-status reconciled on both ack and poll, and surfaced pattern-item tracking corrected twice (per-item marker, then a `(created_at, id)` tie-break for same-timestamp items). Filed `IL-008` for the deferred client-side wiring (typed-client fields, desktop poll/display, real `OSNotificationDelivery`) per the PR's explicit scope-out. Merged to `main` as `5e38dbf` (PR #115).

## Validation

- Commands:
  - `uv run pytest tests/test_inner_life_initiative.py -q` -> 87 passed
  - `bun run test` -> 54 failed / 2821 passed / 2 skipped (54 = pre-existing CoreFS/keyslots/recovery/vault/encrypted-core baseline, confirmed via a clean-checkout diff; zero IL-003 regressions)
  - CI (`Server Ruff` lint) -> pass
- Changed paths:
  - `apps/server/src/anima_server/services/agent/inner_life/{drives,initiative,delivery}.py`
  - `apps/server/src/anima_server/services/agent/inner_life/presence.py`
  - `apps/server/src/anima_server/services/agent/templates/prompts/initiative_message.md.j2`
  - `apps/server/alembic_core/versions/20260720_0001_add_initiative.py`
  - `apps/server/alembic_runtime/versions/{030_drive_state_pending_initiative,031_pattern_surfaced_tiebreak}.py`
  - `apps/server/src/anima_server/{main.py,db/session.py,services/vault.py,services/eval_reset.py,services/data_crypto.py}`
  - `apps/server/src/anima_server/models/{agent_runtime,presence,runtime_consciousness,__init__}.py`
  - `apps/server/src/anima_server/{api/routes/presence.py,schemas/presence.py,services/presence_config.py}`
  - `apps/server/tests/{test_inner_life_initiative.py,test_dashboard_api.py}`
- Notes:
  - Off by default (`PresenceConfig.initiative_enabled`); no initiative can fire while disabled, in quiet hours, over caps, or with no active memories DEK (session locked).
  - `dream_residue` drive and `OSNotificationDelivery` are documented stubs (dream cycle is IL-007; Tauri notification layer is `IL-008`).
  - Client-side wiring (typed-client fields, desktop UI, real OS notifications) is intentionally out of scope here — tracked as `IL-008`.
