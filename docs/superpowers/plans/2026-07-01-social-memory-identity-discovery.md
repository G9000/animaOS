# Social Memory Identity Discovery Plan

> Deferred implementation plan. Use this as a runtime and harness design constraint until F14 is scheduled.

**Goal:** Preserve the architecture needed for Anima to distinguish multiple people, including people with the same display name, without leaking private memories across speakers, subjects, audiences, or groups.

**PRD:** `docs/prds/memory/social-memory-identity-discovery-v1.md`

**Status:** Draft, not scheduled for immediate implementation.

## Scope

This plan documents the future work order for social memory identity discovery. It should not pull focus from agent runtime and harness work. The near-term requirement is to keep runtime contracts and harness probes compatible with future `Person` and `AudiencePolicy` concepts.

## Design Principles

1. Names are aliases, not identity boundaries.
2. `speaker`, `subject`, `audience`, and `scope` are separate concepts.
3. Memory retrieval must be gated by stable IDs and policy, not display text.
4. When identity ambiguity affects privacy, Anima asks instead of guessing.
5. Private memory can be about someone without being available to that person.
6. Group memory is structurally separate from private memory.

## Phase 0: Runtime And Harness Future-Proofing

Purpose: keep current runtime work from hard-coding the wrong assumptions.

- [ ] Define a lightweight runtime context shape that can later carry `speakerPersonId`, `audiencePersonIds`, `conversationScope`, `groupId`, and `memoryPolicy`.
- [ ] Add harness fixtures for identity ambiguity and privacy boundaries, even if marked pending until storage exists.
- [ ] Ensure debug trace and dry-run prompt contracts can respect audience filtering.
- [ ] Keep current single-user behavior mapped to a default owner-private policy.

Done when:

- Runtime and harness design docs mention speaker, subject, audience, and scope separately.
- At least one pending or documented harness probe covers duplicate-name ambiguity.

## Phase 1: Person Identity Registry

Purpose: create stable person records before multi-person memory uses them.

- [ ] Add `Person` identity model or adapt `User` with a clear owner/participant/person distinction.
- [ ] Store aliases, relationship labels, linked external accounts, confidence, and evidence links.
- [ ] Add merge/split operations with audit history.
- [ ] Add owner-facing inspection and correction APIs.

Done when:

- Multiple people can share the same display name without sharing a memory boundary.
- A person can be linked to adapter identities such as Telegram or Discord sender IDs.

## Phase 2: Audience Policy

Purpose: create the policy gate used by prompts, retrieval, tools, dashboard, and traces.

- [ ] Add `AudiencePolicy` construction for private, group, and guest-present contexts.
- [ ] Thread policy through agent runtime calls.
- [ ] Thread policy through automatic retrieval, memory tools, transcript recall, and proactive services.
- [ ] Add prompt guidance for ambiguity and safe clarification.

Done when:

- Restricted audience mode changes what memory can enter context.
- Hidden memory text is absent from prompt dumps, traces, and source fragments.

## Phase 3: Memory Scope Metadata

Purpose: make memory ownership and subject identity explicit.

- [ ] Add memory metadata for `createdByPersonId`, `subjectPersonIds`, `sourceScope`, and `allowedAudience`.
- [ ] Add classification rules for private, group shared, guest, abstract-only, and sealed memory.
- [ ] Update evidence rows to identify speaker and subject separately where possible.
- [ ] Update retrieval filters to enforce policy before prompt assembly.

Done when:

- A memory about Alex A cannot be retrieved for Alex B.
- A memory learned privately from Leo about Alex remains Leo-private unless explicitly shared.

## Phase 4: F14 Group Memory Integration

Purpose: integrate this identity layer with the broader multi-user/group-memory PRD.

- [ ] Add group records and membership.
- [ ] Add group memory and group profile extraction.
- [ ] Scope episodes, knowledge graph relations, and proactive greetings by group.
- [ ] Support adapter-provided participant identity.

Done when:

- Group conversations create shared group memory.
- Private memories remain isolated from group responses.
- Existing single-user flows still pass.

## Test Strategy

- Contract tests for runtime context serialization.
- Unit tests for identity resolution and ambiguity decisions.
- Retrieval tests proving display names are not sufficient for memory access.
- Prompt rendering tests proving hidden memory is not injected.
- Trace tests proving hidden fragments are not visible through debug surfaces.
- Adapter fixture tests for external account identity mapping.

## Verification Commands

When implementation begins, use focused commands by phase:

```powershell
bun run test
bun run lint
bun run build
```

Backend-focused work should also use:

```powershell
cd apps/server
python -m pytest tests/<focused-test-file>.py -v
```

## Risks

| Risk | Mitigation |
| --- | --- |
| Full social memory distracts from runtime stabilization | Keep implementation deferred; only preserve contract fields and harness probes now |
| Identity resolver guesses wrong person | Ask clarification when confidence is low and memory boundaries matter |
| Private memory leaks through debug traces | Apply audience policy before trace/source fragment construction |
| Person model duplicates existing `User` model poorly | Decide explicitly whether `Person` wraps, extends, or separates from auth `User` before migration |
| Group memory adds context pressure | Keep group and participant blocks budgeted and route detailed recall through tools |

## Handoff

Do not start Phase 1 until agent runtime and harness foundations are stable or the user explicitly reprioritizes F14/social memory. Phase 0 can be used immediately as design guidance for runtime contracts.
