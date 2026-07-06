# ARH-011 - TTFT: parallel assembly and single-decrypt retrieval

- Status: backlog
- Priority: P2
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

Cut time-to-first-token by parallelizing independent turn-context assembly work, backgrounding feedback correction, and decrypting each retrieved memory exactly once.

## Problem

1. **Serialized assembly.** `_assemble_turn_context`-area code (`services/agent/service.py:1152-1361`) runs sequentially: awaited soul-writer op promotion (`:1165`), awaited `hybrid_search` (`:1186`), sync document RAG (`:1272`), then feedback-signal collection + `apply_memory_correction`, which decrypts up to 50 memory items inline (`feedback_signals.py:450-471`) even though its result cannot affect the current turn (retrieval already ran).
2. **Triple decrypt + double BM25.** In `hybrid_search`, the keyword leg runs BM25 (`embeddings.py:1133`), then `_bm25_rerank` (`:1185`) recomputes an independent, differently-tokenized BM25 over freshly-decrypted content (`:929-931`), and `service.py:1221` decrypts every survivor a third time for fragment building — ~30+ SQLCipher AEAD decrypts on the pre-first-token path for the `limit=15` case.
3. **Sequential "parallel" executor.** `execute_parallel` (`executor.py:187-203`) executes tool calls one-by-one on a shared session while `runtime.py:554-559` believes it's parallel — a step with two 30s server tools or three delegated client tools (300s timeout each, `client_actions.py:122`) holds the per-thread lock for the summed duration.

## Implementation Notes

1. `asyncio.gather` the soul-writer promotion check and `hybrid_search` (they're independent); assess whether document RAG can join the gather (if it doesn't consume hybrid results). Move feedback-signal processing to `_track_background_task` — it feeds *future* turns, not this one.
2. Single-decrypt: decrypt each surviving item once and thread the plaintext through rerank → fragment building (pass a `dict[item_id, plaintext]` alongside the hits, or attach plaintext to the hit object). Delete `_bm25_rerank`'s second BM25 pass — the keyword-leg scores already participate in the RRF merge; if the rerank measurably helps, reuse the leg's scores instead of recomputing with a different tokenizer.
3. `execute_parallel`: give each tool call its own session/`ToolContext` and `asyncio.gather` them. If per-call sessions are risky for some tools (shared mutable tool context), split: genuinely-independent tools gather, session-mutating tools stay serial — and rename/document whichever constraint remains so `runtime.py`'s comment matches reality.
4. Measure before/after: log an assembly-duration and decrypt-count metric per turn so the win is quantified (repo already traces turns; extend the trace payload).

## Deliverables

- Gathered independent assembly steps; feedback correction backgrounded.
- One decrypt per retrieved item on the hot path; second BM25 pass removed.
- Genuinely parallel (or honestly-named) `execute_parallel` with per-call sessions.
- Tests: feedback correction no longer runs before first token (ordering assertion or trace check); decrypt-count fixture asserting ≤1 decrypt per surviving item; two slow mocked tools complete in ~max not ~sum duration.

## Acceptance

- Retrieval results are byte-identical (or rank-equivalent with documented rationale) after the single-decrypt refactor.
- Turn trace shows assembly-phase parallelism and reduced decrypt count.
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
