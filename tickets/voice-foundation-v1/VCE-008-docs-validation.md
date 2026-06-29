# VCE-008 - Documentation and final validation

- Status: backlog
- Priority: P2
- Scope: `docs`, `apps/server`, `apps/desktop`, `packages/api-client`, `tickets/voice-foundation-v1`
- Parent: `VCE-000`
- Depends on: `VCE-005`, `VCE-006`, `VCE-007`
- Owner: unassigned
- PRD: docs/prds/voice/voice-foundation-v1.md
- Plan: docs/superpowers/plans/2026-06-29-voice-foundation-v1.md
- Created: 2026-06-29 14:37 MYT
- Updated: 2026-06-29 14:37 MYT
- Started:
- Completed:

## Goal

Close Voice Foundation v1 with setup docs, validation records, and remaining-risk notes.

## Deliverables

- Local provider setup documentation for faster-whisper, Kokoro, and Speaches-style endpoints.
- Cloud opt-in setup documentation for the implemented cloud adapter.
- Privacy behavior documentation for transcripts, raw audio, generated audio, and memory extraction.
- Manual smoke-test checklist for local voice-to-voice.
- Final validation results recorded in this ticket and the parent tracker.
- Follow-up notes for streaming STT, sentence-level TTS, barge-in, native speech-to-speech models, and licensing review.

## Acceptance

- Voice setup and troubleshooting docs are sufficient for a developer to run the local path.
- Final validation includes backend tests, desktop build/type checks, health check, and manual smoke test status.
- Parent tracker child status table is updated.
- Any remaining limitations are recorded as follow-up risks instead of hidden.
- No model weights, provider credentials, or local secrets are committed.

## Activity Log

- 2026-06-29 14:37 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
