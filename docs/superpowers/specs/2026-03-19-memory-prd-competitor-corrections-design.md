# Memory PRD Competitor Corrections Design

Date: 2026-03-19
Status: Draft for review
Scope: Competitor-informed editorial correction pass for selected memory PRDs

## Goal

Revise the memory PRDs that are materially affected by the Letta and Mem0 competitor audit so they are:

- competitor-informed without importing architecture baggage,
- aligned with AnimaOS's local-first and encrypted Core constraints,
- internally consistent with the thesis and architecture docs,
- explicit about real risks, assumptions, and scope boundaries.

This pass is editorial and design-level only. It does not change code or implementation plans yet.

## Why This Pass Exists

The competitor audit surfaced two kinds of problems:

1. Missing design considerations that shipped competitors already handle well.
2. Places where current PRD language overstates certainty, scope, or differentiation.

The correction pass should absorb the first category and clean up the second.

## Revision Strategy

Recommended approach: targeted editorial revision.

This pass touches only the PRDs where the audit creates a substantive change in design, risk framing, or product positioning. It avoids a broad rewrite of unrelated memory PRDs.

## In Scope

Primary files:

- `docs/prds/memory/F1-hybrid-search.md`
- `docs/prds/memory/F4-knowledge-graph.md`
- `docs/prds/memory/F5-async-sleep-agents.md`

Conditional file:

- `docs/prds/memory/F7-intentional-forgetting.md`

`F7` should only be revised if the competitor audit yields a concrete improvement in design framing, risk handling, or differentiation beyond stronger justification.

## Out of Scope

- Rewriting unaffected memory PRDs for style consistency alone
- Changing AnimaOS into Letta or Mem0 at the architecture level
- Adding external infrastructure such as Neo4j, Turbopuffer, or multi-backend vector abstractions
- Implementation changes, migrations, or code edits

## Decision Rules

### 1. Thesis-First, Competitor-Informed

Competitor patterns are inputs, not mandates. When a shipped pattern conflicts with the AnimaOS thesis or Core constraints, the default choice is to preserve the thesis unless the competitor pattern offers a clearly superior tradeoff that still fits product goals.

### 2. Import Patterns, Not Baggage

Good patterns may be adopted even when the original architecture is rejected.

Examples:

- adopt relation pruning concepts from Mem0 without adopting Neo4j,
- adopt transcript-aware sleeptime inputs from Letta without adopting open-ended background LLM agents,
- adopt ID-indirection or prompt hardening patterns without copying storage models.

### 3. Separate Substrate Claims from Total-System Claims

When a PRD changes one layer of the system, it should describe that layer precisely.

Example:

- `F1` improves lexical-semantic candidate generation,
- it does not solve graph reasoning,
- it does not solve pattern separation,
- it does not define the entire retrieval theory of the memory system.

### 4. Downgrade Unverified Certainty

Performance numbers, rollback safety claims, and strong benchmark-style wording should be presented as targets or hypotheses unless repo evidence already exists.

### 5. Make Hidden Assumptions Explicit

If a PRD depends on an implementation assumption, corpus limitation, or lifecycle caveat, the document should name it directly in design or risk sections instead of relying on implied behavior.

## Per-PRD Change Matrix

## F1: Hybrid Search

### Why It Changes

The audit confirms that self-hosted BM25 plus vector plus RRF is a real differentiator relative to Letta and Mem0, but it also shows the PRD currently overstates what F1 solves and hides corpus assumptions.

### Required Corrections

- Reframe F1 as a lexical-semantic retrieval upgrade, not the defining answer to the broader memory problem.
- Keep the self-hosted hybrid search differentiation, but ground it as an infrastructure and retrieval-quality advantage.
- Correct integration-path wording so it references the actual retrieval entry points rather than an imprecise prompt-assembly description.
- Add explicit assumptions and risks around corpus coverage if the BM25 corpus is built from vector-backed memory text rather than the full logical memory set.
- Soften unverified claims such as latency, memory footprint, and certainty-heavy wording around configuration choices.

### Non-Changes

- Do not introduce external search infrastructure.
- Do not reframe F1 as solving graph retrieval or cognitive-memory completeness.

## F4: Knowledge Graph

### Why It Changes

The audit shows Mem0 handles graph lifecycle more aggressively than the current F4 plan, especially through relation deletion and lightweight lexical reranking of graph results.

### Required Corrections

- Add a graph-lifecycle section covering stale-relation pruning or deletion strategy.
- Make clear whether pruning happens on write, during sleep tasks, or through a bounded maintenance pass.
- Evaluate a cheap reranking stage for graph search results, especially if result sets are small enough to justify BM25-style reranking without architectural complexity.
- Preserve SQLite-backed graph storage and Core portability as a hard constraint.
- Clarify why AnimaOS deliberately rejects external graph infrastructure despite competitor use.

### Non-Changes

- Do not add Neo4j, Kuzu, or Memgraph as planned dependencies.
- Do not broaden F4 into a full graph platform abstraction.

## F5: Async Sleep Agents

### Why It Changes

Letta's sleeptime system demonstrates useful orchestration patterns, but its open-ended LLM-agent background model does not fit AnimaOS's reliability goals. The PRD should absorb orchestration strengths without inheriting unpredictability.

### Required Corrections

- Tighten the orchestration model around proven patterns such as frequency gating, last-processed tracking, and task-run observability.
- Add clearer language on when transcript-level context should be passed into a structured background task.
- Preserve structured tasks as the default operating model instead of general-purpose background agents.
- Make the reliability tradeoff explicit: less flexible than Letta, but more predictable and auditable.
- Ensure the PRD states which tasks are suitable for transcript-wide context versus narrow event inputs.

### Non-Changes

- Do not convert F5 into a fleet of autonomous background LLM agents.
- Do not weaken the structured-task model to chase flexibility for its own sake.

## F7: Intentional Forgetting

### Why It Might Change

The audit does not reveal a stronger full forgetting design in competitors, but it does sharpen differentiation against Mem0's delete-on-update behavior and may justify cleaner framing of active deletion versus real forgetting.

### Conditional Corrections

- Clarify the distinction between contradiction-driven deletion, passive decay, and derived-reference cleanup.
- Tighten differentiation claims so they are specific rather than broad.
- Keep F7 focused on lifecycle integrity, not just deletion features.

### Skip Condition

If the final editorial review concludes that the existing F7 PRD already captures these points clearly enough, omit changes to avoid churn.

## Editing Rules for the Actual PRD Pass

- Prefer precise product and system language over aspirational rhetoric.
- Keep competitor references in support of decisions, not as the center of the document.
- Preserve the local-first, encrypted Core, SQLite-backed design as a visible constraint where relevant.
- Fix doc drift inside touched files when it affects credibility or understanding.
- Ignore unrelated cleanup in untouched sections.

## Success Criteria

The correction pass succeeds when:

- each selected PRD has a clear audit-driven reason for change,
- no selected PRD overclaims what its feature solves,
- competitor-inspired additions remain compatible with AnimaOS's architecture,
- risks and assumptions are easier to reason about after the edit than before,
- the revised PRDs read as a coherent roadmap rather than borrowed fragments.

## Deliverables

1. Revised PRDs for the selected files.
2. A brief change summary tied to the competitor audit.
3. A clear note on whether `F7` was changed or intentionally left alone.

## References

- `docs/prds/memory/competitor-audit-letta-mem0.md`
- `docs/prds/memory/F1-hybrid-search.md`
- `docs/prds/memory/F4-knowledge-graph.md`
- `docs/prds/memory/F5-async-sleep-agents.md`
- `docs/prds/memory/F7-intentional-forgetting.md`
- `docs/thesis/whitepaper.md`
- `docs/architecture/memory/memory-system.md`
