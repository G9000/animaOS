# VCE-001 - Provider contracts and settings

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `VCE-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/voice/voice-foundation-v1.md
- Plan: docs/superpowers/plans/2026-06-29-voice-foundation-v1.md
- Created: 2026-06-29 14:37 MYT
- Updated: 2026-06-29 14:37 MYT
- Started:
- Completed:

## Goal

Create the server-side voice provider boundary, settings model, and event contract that all local and cloud STT/TTS adapters use.

## Deliverables

- New voice service package under `apps/server/src/anima_server/services/voice/`.
- STT provider, TTS provider, provider health, latency, and provider error contracts.
- Voice settings schema with local defaults and cloud disabled by default.
- Typed voice session event payloads matching the PRD event contract.
- Focused tests for provider selection, settings validation, and fail-closed defaults.

## Acceptance

- STT and TTS adapters can be registered behind stable contracts without touching the chat runtime.
- Default settings select local providers or disabled providers, never cloud.
- Cloud provider selection fails closed unless cloud voice is explicitly enabled.
- Event payload types cover session start, transcript final, agent chunks, TTS lifecycle, completion, and error states.
- Focused provider contract tests pass.

## Activity Log

- 2026-06-29 14:37 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
