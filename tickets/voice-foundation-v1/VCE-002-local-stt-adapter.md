# VCE-002 - Local STT adapter

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `VCE-000`
- Depends on: `VCE-001`
- Owner: unassigned
- PRD: docs/prds/voice/voice-foundation-v1.md
- Plan: docs/superpowers/plans/2026-06-29-voice-foundation-v1.md
- Created: 2026-06-29 14:37 MYT
- Updated: 2026-06-29 14:37 MYT
- Started:
- Completed:

## Goal

Implement the local speech-to-text lane for Voice Foundation v1.

## Deliverables

- faster-whisper STT adapter behind the `VCE-001` contract.
- OpenAI-compatible local STT endpoint adapter for Speaches-style servers.
- Audio input validation and transient temp-file handling.
- Provider health checks and timing metadata for transcription.
- Tests for successful transcription, unavailable provider, invalid audio, empty transcript, and timing metadata.

## Acceptance

- A valid recorded utterance can be transcribed locally without cloud credentials.
- The STT provider returns transcript text, optional confidence/language metadata, and timing data.
- Provider unavailable or invalid audio failures produce voice errors without creating chat turns.
- Temporary audio artifacts are removed after transcription unless raw audio retention is explicitly enabled later.
- Focused STT adapter tests pass with provider boundaries mocked where heavy models are unavailable.

## Activity Log

- 2026-06-29 14:37 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
