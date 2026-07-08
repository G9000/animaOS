# ARH-000 - Agent Runtime Hardening Parent Tracker

- Status: backlog
- Priority: P0
- Scope: `apps/server`, `docs/superpowers/plans`, `tickets/agent-runtime-hardening`
- Depends on: none
- Owner: Claude (Fable 5)
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-08 02:20 MYT
- Started: 2026-07-07 00:45 MYT
- Completed:

## Goal

Track the runtime hardening initiative that fixes silently-broken paths, adds durability to background cognition, cuts per-turn LLM cost via prompt caching and dirty-checks, and consolidates drifted duplicate logic — without changing product behavior.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `ARH-001` | Fix Anthropic LLM compaction endpoint | `in-review` | none |
| `ARH-002` | Cancellation-safe turn lifecycle | `in-review` | none |
| `ARH-003` | Optimistic locking for soul-block writes | `in-review` | none |
| `ARH-004` | Background retry hygiene and persisted gates | `in-review` | none |
| `ARH-005` | LLM client robustness and capability gating | `in-review` | none |
| `ARH-006` | Anthropic prompt caching with stable prefix | `in-review` | `ARH-005` |
| `ARH-007` | Dirty-checks for background cognition | `in-review` | `ARH-004` |
| `ARH-008` | Context and token hygiene | `in-review` | none |
| `ARH-009` | Embedding contract and store consistency | `in-review` | none |
| `ARH-010` | Crash-durable memory extraction | `in-review` | `ARH-004` |
| `ARH-011` | TTFT: parallel assembly and single-decrypt retrieval | `in-review` | none |
| `ARH-012` | Retrieval scoring correctness | `in-review` | none |
| `ARH-013` | Deduplicate drifted turn and sleep logic | `in-progress` | `ARH-002` |

## Deliverables

- Working LLM-powered compaction summaries on the Anthropic provider.
- No stranded `running` runs on client disconnect at any turn stage.
- Version-checked soul-block writes; background reflections can no longer erase concurrent user memory writes.
- Retry caps with backoff on every background retry loop; restart-safe gates.
- Structured LLM error codes, shared retry for background LLM calls, current-model capability gating.
- Cached stable system-prompt prefix on Anthropic; volatile content after the cache breakpoint.
- Persisted contradiction verdicts and input-freshness checks that skip unchanged background work.
- Bounded tool-output replay and slimmed per-step persistence.
- Enforced `(model, dim)` embedding contract with loud failure and re-embed path.
- Commit-before-LLM extraction durability.
- Parallelized turn-context assembly and single-decrypt retrieval hot path.
- Absolute retrieval thresholds that act on raw similarity; heat floor without the 0.0 bypass.
- One copy of the step-tool-call loop, one sleep orchestrator, one stream pump.

## Acceptance

- Every child ticket references this parent.
- The parent status table reflects child progress.
- No product-visible behavior change in chat output.
- Focused pytest coverage lands with every child ticket.
- Final validation includes a full agent-turn smoke and a degraded-log grep, not only typecheck.

## Completed Tickets

- none

## Activity Log

- 2026-07-07 00:28 MYT - Parent tracker created from the 2026-07-07 runtime review.
- 2026-07-07 01:55 MYT - Phase 1 (ARH-001..003) implemented on branch `worktree-agent-runtime-hardening-p1`, all in review.
- 2026-07-08 00:35 MYT - Phase 5 (ARH-011 TTFT, ARH-012 retrieval scoring) implemented on branch `worktree-agent-runtime-hardening-p5`, both in review. Only ARH-013 (Phase 6) remains.
- 2026-07-08 02:20 MYT - Phase 6 (ARH-013) partially implemented on branch `worktree-agent-runtime-hardening-p6`: 4 of 5 sub-items landed with tests (single stream pump, dead-param removal, mid-turn memory-refresh fix, consolidation cursor table + task-run retention). The two highest-risk items are deferred with documented rationale: #1 the shared `_process_step_tool_calls` extraction ("the riskiest refactor in the epic", needs a characterization harness and touches the approval-resume path) and #3 the sleep-orchestrator merge (entangled with ARH-007 freshness gating + a `/sleep` response-contract change). ARH-013 stays `in-progress`.
- 2026-07-08 03:05 MYT - #1 (the shared `_process_step_tool_calls` extraction) landed on `worktree-agent-runtime-hardening-p6`, characterization-test-first: `invoke` and `resume_after_approval` now share one tool-call pipeline (`_execute_validated_calls` + `_process_step_tool_calls`). Only #3 (sleep-orchestrator merge) remains; ARH-013 stays `in-progress` for it.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/agent-runtime-hardening/ARH-000-parent.md
- Notes:
  - planning tracker only
