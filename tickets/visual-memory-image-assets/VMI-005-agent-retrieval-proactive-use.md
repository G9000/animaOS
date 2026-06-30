# VMI-005 - Agent retrieval and proactive image use

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `VMI-000`
- Depends on: `VMI-004`
- Owner: Codex
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 12:23 MYT
- Started: 2026-06-29 12:10 MYT
- Completed: 2026-06-29 12:23 MYT

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
- 2026-06-29 12:10 MYT - Claimed by Codex after completing `VMI-004`; starting bounded agent retrieval context work.
- 2026-06-29 12:23 MYT - Added bounded `relevant_images` prompt context, `search_images`, source metadata, proactive image candidate selection, and repeat suppression.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_retrieval_context.py apps/server/tests/test_proactive_image_memory.py -q`
- Changed paths:
  - `apps/server/src/anima_server/services/images/rag.py`
  - `apps/server/src/anima_server/services/agent/memory_blocks.py`
  - `apps/server/src/anima_server/services/agent/tools.py`
  - `apps/server/src/anima_server/services/agent/proactive.py`
  - `apps/server/src/anima_server/api/routes/chat.py`
  - `packages/api-client/src/types.ts`
  - `apps/server/tests/test_image_retrieval_context.py`
  - `apps/server/tests/test_proactive_image_memory.py`
- Notes:
  - Retrieval reuses the turn query embedding for prompt context, so context assembly does not add a second provider call.
  - `search_images` uses the configured embedding path only when the agent explicitly invokes the tool.
