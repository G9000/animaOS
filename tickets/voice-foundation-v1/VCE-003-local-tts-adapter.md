# VCE-003 - Local TTS adapter

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

Implement the local text-to-speech lane for Voice Foundation v1.

## Deliverables

- Kokoro TTS adapter behind the `VCE-001` contract.
- OpenAI-compatible local TTS endpoint adapter for Speaches-style servers.
- Audio output result shape for generated chunks or a generated audio file.
- Provider health checks, supported voice/model metadata, and synthesis timing metadata.
- Tests for successful synthesis, unsupported voice, unavailable provider, empty text, and timing metadata.

## Acceptance

- Assistant text can be synthesized locally without cloud credentials.
- The TTS provider returns playable audio bytes or a server-readable audio stream reference.
- TTS failure does not erase or block the assistant text response.
- Generated audio is transient by default and is not promoted into memory.
- Focused TTS adapter tests pass with provider boundaries mocked where heavy models are unavailable.

## Activity Log

- 2026-06-29 14:37 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
