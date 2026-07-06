# ARH-006 - Anthropic prompt caching with stable prefix

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: `ARH-005`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 00:28 MYT
- Started:
- Completed:

## Goal

Stop re-paying full input price for the mostly-static system prompt on every Anthropic turn: restructure the prompt into a byte-stable cached prefix and a volatile suffix, and enable `cache_control`.

## Problem

`anthropic_client.py:158-159` sends `system` as one plain string with no `cache_control` anywhere. Worse, the prompt is structurally cache-hostile: `system_prompt.py:46-51,91` + `templates/system_prompt.md.j2:16` inject a second-precision `now.isoformat()` timestamp near the top, and all per-turn volatile content (retrieval scores like `relevance: 0.87` at `memory_blocks.py:412`, `today: {date}` at `:543-559`, mood/relationship state) renders inside that same string. The prefix changes byte-wise every turn, so even adding `cache_control` today would hit 0%. The entire ~24K-char block budget (rules, guardrails, persona, tools) is re-billed uncached each request.

## Implementation Notes

1. **Restructure `system` into an ordered block list** (Anthropic accepts `system` as a list of text blocks):
   - Stable prefix, in fixed order: core rules, guardrails, persona, relationship-stage instructions keyed to a coarse bucket (not raw turn count), static tier-0 memory blocks (identity/persona soul blocks that change rarely).
   - Set `{"cache_control": {"type": "ephemeral"}}` on the *last* stable block (one breakpoint is enough; up to 4 are allowed if a second tier helps, e.g. persona changes weekly vs rules never).
   - Volatile suffix, after the breakpoint: current time (rounded to the minute at most — prefer injecting it into the latest user message instead), retrieved memory fragments with their scores, mood/emotional state, today-context.
2. **Purge volatility from the stable prefix**: audit `system_prompt.py` + the Jinja template for anything time-, score-, or turn-derived; move each to the suffix or the user turn. Retrieval scores can also simply be dropped from the rendered text if they don't influence the model measurably.
3. **Tool schemas**: tools are part of the cache prefix on Anthropic — ensure tool ordering is deterministic (sort by name at serialization) so the tool block doesn't bust the cache when sets are equal.
4. **Client change** (`anthropic_client.py`): accept `system` as `str | list[dict]`; pass blocks through; keep the plain-string path for other providers (concatenate blocks in order).
5. **Mid-turn refresh caution**: the memory-refresh rebuild (`runtime.py:632`) must produce the same stable prefix bytes — rebuild only the suffix blocks. Coordinate with ARH-013 which fixes that rebuild's dropped inputs.
6. **Measure**: log `cache_read_input_tokens` / `cache_creation_input_tokens` from the usage payload so the win is observable.

## Deliverables

- `system` assembled as stable-prefix blocks + volatile-suffix blocks with one `cache_control` breakpoint.
- No second-precision timestamp anywhere in the prompt; time injected at minute granularity in the suffix or user turn.
- Deterministic tool ordering.
- Cache usage counters logged per turn.
- Integration test: build the prompt for two consecutive turns (different retrieved memories, different time) and assert the serialized prefix up to the breakpoint is byte-identical; unit test that the Anthropic payload carries `cache_control` on the intended block.

## Acceptance

- Two consecutive turns produce a byte-identical cached prefix.
- Non-Anthropic providers receive an equivalent concatenated string (no behavior change).
- `cache_read_input_tokens > 0` on the second of two consecutive live turns (manual smoke).
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
