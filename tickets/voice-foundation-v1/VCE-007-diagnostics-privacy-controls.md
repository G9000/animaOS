# VCE-007 - Diagnostics and privacy controls

- Status: backlog
- Priority: P1
- Scope: `apps/server`, `apps/desktop`, `packages/api-client`
- Parent: `VCE-000`
- Depends on: `VCE-004`, `VCE-005`
- Owner: unassigned
- PRD: docs/prds/voice/voice-foundation-v1.md
- Plan: docs/superpowers/plans/2026-06-29-voice-foundation-v1.md
- Created: 2026-06-29 14:37 MYT
- Updated: 2026-06-29 14:37 MYT
- Started:
- Completed:

## Goal

Make voice providers understandable, testable, and privacy-safe for users and implementers.

## Deliverables

- Voice settings fields for STT provider, STT model/endpoint, TTS provider, TTS voice/model/endpoint, cloud enablement, and raw audio retention.
- Provider test actions for recording, transcription, synthesis, and playback.
- Health checks for selected STT and TTS providers.
- Latency breakdown for upload/receive, STT, agent first token, TTS, and playback-ready.
- Privacy tests for transient raw audio and non-persistent generated audio defaults.

## Acceptance

- The user can see whether configured voice providers are available.
- Diagnostics expose useful timing without storing sensitive acoustic data by default.
- Raw microphone audio is transient by default.
- Generated assistant audio is not persisted by default.
- Acoustic features are not promoted into memory.
- Settings and diagnostics tests pass.

## Activity Log

- 2026-06-29 14:37 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
