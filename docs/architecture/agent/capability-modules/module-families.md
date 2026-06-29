---
title: Capability Module Families
description: Initial families for ANIMA's optional body and cognition modules
category: architecture
updated: 2026-06-29
---

# Capability Module Families

[Back to Capability Modules](README.md)

Capability Modules are grouped by family. Families give the architecture a language for "what kind of body part is this?"

## Host Runtime Reference

### `brain.core`

Required host runtime. Owns the turn loop, prompt assembly, model calls, tool orchestration, final response policy, and capability registry.

Brain System is documented here only because module manifests need compatibility with it. It is the host runtime, not a capability module family.

See [Brain System](../brain-system.md).

## Memory Boundary Reference

### `memory.core`

Required for ANIMA's thesis. Owns durable memory, self-model blocks, recall, consolidation, and the write boundary between runtime state and soul state.

Memory is documented here only because modules can propose memory candidates and need a clear promotion boundary. It can report status, configuration, audit, and retention behavior, but it should not be treated as casually disableable for normal ANIMA.

See [Memory Core Boundary](../../memory/memory-core-boundary.md).

Future memory-adjacent capability modules:

- `memory.visual_assets`
- `memory.skills`
- `memory.group_context`

## Perception

Perception modules give ANIMA senses.

Initial modules:

- `perception.camera`
- `perception.screen`
- `perception.window`
- `perception.media`

Perception modules usually need desktop bridges and strict retention policy.

They answer questions like:

- What can ANIMA see right now?
- Did the user explicitly allow this sensor?
- Is raw sensor data retained?
- Can perception outputs become memory candidates?

See [Perception Modules](perception.md) and [Camera Perception](perception-camera.md).

## Voice

Voice modules give ANIMA speech and hearing.

Initial module:

- `voice.core`

Possible submodules:

- `voice.stt.local`
- `voice.tts.local`
- `voice.stt.cloud`
- `voice.tts.cloud`

Voice has two separate concerns:

1. **Interface:** talking/listening during a session.
2. **Memory:** whether transcripts, audio, or emotional/prosodic signals are retained.

Those should not be conflated.

See [Voice Core Module](voice-core.md).

## Action

Action modules let ANIMA do things outside normal chat.

Initial modules:

- `action.local`
- `action.filesystem`
- `action.process`

Action modules need approval policy. Running commands, editing files, controlling apps, or making network calls should have explicit risk levels.

The existing client-action path and Animus-style tools are likely part of this family.

See [Local Action Module](action-local.md).

## Presence

Presence modules let ANIMA maintain ambient awareness and proactive behavior.

Initial module:

- `presence.core`

Possible capabilities:

- daily greeting context
- nudges
- focus windows
- inactivity-aware reflection
- availability state

Presence should be governable because proactive behavior can feel caring or intrusive depending on user intent.

See [Presence Core Module](presence-core.md).

## External Integrations

External integrations remain in `apps/anima-mod` unless they become internal body systems.

Examples:

- Telegram
- Google
- webhooks
- future Discord

These are not Capability Modules by default. They are channels and service adapters.

If an external service becomes part of ANIMA's internal cognition, it can expose data into a capability module. But the module boundary should stay in FastAPI.

See [External Integration Boundary](../../system/external-integration-boundary.md).

## Naming Rules

Use dotted ids:

- `family.name`
- `family.name.variant` if needed

Good:

- `perception.camera`
- `voice.core`
- `action.local`
- `memory.visual_assets`

Avoid:

- `camera`
- `vision`
- `perception-camera` as the canonical id
- metaphor-heavy names as canonical ids

The repo can still use hyphenated filenames where appropriate, but module ids should be dotted.
