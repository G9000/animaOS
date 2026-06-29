---
title: Voice Core Module
description: Optional speech and listening surfaces for ANIMA
category: architecture
updated: 2026-06-29
---

# Voice Core Module

[Back to Capability Modules](README.md)

`voice.core` gives ANIMA a spoken interface.

It answers:

```text
How does ANIMA listen, speak, and remember voice interactions?
```

Voice is not just text chat with a microphone. It changes pacing, interruption behavior, retention risk, emotional intimacy, and user expectations.

## Capability Id

```text
voice.core
```

Suggested submodules:

- `voice.stt.local`
- `voice.stt.cloud`
- `voice.tts.local`
- `voice.tts.cloud`

The core module should coordinate policy. Provider-specific modules can implement speech-to-text and text-to-speech.

## What Voice Owns

Voice Core owns:

- speech input mode
- speech output mode
- push-to-talk versus session listening policy
- STT provider configuration
- TTS provider configuration
- voice session state
- transcript retention policy
- raw audio retention policy
- interruption and barge-in behavior
- audio bridge requirements
- voice-specific audit events

Voice Core should not own:

- durable memory promotion
- raw microphone access as a model-visible tool
- emotion diagnosis from voice alone
- always-on listening by default

## Interface Versus Tool

Voice may not need many agent-visible tools.

Most voice behavior wraps the chat turn:

```text
microphone -> STT -> normal user message -> Brain System -> normal response -> TTS -> speaker
```

The model may not need to know that the user spoke rather than typed, except when voice-specific context matters.

Possible agent-visible tools should be narrow:

- `speak_short_confirmation`
- `set_voice_session_mode`
- `summarize_voice_session`

Raw tools like `listen_microphone` or `play_audio_buffer` should remain hidden bridge primitives.

## Listening Modes

| Mode | Meaning |
| --- | --- |
| `push_to_talk` | User holds or taps control for each utterance |
| `manual_start_session` | User starts a voice session explicitly |
| `wake_word` | Future local-only wake detection |
| `always_listening` | Out of scope until a much stronger policy exists |

Production default should be `push_to_talk` or manual session start.

## Data Boundaries

Raw audio is sensitive.

Default retention:

- raw audio buffer: transient
- STT transcript: same as chat message if user sends it
- TTS output: transient unless cached voice output is explicitly enabled
- prosodic metadata: runtime only unless user opts in
- durable memory: candidate only through Memory Core

Do not store raw audio by default.

## Emotional Signals

Voice can carry emotional signal, but ANIMA's emotional doctrine still applies: notice and adjust, never diagnose and announce.

Allowed:

```text
User's speech was quieter and slower than usual during this session; confidence low.
```

Avoid:

```text
User is depressed.
```

Voice-derived emotional signals should be low confidence unless backed by conversation content and repeated pattern.

## Provider Policy

Voice may use local or cloud providers.

Config should make this explicit:

- STT provider
- TTS provider
- whether audio leaves the device
- language and locale
- voice identity
- latency/quality tradeoff
- retention mode

If cloud STT/TTS is used, the UI should make that obvious.

## Failure Cases

Expected failures:

- microphone permission denied
- audio bridge unavailable
- STT provider unavailable
- TTS provider unavailable
- unsupported locale
- input too long
- no speech detected
- user interrupted output

Voice should fail gracefully back to typed chat whenever possible.

## Future Extensions

Future voice work can include:

- local wake word
- low-latency streaming STT
- duplex voice sessions
- user-selectable voice identity
- emotional tone adaptation
- transcript review before memory promotion
- voice memories with explicit user consent
