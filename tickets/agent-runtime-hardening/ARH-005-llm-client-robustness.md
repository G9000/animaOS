# ARH-005 - LLM client robustness and capability gating

- Status: in-review
- Priority: P1
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 03:10 MYT
- Started: 2026-07-07 02:40 MYT
- Completed:

## Goal

LLM error handling is structured (status codes, not substring matching), every LLM call path retries transient failures, and model capability gating matches current model generations.

## Problem

1. **Substring retry classification.** `_is_retryable_error` (`services/agent/runtime.py:87-101`) greps the exception message for `"429"`/`"500"` etc. — 529 (Anthropic overload) and 408 are absent; a benign "429" in an ID makes a permanent 400 retryable; the `retry-after` header is discarded even though `wrap_llm_error` (`llm.py:252-262`) has the `httpx.HTTPStatusError` in hand.
2. **Background calls never retry.** `call_llm_for_text` (`llm_json.py:45-47`) calls `client.ainvoke` directly; the backoff loop lives only in `AgentRuntime._invoke_llm_with_retry` (`runtime.py:1306`). Every extraction path built on `call_llm_for_json` (memory extraction, conflict checks, knowledge graph, profile synthesis) fails permanently on one transient 429, silently losing memories.
3. **Stale vision gating.** `_VISION_MODEL_PATTERNS` (`model_capabilities.py:12-26`) matches retired `claude-3`/`claude-*-4` but not `claude-sonnet-5`/`claude-fable-5` — image attachments are silently stripped on current models.
4. **Unconditional `temperature`.** `anthropic_client.py:164-165` sends `agent_temperature` in every Anthropic payload; current Anthropic models (Opus 4.7+, Sonnet 5, Fable 5) reject it with a 400, so a config that works on Ollama bricks Anthropic on model upgrade.
5. **`stop_reason` unread.** `_normalize_response` (`anthropic_client.py:388-436`) stashes `stop_reason` unread; a `max_tokens` cut (default cap 4096, `:15`) is delivered as a complete answer, a `refusal` as a silent empty message.
6. **Lossy tool round-trip.** `anthropic_client.py:12,341` round-trips tools through the OpenAI serializer; `_serialize_anthropic_tool` (`:340-365`) drops the `strict` flag, so `agent_strict_tool_schemas=True` (`config.py:40`) has zero effect on Anthropic.

## Implementation Notes

1. In `wrap_llm_error`, attach `status_code: int | None` and `retry_after: float | None` to `LLMInvocationError` (parse `Retry-After` header, seconds or HTTP-date). Reclassify retryability on the integer: `{408, 429, 500, 502, 503, 504, 529}` plus transport timeouts/connection errors. Keep a narrow message-based fallback only for errors with no status.
2. Extract the backoff loop from `AgentRuntime._invoke_llm_with_retry` into `invoke_with_retry(...)` in `llm.py` (honoring `retry_after` as the backoff floor); have both the runtime and `call_llm_for_text` use it. Config stays on the existing retry settings (`config.py:61`).
3. Vision patterns: add a generic `claude-` prefix match (all current Claude models accept images) and extend the OpenAI-side list; keep the explicit deny-list approach only for models known to lack vision.
4. Gate `temperature` per provider/model: omit it entirely for Anthropic models that reject it (simplest: omit for the anthropic provider unless the model is in a known-accepting list).
5. Surface `stop_reason` on the normalized response / `StepExecutionResult`; at minimum log WARNING on `max_tokens` and `refusal` in the runtime, and mark the result so callers can react (retry with a higher cap is a follow-up, not required here).
6. Serialize Anthropic tools directly from the source tool object and pass `strict: true` through when `agent_strict_tool_schemas` is set.
7. While in the file: evaluate whether adopting the official `anthropic` SDK (`AsyncAnthropic`) resolves 1/2/5 wholesale (typed errors, built-in retry, `count_tokens`, native `cache_control`). Adopt only if it clearly reduces code; otherwise record the decision in this ticket's notes. ARH-006 and ARH-008 benefit either way.

## Deliverables

- Structured `status_code`/`retry_after` on `LLMInvocationError`; integer-based retry classification.
- Shared `invoke_with_retry` used by runtime, `call_llm_for_text`, and (via ARH-001) compaction.
- Current-generation vision gating; temperature gating for Anthropic.
- `stop_reason` surfaced with WARNING on truncation/refusal.
- `strict` propagated to Anthropic tool schemas.
- Tests: 529 with `Retry-After` retries after the floor; permanent 400 containing "429" text does not retry; `call_llm_for_json` survives one transient 429; image parts retained for `claude-sonnet-5`; Anthropic payload omits `temperature`; `stop_reason=max_tokens` flags the result.

## Acceptance

- No retryability decision anywhere reads exception message text when a status code exists.
- Background extraction survives a single transient provider error.
- Focused tests pass.

## Activity Log

- 2026-07-07 00:28 MYT - Ticket created.
- 2026-07-07 03:10 MYT - Implemented on branch `worktree-agent-runtime-hardening-p2`: `LLMInvocationError` carries `status_code`/`retry_after` (set in `wrap_llm_error`, HTTP-date and delta-seconds both parsed); retryability decided on the integer (`{408,409,429,500,502,503,504,529}`) via shared `is_retryable_llm_error` with a narrow message fallback that no longer matches bare numeric substrings; `retry_backoff_delay` honors retry-after as the backoff floor (runtime loop updated); new `invoke_with_retry` wraps `call_llm_for_text`, so every `call_llm_for_json` extraction path now survives transient errors; vision gating matches the bare `claude-` prefix (all Claude 3+ models accept images); temperature dropped for models that reject sampling params (fable-5/mythos/opus-4-7/opus-4-8/sonnet-5, per the Anthropic API reference); `stop_reason` surfaced as a first-class `AnthropicResponse` field with WARNING on `max_tokens`/`refusal` (non-streaming and streaming); `strict` passes through Anthropic tool serialization as top-level `strict: true`. Official-SDK adoption evaluated and deferred: the retry/typing gaps are now closed in-tree, and the SDK swap belongs with the ARH-006 caching work if at all.

## Validation

- Commands:
  - `uv run --directory apps/server pytest tests/test_llm_client_robustness.py -q` → 21 passed
  - `uv run --directory apps/server pytest tests/test_agent_anthropic_client.py tests/test_agent_llm.py tests/test_agent_openai_compatible_adapter.py tests/test_agent_runtime.py tests/test_chat_attachments.py tests/test_llm_json.py tests/test_llm_retry.py tests/test_runtime_enhancements.py -q` → 129 passed
  - `uv run --directory apps/server pytest tests/test_active_recall.py tests/test_agent_compaction.py tests/test_agent_consolidation.py -q` → 91 passed
- Changed paths:
  - apps/server/src/anima_server/services/agent/llm.py
  - apps/server/src/anima_server/services/agent/runtime.py
  - apps/server/src/anima_server/services/agent/llm_json.py
  - apps/server/src/anima_server/services/agent/model_capabilities.py
  - apps/server/src/anima_server/services/agent/anthropic_client.py
  - apps/server/tests/test_llm_client_robustness.py
- Notes:
  - 21 new tests: status/retry-after attachment, 529/408/409 retryable, permanent-400-with-"429"-body not retryable, retry-after floor + cap, transient-then-success recovery, permanent errors raise immediately, `call_llm_for_json` survives one 429, current-generation vision, temperature gating, stop_reason surfacing + degraded warnings, strict passthrough.
