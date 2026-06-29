# VCE-000 - Voice Foundation v1 Parent Tracker

- Status: backlog
- Priority: P1
- Scope: `apps/server`, `apps/desktop`, `packages/api-client`, `docs/prds/voice`, `docs/superpowers/plans`, `tickets/voice-foundation-v1`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/voice/voice-foundation-v1.md
- Plan: docs/superpowers/plans/2026-06-29-voice-foundation-v1.md
- Created: 2026-06-29 14:37 MYT
- Updated: 2026-06-29 14:37 MYT
- Started:
- Completed:

## Goal

Track the initiative that adds local-first half-duplex voice-to-voice chat around Anima's existing agent runtime, with provider abstraction, transcript-first memory, optional cloud adapters, diagnostics, and privacy-safe defaults.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `VCE-001` | Provider contracts and settings | `backlog` | none |
| `VCE-002` | Local STT adapter | `backlog` | `VCE-001` |
| `VCE-003` | Local TTS adapter | `backlog` | `VCE-001` |
| `VCE-004` | Voice session API | `backlog` | `VCE-002`, `VCE-003` |
| `VCE-005` | Desktop voice chat UI | `backlog` | `VCE-004` |
| `VCE-006` | Cloud opt-in adapters | `backlog` | `VCE-001`, `VCE-004` |
| `VCE-007` | Diagnostics and privacy controls | `backlog` | `VCE-004`, `VCE-005` |
| `VCE-008` | Documentation and final validation | `backlog` | `VCE-005`, `VCE-006`, `VCE-007` |

## Deliverables

- Voice provider contracts for STT, TTS, health checks, settings, events, and diagnostics.
- Local STT support through faster-whisper and OpenAI-compatible local endpoints.
- Local TTS support through Kokoro and OpenAI-compatible local endpoints.
- Server voice session API that emits typed events and feeds transcripts into the normal Anima agent runtime with `source="voice"`.
- Desktop push-to-talk UI with transcript review/edit, response streaming, generated audio playback, and stop controls.
- Explicit cloud opt-in adapter path, with OpenAI Audio as the first cloud fallback.
- Voice settings, provider tests, latency diagnostics, and privacy controls.
- Final documentation, setup notes, and validation records.

## Core Production Path

The first production-ready slice is `VCE-001` through `VCE-007` together:

- `VCE-001` establishes provider and event boundaries.
- `VCE-002` and `VCE-003` make local STT/TTS real.
- `VCE-004` proves voice can use the normal Anima runtime without bypassing memory, tools, approvals, or persistence.
- `VCE-005` makes the feature usable in desktop chat.
- `VCE-006` keeps cloud as an explicit opt-in fallback instead of the default.
- `VCE-007` makes the feature understandable, debuggable, and privacy-safe.

`VCE-008` is required before closing the initiative, but it is the docs, packaging notes, and final validation pass.

## Acceptance

- Every child ticket references this parent.
- Parent status table reflects child progress.
- Voice provider settings default to local mode and cloud disabled.
- A user can record an utterance, receive a transcript, send it as a voice-sourced chat turn, and hear Anima speak the response.
- Voice turns use the normal memory, self-model, tool, approval, persistence, and reflection paths.
- STT failure creates no chat turn.
- TTS failure preserves and displays the assistant text response.
- Raw microphone audio is transient by default.
- Generated assistant audio is not persisted by default.
- Provider diagnostics expose availability and latency breakdowns.
- No always-on microphone, wake word, voice cloning, durable acoustic emotion inference, or native speech-to-speech runtime replacement is introduced.

## Completed Tickets

- none

## Activity Log

- 2026-06-29 14:37 MYT - Parent tracker created for Voice Foundation v1 planning.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/voice-foundation-v1/VCE-000-parent.md
- Notes:
  - tracker only
