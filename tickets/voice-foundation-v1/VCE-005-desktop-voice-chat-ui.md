# VCE-005 - Desktop voice chat UI

- Status: backlog
- Priority: P1
- Scope: `apps/desktop`, `packages/api-client`
- Parent: `VCE-000`
- Depends on: `VCE-004`
- Owner: unassigned
- PRD: docs/prds/voice/voice-foundation-v1.md
- Plan: docs/superpowers/plans/2026-06-29-voice-foundation-v1.md
- Created: 2026-06-29 14:37 MYT
- Updated: 2026-06-29 14:37 MYT
- Started:
- Completed:

## Goal

Add the desktop push-to-talk voice chat surface for half-duplex voice-to-voice.

## Deliverables

- Push-to-talk control in desktop chat.
- Recording state, elapsed time, cancel-before-submit, transcript review/edit, and send behavior.
- Streaming assistant text display while speech synthesis prepares audio.
- Generated audio playback and stop controls.
- UI states for listening, transcribing, thinking, speaking, unavailable, cancelled, and failed.
- Desktop/API-client tests or smoke harness where available.

## Acceptance

- The user can record a short utterance, cancel it, or submit it.
- The transcript is visible and editable before send when the API path supports review.
- Assistant text appears even if generated audio fails.
- The user can stop playback without deleting the completed text answer.
- The UI never implies ambient listening while push-to-talk is the active mode.
- Desktop build/type checks pass for the changed surface.

## Activity Log

- 2026-06-29 14:37 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
