# VMI-005 - Agent retrieval and proactive image use

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `VMI-000`
- Depends on: `VMI-004`
- Owner: unassigned
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 10:53 MYT
- Started:
- Completed:

## Goal

Let Anima retrieve indexed images during agent context assembly and safely ask proactive follow-up questions about relevant images.

## Deliverables

- Compact image retrieval block for agent prompts.
- Bounded image search helper or tool for explicit visual recall.
- Image source pills for assistant responses that cite images.
- Proactive image candidate selection with repeat suppression.
- Tests proving the agent path uses indexed annotations and resolves back to image assets.
- Tests for retrieval relevance, user isolation, deleted asset exclusion, and proactive candidate behavior.

## Acceptance

- The agent can find image annotations by semantic query.
- Retrieval results include image asset id, label, source message/thread provenance, and attachment URL metadata.
- Prompt context includes only a small, bounded set of relevant image snippets.
- Proactive image prompts never include unindexed, deleted, or cross-user assets.
- Repeated proactive prompts for the same image are suppressed.
- Source pills identify referenced image assets.

## Activity Log

- 2026-06-29 10:53 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
