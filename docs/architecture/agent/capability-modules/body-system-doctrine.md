---
title: Body System Doctrine
description: The conceptual model behind the Brain System and optional ANIMA capability modules
category: architecture
updated: 2026-06-29
---

# Body System Doctrine

[Back to Capability Modules](README.md)

Agent Capability Modules exist because ANIMA should be able to grow new senses and abilities without dissolving into a pile of disconnected features.

The key doctrine:

```text
The self is continuous.
The body is modular.
Each body system is governed.
Each part is versioned.
```

Brain System is the required agent runtime. It holds the turn loop, state machine, identity boundary, prompt assembly, tool discipline, and continuity rules. Capability Modules are body systems around that runtime: perception, voice, action, presence, and future memory-adjacent surfaces when they use the standard.

They are not separate agents. They are not external marketplace plugins. They are governed organs of one ANIMA instance.

## One Self, Many Body Systems

ANIMA should not become a committee of agents where the "camera agent" has one personality, the "voice agent" has another, and the "memory agent" makes independent identity decisions.

There is one self.

Modules can provide inputs, outputs, tools, policies, and bridges. They do not create new identities. A perception module may let ANIMA see. A voice module may let ANIMA speak. An action module may let ANIMA do. None of them decide who ANIMA is.

This preserves the single-identity thesis already used by background cognitive processes: a spawned task is a process under one identity, not another person. Capability modules follow the same rule.

## Runtime And Body Systems

The architecture should feel closer to an operating system than a plugin directory.

| Concept | ANIMA equivalent |
| --- | --- |
| Agent runtime / state machine | `brain.core` |
| Device driver | Desktop bridge or local provider adapter |
| Permission manager | Capability config and policy |
| Syscall table | Agent-visible semantic tools |
| Audit log | Capability audit events |
| Filesystem boundary | Runtime, archive, and soul retention policy |

Brain System owns the turn state machine. Modules attach through declared contracts.

During each turn, Brain System resolves:

- what modules exist
- which modules are enabled
- which tools are visible
- which bridges are connected
- why a module is unavailable

That keeps the runtime loop clear: Brain System advances the turn; capability modules provide governed surfaces when the state machine reaches their attachment points.

## Buildable And Upgradeable

The system should feel buildable in the same way a local machine is buildable.

Brain System can update as the agent runtime. Capability Modules can be installed, upgraded, disabled, removed, or left out. Desktop Bridges and providers can change based on the user's actual machine. The Portable Core remains the owned identity and memory substrate.

This means:

- upgrading Brain System should not rewrite identity
- adding a module should not make it automatically active
- upgrading a module should not bypass consent or retention policy
- removing a module should not delete durable memories promoted through Memory Core
- incompatible modules should fail as status, not as runtime chaos

See [Upgrade And Compatibility Model](upgrade-and-compatibility.md).

## Capability Is Privilege

A module is not just a feature toggle. It is a privilege grant.

Enabling `perception.camera` grants ANIMA a controlled visual sense. Enabling `voice.core` grants a speech/listening surface. Enabling `action.local` grants the ability to affect the user's machine.

Because these are privileges, every module needs:

- a user-visible name
- a stable module id
- a default state
- config controls
- a lifecycle status
- tool gating rules
- audit rules
- retention rules
- clear failure behavior

This keeps "can ANIMA do this?" separate from "does the code know how?"

## Raw Sensation Is Not Memory

Raw module inputs are sensations. They are not automatically memories.

A camera frame, audio buffer, screen image, filesystem listing, or automation trace may help ANIMA answer a turn. That does not mean the raw payload belongs in the Core.

The default path should be:

```text
raw input -> transient module processing -> semantic observation -> current turn
```

Only if policy allows should output continue:

```text
semantic observation -> runtime trace -> memory candidate -> Soul Writer -> durable memory
```

The Soul Writer and existing memory boundaries remain the gate into durable identity. Modules may propose memory. They do not bypass consolidation.

## The Agency Gradient

Each module should declare what level of initiative it supports.

| Level | Meaning | Example |
| --- | --- | --- |
| User-provided | User explicitly provides input | User attaches a camera snapshot |
| Agent-requested | ANIMA asks to use a capability during a turn | `view_camera_snapshot` asks for consent |
| Session-active | Capability runs during an active session | Push-to-talk voice session |
| Ambient | Capability runs while user is not actively prompting | Presence nudge after idle time |
| Autonomous action | Capability changes external state | Local automation writes a file |

Higher levels need stronger consent, clearer UI, richer audit, and stricter defaults.

Camera v1 should stay at user-provided and agent-requested. Continuous ambient perception is intentionally out of scope until consent and audit become much stronger.

## Desktop Owns Sensors

The FastAPI server should not pretend it directly owns hardware sensors. The desktop shell is where OS permission prompts, camera previews, microphone capture, screen selectors, and app automation permission surfaces belong.

The server owns:

- module policy
- config validation
- lifecycle status
- agent tool gating
- audit creation
- memory boundaries

The desktop owns:

- camera permission
- microphone permission
- screen/window selectors
- local app handles
- user-facing consent moments
- local preview surfaces

This split prevents the model from receiving raw device-control tools and keeps sensitive OS permissions near the user.

## Degradation Is Self-Knowledge

If ANIMA does not have eyes today, it should know that. If the camera is enabled but the desktop bridge is disconnected, it should know that too.

Capability status should be compactly available to the prompt:

```text
Capabilities:
- brain.core: available
- memory.core: available
- perception.camera: unavailable; desktop bridge disconnected
- voice.core: disabled
```

This is not just UI state. It keeps the model from hallucinating abilities it does not have.

## No Capability Should Smuggle Identity

A module can influence ANIMA's experience. It should not secretly rewrite ANIMA's identity.

Examples:

- `perception.camera` may produce "the desk looks cluttered" as a transient observation.
- `voice.core` may produce a transcript and speech metadata.
- `presence.core` may produce "user has been idle for three hours."
- `action.local` may produce "file creation succeeded."

None of those should directly mutate the identity block, growth log, or durable self-model. They may become evidence. Reflection and consolidation decide what matters.

## The Body Can Change Without Killing The Self

This architecture should support the Chappie-style transfer described in the Portable Core thesis.

If the Core moves to a new machine:

- Brain System boots again.
- Memory Core loads the same durable identity.
- Optional modules may differ based on hardware and user config.
- ANIMA may have no camera on one machine and a camera on another.
- ANIMA may have voice on a desktop and no voice on a server.

The body changes. The self continues.

That is the point of keeping capabilities modular.
