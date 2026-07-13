---
title: "PRD: Social Memory Identity Discovery v1"
description: Preserve person identity, duplicate-name resolution, and audience-safe memory boundaries for future multi-person Anima conversations.
category: prd
version: "1.0"
status: draft
date: 2026-07-01
related:
  - F14-multi-user-group-memory.md
  - single-user-temporal-memory-v2.md
  - ../../architecture/memory/memory-system.md
---

# PRD: Social Memory Identity Discovery v1

| Field | Value |
| --- | --- |
| Status | Draft |
| Date | 2026-07-01 |
| Owner | AnimaOS Engineering |
| Priority | P1 design constraint, deferred implementation |
| Related PRD | [F14 Multi-User & Group Memory](F14-multi-user-group-memory.md) |
| Related PRD | [Single-User Temporal Memory v2](single-user-temporal-memory-v2.md) |
| Related Plan | [2026-07-01 Social Memory Identity Discovery](../../superpowers/plans/2026-07-01-social-memory-identity-discovery.md) |

> Names are aliases, not identity boundaries.

## Summary

Anima should eventually talk with and remember multiple people without confusing their personal memories, even when several people share the same display name. This version records the product and architecture rules for identity discovery, duplicate-name handling, and audience-safe memory retrieval so agent runtime and harness work can preserve the correct extension points now.

This is not the full multi-user implementation. It is a durable design constraint for future runtime, memory, adapter, and harness work.

## Context

AnimaOS currently treats `user_id` as the main memory boundary. That is correct for the current owner-only product, but it will not be enough once Anima can talk with multiple people.

F14 already defines the broad target: one Anima identity serving multiple participants, with per-person private memory and shared group memory. The missing sharper rule is identity discovery:

- "Alex" is not enough to know which person is being discussed.
- A memory can be about one person but owned by another person's private context.
- A group conversation can create shared memories without exposing private one-on-one memories.
- Anima must ask for clarification when an identity guess would affect memory boundaries.

## Product Goals

1. Make duplicate-name handling a first-class memory requirement.
2. Separate who is speaking from who a memory is about and who may hear it.
3. Prevent private memories about one person from leaking to another person with the same name.
4. Let future adapters identify participants through stable external account links.
5. Give the runtime and harness concrete fields and probes to design around before full F14 implementation.

## Core Concepts

### Person

A `Person` is a stable identity known to Anima. A display name is only one attribute on that identity.

Example shape:

```ts
type Person = {
  id: string;
  displayName: string;
  aliases: string[];
  relationshipLabels: string[];
  linkedAccounts: LinkedAccount[];
  identityEvidenceIds: string[];
  confidence: number;
};
```

Examples:

- `person_1`: Alex Chen, work colleague, Telegram user `812378`
- `person_2`: Alex Tan, cousin, Discord user `alex_tan`
- `person_3`: Alex, temporary guest, no linked account yet

### Speaker, Subject, Audience, Scope

Every memory-sensitive turn should distinguish four roles:

| Role | Meaning |
| --- | --- |
| Speaker | The person currently talking to Anima |
| Subject | The person, group, project, or entity the memory is about |
| Audience | The people who can hear the current response |
| Scope | The memory boundary that decides whether a memory can be retrieved or spoken |

A private memory from Leo about Alex is not automatically available to Alex. It may have `subjectPersonId=Alex` while still having `createdByPersonId=Leo` and `scope=leo_private`.

## Memory Boundary Rules

1. Display names must never be used as memory boundaries.
2. Memory retrieval must be scoped by stable identifiers: `personId`, `groupId`, and `audiencePolicy`.
3. If identity confidence is low and retrieval could expose personal memory, Anima must ask a clarification question.
4. A memory about a person belongs to the scope in which it was learned unless explicitly shared.
5. Private one-on-one memories must not appear in group context by default.
6. Shared group memories are visible only to current group members and owner-authorized views.
7. Temporary guests can be represented, but their memories should remain low-confidence until linked or confirmed.
8. Person merge and split operations must preserve audit history and evidence.

## Identity Discovery

Anima should resolve identity using layered evidence:

1. Direct account identity from an adapter or login session.
2. Current thread or group participant list.
3. Explicit relationship labels from the owner or participant.
4. Recent conversational context.
5. Existing aliases and linked accounts.
6. Knowledge graph entity evidence.
7. User confirmation when ambiguity remains.

Ambiguous names must produce normal human clarification:

> "Do you mean Alex from work or Alex your cousin?"

Anima should avoid revealing private facts inside the clarification prompt. The options can use safe labels, not sensitive memories.

## Audience Policy

Every agent turn should eventually carry an audience policy:

```ts
type AudiencePolicy = {
  speakerPersonId: string;
  audiencePersonIds: string[];
  conversationScope: "owner_private" | "person_private" | "group_shared" | "guest_present";
  groupId?: string;
  allowPrivateOwnerMemory: boolean;
  allowGroupMemory: boolean;
  allowAbstractedSensitiveMemory: boolean;
};
```

The policy is the gate in front of:

- prompt memory blocks
- semantic retrieval
- long-memory tools
- transcript recall
- proactive greetings
- dashboard memory cards
- source pills, traces, and dry-run prompt output

## Harness Scenarios

The agent runtime and harness should eventually include deterministic privacy probes:

| Scenario | Expected behavior |
| --- | --- |
| Two people named Alex exist | Anima asks which Alex unless the active context identifies one |
| Owner has a private memory about Alex A | The memory never appears when Alex B is the speaker |
| A memory was learned in a group | It is available only inside that group scope |
| A private memory names someone in the room | Anima withholds it unless the owner explicitly shares it |
| Adapter passes Telegram sender ID | Anima maps the message to the linked `Person`, not only the display name |
| Temporary guest says "I'm Alex" | Anima creates or uses a low-confidence guest identity and asks before merging |
| Debug trace is opened in shared mode | Hidden memories are absent from trace fragments and prompt dumps |

## What Users See

The owner eventually sees:

- a people list with aliases and relationship labels
- unresolved identity prompts when a name is ambiguous
- merge/split controls for duplicate people
- memory scope labels such as private, group shared, guest context, or sealed
- friend/private mode as a visible audience state, not a dashboard-only filter

Other participants eventually experience:

- Anima remembers them separately from other people with the same name
- Anima does not repeat another person's private memories to them
- Anima can remember shared group context when they are in that group

## Relationship To Existing Work

This PRD narrows and sharpens F14. F14 defines groups and multi-user memory. This document defines the identity and audience rules that make F14 safe.

It also affects Single-User Temporal Memory v2 because structured profiles, retrieval routing, salience, and evidence should eventually operate over `Person` and `AudiencePolicy`, not only owner `user_id`.

For current agent runtime and harness work, this PRD should be treated as a future-proofing constraint:

- do not assume a display name identifies a person
- do not collapse speaker, subject, and audience into one `userId`
- preserve room for audience policy on runtime calls and tests

## Out Of Scope

- Full F14 implementation.
- Group chat UI.
- Multi-user auth flows.
- Adapter user-linking implementation.
- Database migrations for person/group identity.
- Automatic face recognition or biometric identity.
- Cross-owner or cloud-hosted multi-tenant permissions.

## Success Metrics

| Metric | Target | How to measure |
| --- | --- | --- |
| Identity ambiguity handling | No memory-boundary decision uses display name alone | Harness privacy probes |
| Privacy boundary | 0 private-memory leaks across people with same name | Unit and integration tests |
| Runtime readiness | Agent turn contract can carry speaker, audience, scope, and policy fields | Contract tests |
| Trace safety | Hidden memory text absent from debug traces in restricted audience mode | Prompt and trace tests |
| F14 readiness | Future group memory can reuse person identity and audience policy concepts | Plan review |

## References

- [F14 Multi-User & Group Memory](F14-multi-user-group-memory.md)
- [Single-User Temporal Memory v2](single-user-temporal-memory-v2.md)
- [Memory System Architecture](../../architecture/memory/memory-system.md)
- [Agent Runtime Architecture](../../architecture/agent/agent-runtime.md)
