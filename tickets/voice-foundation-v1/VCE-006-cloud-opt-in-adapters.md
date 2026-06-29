# VCE-006 - Cloud opt-in adapters

- Status: backlog
- Priority: P2
- Scope: `apps/server`, `apps/desktop`
- Parent: `VCE-000`
- Depends on: `VCE-001`, `VCE-004`
- Owner: unassigned
- PRD: docs/prds/voice/voice-foundation-v1.md
- Plan: docs/superpowers/plans/2026-06-29-voice-foundation-v1.md
- Created: 2026-06-29 14:37 MYT
- Updated: 2026-06-29 14:37 MYT
- Started:
- Completed:

## Goal

Add explicit opt-in cloud STT/TTS support without weakening the local-first default.

## Deliverables

- Cloud enablement gate in voice settings.
- OpenAI Audio STT adapter behind the same STT contract.
- OpenAI Audio TTS adapter behind the same TTS contract.
- Clear provider metadata so the UI can show when audio or generated text may leave the device.
- Tests that cloud provider selection is rejected unless cloud voice is enabled.
- Follow-up notes for Deepgram, Cartesia, and ElevenLabs provider slots.

## Acceptance

- Local voice works without cloud credentials.
- Selecting a cloud STT/TTS provider requires explicit cloud voice enablement.
- Missing cloud credentials produce a clear provider health/error state.
- Cloud adapters preserve the cascaded pipeline: STT -> Anima runtime -> TTS.
- No external realtime agent bypasses Anima's memory, tools, approvals, or transcript contract.

## Activity Log

- 2026-06-29 14:37 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
