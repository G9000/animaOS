# ARH-001 - Fix Anthropic LLM compaction endpoint

- Status: backlog
- Priority: P0
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 00:28 MYT
- Started:
- Completed:

## Goal

Make LLM-powered compaction summaries actually work on the Anthropic provider instead of silently 404ing and falling back to crude line-based summaries.

## Problem

`summarize_with_llm` (`services/agent/compaction.py:345-358`) hand-builds a raw httpx POST to `{base_url}/chat/completions` with an OpenAI-shaped payload. For `provider="anthropic"` the base URL is `https://api.anthropic.com/v1` (`llm.py:31`), which has no `/chat/completions` endpoint. Every call 404s, the bare `except Exception` at `compaction.py:367` swallows it at debug level, and compaction degrades to `render_summary_text` permanently with no signal.

## Implementation Notes

1. Delete the raw httpx call. Route through the existing provider abstraction: build a client via `create_provider_chat_client(...)` and call it with the summarization prompt (or reuse `call_llm_for_text` from `llm_json.py` if it fits the message shape). This makes the code provider-agnostic for free.
2. Raise the failure log from debug to WARNING on the `anima.runtime.degraded` logger (see plan cross-cutting decisions) so a broken summarizer is visible.
3. Keep the `render_summary_text` fallback — the change is about routing and visibility, not removing the safety net.
4. After ARH-005 lands, this path picks up shared retry automatically; do not build bespoke retry here.

## Deliverables

- `summarize_with_llm` routed through the provider chat client, no raw `/chat/completions` POST remaining in `compaction.py`.
- WARNING-level degraded log on summarization failure.
- Test in `apps/server/tests/` mocking the provider client: asserts the Anthropic provider path produces an LLM summary, and that a client failure logs the degraded warning and falls back.

## Acceptance

- With `provider="anthropic"` configured, compaction produces an LLM summary (mocked client, no 404 path reachable).
- Summarization failure is visible at WARNING, not debug.
- Existing OpenAI-compatible provider behavior unchanged.
- Focused tests pass.

## Activity Log

- 2026-07-07 00:28 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
