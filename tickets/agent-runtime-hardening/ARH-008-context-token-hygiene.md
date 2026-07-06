# ARH-008 - Context and token hygiene

- Status: backlog
- Priority: P1
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

Bound what enters and re-enters the context window: cap tool-output replay, slim per-step persistence snapshots, stop mid-structure truncation in block builders, and calibrate the token estimate.

## Problem

1. **Tool-output replay.** `_TOOL_RETURN_CHAR_LIMIT = 50_000` (`services/agent/executor.py:359`); outputs land in `messages` (`runtime.py:564-567`), persist in-context (`persistence.py:354-366`), and replay on every subsequent LLM call until compaction — one large `conversation_search` result ≈ 12k tokens re-billed per step per turn.
2. **Per-step snapshots.** `create_step` (`persistence.py:574-598`) stores `request_json` containing every message snapshot (including those giant outputs) for each step — O(steps × history) row size, serialized on the critical path before the client's `done` event.
3. **Mid-structure truncation.** Raw `value = value[:2000]` / `[:1500]` slices in block builders (`memory_blocks.py:450-451, 481-482, 512-513, 562-563, 598-599, 1330-1331, 1497-1498`) cut mid-fact ("user is allergic to") and run *before* the planner, so `prompt_budget._truncate_at_boundary` (`prompt_budget.py:307-320`) never sees the full text.
4. **chars/4 estimate.** `prompt_budget.py:147-150` estimates tokens as `ceil(chars/4)`; for CJK/emoji/code the real ratio is 1–2, so the "24K-char ≈ 6K-token" budget can be 12–24K real tokens; the only backstop is substring-matching provider overflow errors (`llm.py:232-249`). Tool JSON schemas and the system template scaffolding are never counted at all (`resolve_context_budget_tokens`, `prompt_budget.py:106-119`). Related default inconsistency: with `agent_context_window_tokens` unset, the context budget falls back to `agent_max_tokens` (4096) while `DEFAULT_BUDGET` simultaneously allows ~6K tokens of blocks alone (`prompt_budget.py:106`, `config.py:49`).

## Implementation Notes

1. Add a separate in-history cap (suggest 8_000 chars) applied where tool results are appended to `messages` and persisted; keep the full (50k-capped) output in the step trace/tool-result record only. Truncation marker should state how much was elided so the model knows.
2. Slim `request_json`: store per-message previews (reuse `streaming.py`'s `_preview_text`) or only the step's delta messages; keep enough to debug (roles, tool-call ids, truncated content).
3. Replace the raw slices with the existing `_truncate_lines` helper (`memory_blocks.py:1454-1461`) or drop the pre-caps and let `_BLOCK_POLICIES.max_chars` handle it at the planner boundary.
4. Token calibration: prefer real counts — Anthropic `count_tokens` (cheap; can calibrate a per-blockset ratio once and reuse) or a local tokenizer if one is already a dependency. Minimum bar: chars/3 conservative ratio + a fixed reservation for template scaffolding and serialized tool schemas in `resolve_context_budget_tokens`; fix the legacy fallback so the block budget derives from the same window value it validates against.

## Deliverables

- In-history/persisted tool-return cap decoupled from the trace cap.
- Slimmed per-step `request_json`.
- Boundary-aware truncation in all block builders (no raw slices left in `memory_blocks.py`).
- Calibrated token estimate including tool schemas + scaffolding; consistent legacy fallback.
- Tests: a 50k tool output appears ≤8k in the next LLM request payload but full in the step trace; step row size bounded; a block ending mid-line is truncated at a boundary; budget estimate for a CJK-heavy prompt within tolerance of a real count.

## Acceptance

- A turn with one large tool result no longer re-bills the full output on every subsequent step.
- No `[:2000]`-style raw slices remain in block builders.
- Estimated tokens ≥ real tokens for representative fixtures (estimate is conservative, never optimistic).
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
