# VCE-004 - Voice session API

- Status: backlog
- Priority: P1
- Scope: `apps/server`, `packages/api-client`
- Parent: `VCE-000`
- Depends on: `VCE-002`, `VCE-003`
- Owner: unassigned
- PRD: docs/prds/voice/voice-foundation-v1.md
- Plan: docs/superpowers/plans/2026-06-29-voice-foundation-v1.md
- Created: 2026-06-29 14:37 MYT
- Updated: 2026-06-29 14:37 MYT
- Started:
- Completed:

## Goal

Expose a voice session API that turns microphone audio into a normal Anima voice-sourced chat turn and then synthesized assistant audio.

## Deliverables

- New voice API route registered in the FastAPI app.
- Voice session orchestration service that emits the PRD event contract.
- Transcript review/send flow compatible with desktop edit/cancel UX.
- Normal agent runtime integration with `source="voice"`.
- API client types/helpers for voice session events.
- Tests for successful session flow and failure transitions.

## Acceptance

- A voice session can emit start, transcript, agent text, TTS, done, and error events.
- Sending a transcript creates a normal chat turn with `source="voice"`.
- The voice path does not bypass memory retrieval, self-model blocks, tools, approval checkpoints, persistence, or post-turn reflection.
- STT failure creates no chat message.
- TTS failure keeps the completed text response.
- Focused voice session API tests pass.

## Activity Log

- 2026-06-29 14:37 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
