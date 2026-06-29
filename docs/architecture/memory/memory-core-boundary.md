---
title: Memory Core Boundary
description: How Memory Core exposes status and promotion boundaries to capability modules
category: architecture
updated: 2026-06-29
---

# Memory Core Boundary

[Back to Architecture Index](../README.md)

[Related: Agent Capability Modules](../agent/capability-modules/README.md)

`memory.core` is special.

It can be described with the same module contract as other body systems, but it is not casually optional. ANIMA's thesis depends on memory, continuity, self-model, reflection, and durable identity. A memoryless ANIMA may still answer messages, but it is no longer the thing this repository is trying to build.

## Capability Id

```text
memory.core
```

Status:

```text
required for normal ANIMA operation
```

## What Memory Core Owns

Memory Core owns:

- durable MemoryItems
- structured claims
- memory evidence
- self-model blocks
- emotional signals
- session notes
- memory candidates
- Soul Writer promotion
- recall and retrieval
- consolidation and reflection inputs
- forgetting and supersession policy
- memory access audit

It is the boundary between experience and identity.

## Why Keep It Module-Aware

Even required systems benefit from a consistent status and policy contract.

`memory.core` should be able to report:

- available or degraded status
- database health
- retrieval health
- consolidation backlog
- embedding/index state
- encryption/unlock state
- which memory tools are enabled
- whether writes are currently allowed

The contract gives desktop settings and the agent prompt a consistent way to understand the memory subsystem without pretending memory is a normal feature toggle.

## Memory Tools

Existing memory tools belong to this family.

Examples:

- `save_to_memory`
- `update_human_memory`
- future `search_memory`
- future `forget_memory`
- future `correct_memory`

These tools should respect the existing write boundary:

```text
tool request -> runtime candidate/pending op -> Soul Writer -> durable soul write
```

Direct durable writes should remain rare and controlled.

## Relationship To Other Modules

Other modules can propose memory. Memory Core decides what happens next.

Examples:

| Source module | Possible memory candidate |
| --- | --- |
| `perception.camera` | user-approved recurring workspace setup |
| `voice.core` | user prefers spoken summaries in the morning |
| `action.local` | user frequently asks ANIMA to manage a project folder |
| `presence.core` | repeated check-in preference during late-night work |

The source module provides evidence. Memory Core performs promotion, deduplication, conflict handling, and retention governance.

## Degraded Modes

Memory Core can be present but degraded.

Examples:

- user locked
- Soul DB unavailable
- runtime DB unavailable
- embedding provider unavailable
- pending memory backlog too large
- consolidation disabled
- archive unavailable

The agent should know degraded status. For example, if durable memory writes are unavailable, ANIMA can still answer but should not claim it saved something permanently.

## User Controls

Memory controls should include:

- review pending memory candidates
- correct memory
- forget memory
- inspect memory evidence
- configure automatic memory extraction
- configure sensitive-source promotion rules
- inspect consolidation health

Memory is foundational, but user ownership still comes first.

## Retention Authority

Memory Core is the only boundary that turns experience into durable identity.

That authority is why it must be conservative:

- supersede instead of mutating silently
- preserve evidence
- avoid turning temporary emotions into traits
- avoid promoting raw sensor data
- prefer stable patterns over one-off noise
- keep user correction paths available

The Core is portable and mortal. Memory Core is the steward of what enters it.
