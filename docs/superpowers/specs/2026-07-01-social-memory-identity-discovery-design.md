# Social Memory Identity Discovery Design

**Status:** Draft
**Date:** 2026-07-01
**Purpose:** Preserve the design decision that Anima must distinguish people by stable identity and audience policy, not by display names.

## Source Of Truth

- Product requirements: [Social Memory Identity Discovery v1](../../prds/memory/social-memory-identity-discovery-v1.md)
- Sequencing plan: [2026-07-01 Social Memory Identity Discovery](../plans/2026-07-01-social-memory-identity-discovery.md)
- Ticket tracker: [SID-000 Social Memory Identity Discovery](../../../tickets/social-memory-identity-discovery/SID-000-parent.md)
- Related broad PRD: [F14 Multi-User & Group Memory](../../prds/memory/F14-multi-user-group-memory.md)

## Design Decision

Names are aliases, not memory boundaries. Future agent runtime and harness work must keep these concepts separate:

- speaker: who is currently talking
- subject: who or what the memory is about
- audience: who can hear the current response
- scope: which memory boundary allows retrieval or speech

The system should never use a display name such as "Alex" as the authority for memory access. It should use stable identifiers such as `personId`, `groupId`, and an `AudiencePolicy`.

## Current Implementation Stance

This is a deferred implementation. It should not interrupt agent runtime and harness stabilization.

Immediate impact:

- Runtime contracts should leave room for `speakerPersonId`, `audiencePersonIds`, `conversationScope`, `groupId`, and `memoryPolicy`.
- Harness planning should include privacy probes for duplicate names and audience leakage.
- Existing single-user behavior maps to owner-private policy.

Deferred impact:

- Person identity registry.
- Identity merge and split.
- Group memory and group profiles.
- Adapter participant linking.
- Memory scope migrations.

## Non-Negotiable Rules

1. A memory can be about someone without being available to that person.
2. If identity ambiguity affects memory privacy, Anima asks for clarification.
3. Private one-on-one memory does not enter group context by default.
4. Hidden memories must also be absent from prompt traces, source fragments, and dry-run output.
5. F14 social memory should reuse this identity and audience model rather than inventing a separate boundary.
