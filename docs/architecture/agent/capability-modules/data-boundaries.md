---
title: Capability Data Boundaries
description: Retention, audit, and memory rules for data produced by capability modules
category: architecture
updated: 2026-06-29
---

# Capability Data Boundaries

[Back to Capability Modules](README.md)

Capability Modules deal with sensitive data. Some data is as intimate as the user's room, voice, screen, local files, or idle patterns. The architecture must treat those inputs as privileges, not generic context.

## Data Classes

Module data should be classified before it is stored.

| Class | Meaning | Example | Default |
| --- | --- | --- | --- |
| Raw payload | Direct sensor or action data | image bytes, audio buffer, screenshot | transient |
| Derived observation | Text description produced from raw input | "A red notebook is on the desk" | current turn/runtime |
| User-provided artifact | File or image intentionally attached by user | chat camera image | chat retention policy |
| Operational trace | Structured tool result or status | capture succeeded, bridge timed out | runtime |
| Audit event | Minimal accountability record | camera requested, consent denied | runtime/audit |
| Memory candidate | Proposed durable learning | "User's desk setup includes..." | pending soul write |
| Durable memory | Accepted identity or user knowledge | MemoryItem, claim, self-model evidence | soul |

Raw payloads should almost never skip directly to durable memory.

## Retention Ladder

Use this ladder when designing a module:

```text
transient
  -> runtime trace
  -> encrypted archive
  -> memory candidate
  -> durable soul memory
```

Each step up the ladder requires a reason.

| Retention | Rule |
| --- | --- |
| `transient` | Available only during current call or turn. Default for raw sensor data. |
| `runtime` | Stored as operational state, trace, or cache. Rebuildable or low-sensitivity. |
| `archive` | Stored as part of encrypted conversation record when user intentionally sends it. |
| `soul_candidate` | Proposed for long-term memory and subject to promotion policy. |
| `soul` | Durable identity/memory after approved write path. |

## Default By Module Family

| Family | Raw data default | Derived data default | Durable memory path |
| --- | --- | --- | --- |
| Perception | transient | current turn/runtime | candidate only |
| Voice | transient audio, transcript per policy | transcript/runtime | candidate only |
| Action | no raw capture unless needed | runtime trace/audit | candidate only for meaningful outcomes |
| Presence | runtime signal | runtime summary | candidate only after repeated pattern |
| Memory | existing soul/runtime rules | existing soul/runtime rules | owns promotion path |

## Raw Payload Handling

Raw payload rules:

- keep in memory when possible
- use temp files only when provider APIs require them
- delete temp files in `finally`
- do not include raw base64 in agent-visible tool results
- do not write raw payloads to logs
- size-limit payloads before provider calls
- MIME-type validate payloads before analysis
- make retention explicit in tool output

For camera perception, the main agent receives a text observation, not the image bytes.

## Derived Observations

Derived observations are safer than raw payloads but still sensitive.

Example:

```text
The snapshot shows a desk with a laptop, an open notebook, and a mug.
```

This may reveal location, habits, documents, or other people. Treat derived observations as runtime data unless the user or memory policy explicitly promotes them.

## Memory Promotion

Modules may propose memory candidates, but Memory Core decides promotion.

Good candidate:

```text
User repeatedly uses a dual-monitor desk setup while working on ANIMA OS.
```

Weak candidate:

```text
There was a mug on the desk today.
```

Bad direct write:

```text
Camera module inserts a durable MemoryItem without Soul Writer review.
```

Durable memory should represent meaningful, stable understanding, not accidental sensor residue.

## Bystander And Protected Data

Perception and voice modules can capture bystanders. They can also invite unsafe inference.

Default rules:

- do not identify people from camera frames
- do not perform face recognition
- do not infer protected traits
- do not guess emotion from faces or voice alone
- do not store bystander details unless user explicitly provides and approves them
- prefer uncertainty over confident guesses

The goal is situational help, not biometric analysis.

## Audit Without Hoarding

Audit must make module behavior inspectable without turning audit into surveillance.

An audit event can record:

- module id
- operation id
- request source
- timestamp
- policy decision
- consent result
- bridge/provider status
- retention mode
- success/failure

An audit event should not record by default:

- image bytes
- audio bytes
- screenshot contents
- full filesystem listings
- secrets
- raw provider prompts containing private payloads

## User Controls

Every sensitive module should expose controls for:

- enabled/disabled
- manual-only versus agent-requested use
- consent mode
- retention mode
- audit visibility
- provider choice where applicable
- deletion or pruning of module runtime traces where safe

These controls are part of the product contract, not decoration.

## The Memory Boundary Principle

The durable Core is the soul. Modules are body systems.

Body systems can experience the world. They can send signals to the mind. They can produce evidence for reflection. But they do not get to write the soul directly.

That boundary is what lets ANIMA gain senses without becoming porous.
