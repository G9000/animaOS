# Memory PRD Competitor Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revise the selected memory PRDs so they incorporate the Letta and Mem0 competitor audit, stay aligned with AnimaOS's thesis and Core constraints, and remove editorial overclaims or ambiguity.

**Architecture:** This is a docs-only implementation pass over the selected PRDs, with the competitor audit as the source of external patterns and the thesis and architecture docs as the design constraints. The plan edits `F1`, `F4`, and `F5` directly, evaluates `F7` against the approved skip rubric, and records a brief correction summary so the reasoning behind the edits remains durable.

**Tech Stack:** Markdown, `rg`, PowerShell, git

---

## File Structure

- `docs/prds/memory/F1-hybrid-search.md`
  Responsibility: lexical-semantic retrieval PRD; narrow scope, correct integration wording, and make corpus/risk assumptions explicit.
- `docs/prds/memory/F4-knowledge-graph.md`
  Responsibility: graph-memory PRD; add lifecycle/pruning decisions, reranking decision, and competitor-derived safeguards without adding external graph infrastructure.
- `docs/prds/memory/F5-async-sleep-agents.md`
  Responsibility: background-orchestration PRD; absorb Letta-inspired orchestration patterns while preserving structured-task reliability.
- `docs/prds/memory/F7-intentional-forgetting.md`
  Responsibility: forgetting PRD; edit only if the approved rubric says the current doc is insufficient.
- `docs/prds/memory/competitor-audit-prd-corrections-summary-2026-03-19.md`
  Responsibility: brief audit-tied summary of what changed and whether `F7` was changed or intentionally skipped.
- `docs/prds/memory/competitor-audit-letta-mem0.md`
  Responsibility: reference source for shipped competitor behavior; do not edit unless a broken link or factual typo blocks the PRD pass.
- `docs/thesis/whitepaper.md`
  Responsibility: thesis constraint reference for product framing; use as a source, do not edit in this pass.
- `docs/architecture/memory/memory-system.md`
  Responsibility: architecture constraint reference for implementation-facing wording; use as a source, do not edit in this pass.

## Baseline Capture

Before editing any PRD, record the starting commit so the full editorial pass can be reviewed across multiple commits later.

Run:

```powershell
$base = git rev-parse HEAD
Set-Content -Path "$env:TEMP\memory-prd-correction-base.txt" -Value $base
Write-Output $base
```

Expected: a 40-character commit hash printed and persisted to `$env:TEMP\memory-prd-correction-base.txt` for reuse in Task 5, Step 3 across shell sessions.

## Task 1: Tighten F1 Hybrid Search PRD

**Files:**
- Modify: `docs/prds/memory/F1-hybrid-search.md`
- Reference: `docs/prds/memory/competitor-audit-letta-mem0.md`
- Reference: `docs/thesis/whitepaper.md`
- Reference: `docs/architecture/memory/memory-system.md`

- [ ] **Step 1: Review the current F1 framing against the audit and spec**

Run: `rg -n "single highest-impact|companion.py|BM25|RRF|< 100ms|< 5 MB|validated configuration|rollback" "docs/prds/memory/F1-hybrid-search.md"`

Expected: Locate the overclaim, integration wording, and certainty-heavy sections that the spec requires changing.

- [ ] **Step 2: Rewrite the title and overview to narrow the claim**

Edit `docs/prds/memory/F1-hybrid-search.md` so the top framing reads as a lexical-semantic retrieval upgrade rather than the total memory-system solution.

Use wording like:

```md
# PRD: F1 - Lexical-Semantic Hybrid Retrieval (BM25 + Vector + RRF)

This feature upgrades the candidate-generation layer of memory retrieval by replacing the current Jaccard keyword leg with BM25 while preserving vector search and RRF fusion. It improves exact-term and proper-noun recall without claiming to solve graph reasoning, pattern separation, or the full retrieval stack.
```

- [ ] **Step 3: Correct the integration-path section**

Replace any wording that says `hybrid_search()` is called from prompt assembly in `companion.py` with wording that points to the real entry points.

Use wording like:

```md
- Retrieval entry points: `service.py::_prepare_turn_context()` for per-turn memory recall and `tools.py::recall_memory()` for explicit memory search.
- `companion.py` caches static blocks; it is not the per-turn caller of `hybrid_search()`.
```

- [ ] **Step 4: Add the corpus-coverage assumption and risk**

Add a design note and a risk entry explaining that if BM25 is built from vector-backed memory text, newly written or unembedded memories may not be searchable by the BM25 leg until sync/backfill happens.

Use wording like:

```md
Assumption: the BM25 corpus is built from the currently search-indexed memory text, not necessarily every logical memory row at write time.

Risk: fresh or unembedded memories may be temporarily invisible to the BM25 leg until embedding sync or index rebuild completes.
```

- [ ] **Step 5: Preserve the self-hosted differentiation without overclaiming**

Keep the competitive claim that AnimaOS delivers self-hosted BM25 + vector + RRF without external search infrastructure, but phrase it as an infrastructure and retrieval-quality advantage rather than a total-system advantage.

Use wording like:

```md
This design keeps hybrid lexical-semantic retrieval self-hosted and local to the Core, rather than depending on external search infrastructure for BM25 fusion quality.
```

- [ ] **Step 6: Downgrade unverified performance and certainty claims**

Convert hard claims such as `<100ms`, `<5 MB`, “Nemori's validated configuration”, and “no rollback risk” into targets, defaults, or hypotheses.

Use wording like:

```md
- Target build time: under 100 ms for small personal-memory corpora; verify by benchmark.
- `_RRF_K = 60` is retained as the current standard RRF constant and existing implementation default.
```

- [ ] **Step 7: Verify the edited F1 document**

Run: `rg -n "Lexical-Semantic|candidate-generation|companion.py|corpus|unembedded|self-hosted|external search infrastructure|target build time|_RRF_K = 60" "docs/prds/memory/F1-hybrid-search.md"`

Expected: The revised file contains the narrowed framing, corrected integration wording, explicit corpus assumption, preserved self-hosted differentiation, and softened certainty language.

- [ ] **Step 8: Commit the F1 changes**

```bash
git add docs/prds/memory/F1-hybrid-search.md
git commit -m "docs: tighten F1 hybrid retrieval framing"
```

## Task 2: Strengthen F4 Knowledge Graph PRD

**Files:**
- Modify: `docs/prds/memory/F4-knowledge-graph.md`
- Reference: `docs/prds/memory/competitor-audit-letta-mem0.md`
- Reference: `docs/thesis/whitepaper.md`
- Reference: `docs/architecture/memory/memory-system.md`

- [ ] **Step 1: Review the current F4 lifecycle and retrieval sections**

Run: `rg -n "rerank|prun|delete|Neo4j|SQLite-backed|ingest|relations" "docs/prds/memory/F4-knowledge-graph.md"`

Expected: Confirm whether graph-result reranking, relation pruning, and SQLite/Core constraints are already explicit enough.

- [ ] **Step 2: Add a graph-lifecycle section with pruning strategy**

Insert a section that explains how stale or contradicted relations are handled and where pruning happens.

Use wording like:

```md
### Graph Lifecycle

The graph is not append-only by default. The revised design must specify whether stale-relation pruning happens during ingestion, during sleep-time maintenance, or in a bounded maintenance pass. The goal is to prevent monotonic accumulation of outdated relations without introducing external graph infrastructure.
```

- [ ] **Step 3: Force a decision on graph-result reranking**

Revise the design and requirements so the PRD either adopts lightweight reranking for small traversal result sets or explicitly rejects it with rationale.

Use wording like:

```md
The PRD must take a position on graph-result reranking. If adopted, constrain it to small result sets and in-process ranking only. If rejected, state why graph traversal output is sufficient without an added reranking layer.
```

- [ ] **Step 4: Preserve SQLite/Core constraints and reject competitor baggage explicitly**

Make sure the non-goals or design constraints clearly reject Neo4j, Kuzu, Memgraph, and similar external graph backends because they violate portability and encrypted-Core goals.

Use wording like:

```md
The graph remains SQLite-backed inside the Core. Competitor use of external graph backends informs lifecycle patterns, not storage architecture.
```

- [ ] **Step 5: Add competitor-derived safeguards only if they fit AnimaOS**

Add any missing mention of relation pruning and ID-indirection safeguards if they remain compatible with the current direction. Do not import external graph platform abstractions.

- [ ] **Step 6: Verify the edited F4 document**

Run: `rg -n "Graph Lifecycle|pruning|reranking|SQLite-backed inside the Core|Neo4j|Kuzu|Memgraph|ID" "docs/prds/memory/F4-knowledge-graph.md"`

Expected: The file now contains an explicit lifecycle section, a clear reranking decision, and visible architecture constraints.

- [ ] **Step 7: Commit the F4 changes**

```bash
git add docs/prds/memory/F4-knowledge-graph.md
git commit -m "docs: strengthen F4 graph lifecycle and constraints"
```

## Task 3: Clarify F5 Async Sleep Agents PRD

**Files:**
- Modify: `docs/prds/memory/F5-async-sleep-agents.md`
- Reference: `docs/prds/memory/competitor-audit-letta-mem0.md`
- Reference: `docs/architecture/memory/memory-system.md`

- [ ] **Step 1: Review the current orchestration framing**

Run: `rg -n "Letta|frequency|transcript|structured|agent|background|observability|task tracking" "docs/prds/memory/F5-async-sleep-agents.md"`

Expected: Locate where the PRD references Letta and whether it clearly distinguishes structured tasks from autonomous background agents.

- [ ] **Step 2: Tighten the overview and problem framing**

Revise the overview so it says F5 adopts orchestration patterns from Letta without adopting Letta's open-ended background-agent model, and explicitly preserve last-processed tracking as a required part of restart safety.

Use wording like:

```md
F5 adopts proven orchestration patterns such as frequency gating, explicit last-processed tracking for restart safety, and task-run observability, while preserving structured background tasks instead of free-form background LLM agents.
```

- [ ] **Step 3: Add transcript-context guidance**

Add a design note describing which tasks should receive transcript-wide context and which should continue operating on narrower inputs.

Use wording like:

```md
Transcript-wide context is appropriate for synthesis-oriented tasks such as profile updates or graph maintenance when message-local inputs are insufficient. Extraction and bookkeeping tasks should remain narrow and deterministic when possible.
```

- [ ] **Step 4: Make the reliability tradeoff explicit**

Add a short comparison note that AnimaOS intentionally chooses structured, auditable background tasks over general-purpose background agents.

Use wording like:

```md
This design is less flexible than Letta's general sleeptime-agent model, but more predictable and easier to audit in a personal AI with long-lived memory.
```

- [ ] **Step 5: Verify the edited F5 document**

Run: `rg -n "structured background tasks|transcript-wide context|predictable|auditable|Letta|last-processed" "docs/prds/memory/F5-async-sleep-agents.md"`

Expected: The revised document clearly states the orchestration borrowings, transcript guidance, last-processed tracking, and reliability tradeoff.

- [ ] **Step 6: Commit the F5 changes**

```bash
git add docs/prds/memory/F5-async-sleep-agents.md
git commit -m "docs: clarify F5 sleeptime orchestration model"
```

## Task 4: Evaluate F7 Against the Skip Rubric

**Files:**
- Modify if needed: `docs/prds/memory/F7-intentional-forgetting.md`
- Reference: `docs/prds/memory/competitor-audit-letta-mem0.md`
- Reference: `docs/superpowers/specs/2026-03-19-memory-prd-competitor-corrections-design.md`

- [ ] **Step 1: Apply the approved F7 rubric before editing anything**

Run: `rg -n "delete|forget|passive decay|derived-reference cleanup|differenti" "docs/prds/memory/F7-intentional-forgetting.md"`

Expected: Enough evidence to decide whether the document actually blurs deletion vs forgetting, misses a boundary, or overstates differentiation.

- [ ] **Step 2: Choose one branch and record it**

Branch A, edit `F7` if at least one rubric condition is met.

Use targeted edits only:

```md
- distinguish contradiction-driven deletion from forgetting,
- distinguish passive decay from hard deletion,
- tighten competitor comparisons so they are specific and defensible.
```

Branch B, skip `F7` if the current PRD already states those boundaries clearly.

In that case, do not edit `F7`; record the skip decision in the summary document.

- [ ] **Step 3: Verify the F7 decision**

If edited, run:
`rg -n "contradiction-driven deletion|passive decay|derived-reference cleanup|Mem0" "docs/prds/memory/F7-intentional-forgetting.md"`

Expected: The revised distinctions are explicit.

If skipped, verify there is no diff for `F7`:
`git diff -- docs/prds/memory/F7-intentional-forgetting.md`

Expected: No output.

- [ ] **Step 4: Commit only if F7 changed**

```bash
git add docs/prds/memory/F7-intentional-forgetting.md
git commit -m "docs: tighten F7 forgetting distinctions"
```

Skip this commit if `F7` was not edited.

## Task 5: Write the Audit-Tied Summary and Run Final Verification

**Files:**
- Create: `docs/prds/memory/competitor-audit-prd-corrections-summary-2026-03-19.md`
- Reference: `docs/prds/memory/competitor-audit-letta-mem0.md`
- Reference: `docs/prds/memory/F1-hybrid-search.md`
- Reference: `docs/prds/memory/F4-knowledge-graph.md`
- Reference: `docs/prds/memory/F5-async-sleep-agents.md`
- Reference: `docs/prds/memory/F7-intentional-forgetting.md`

- [ ] **Step 1: Create the summary document**

Create `docs/prds/memory/competitor-audit-prd-corrections-summary-2026-03-19.md` with these sections:

```md
# Competitor Audit PRD Corrections Summary

## Scope
- F1
- F4
- F5
- F7 (changed or skipped)

## What Changed
- `F1`: change summary + audit finding addressed
- `F4`: change summary + audit finding addressed
- `F5`: change summary + audit finding addressed
- `F7`: change summary or skip reason + audit finding addressed if changed

## What We Explicitly Rejected
- External graph backends
- Turbopuffer-style dependency assumptions
- Open-ended background LLM agents as the default model

## F7 Decision
- Changed | Skipped
- One-paragraph reason
```

- [ ] **Step 2: Verify all touched docs align with the spec**

Run: `git diff --check`

Expected: No whitespace or merge-marker errors.

Run: `rg -n "single highest-impact|no rollback risk|validated configuration" "docs/prds/memory/F1-hybrid-search.md" "docs/prds/memory/F4-knowledge-graph.md" "docs/prds/memory/F5-async-sleep-agents.md" "docs/prds/memory/F7-intentional-forgetting.md"`

Expected: No stale overclaim language remains in the revised docs.

- [ ] **Step 3: Review the final patch as one coherent editorial pass**

Run:

```powershell
$base = (Get-Content "$env:TEMP\memory-prd-correction-base.txt").Trim()
git diff --word-diff=plain $base -- docs/prds/memory/F1-hybrid-search.md docs/prds/memory/F4-knowledge-graph.md docs/prds/memory/F5-async-sleep-agents.md docs/prds/memory/F7-intentional-forgetting.md docs/prds/memory/competitor-audit-prd-corrections-summary-2026-03-19.md
```

Expected: The diff from the pre-edit baseline shows the full competitor-informed correction pass across all touched files, even if earlier tasks were committed individually.

- [ ] **Step 4: Commit the summary and final verification state**

```bash
git add docs/prds/memory/competitor-audit-prd-corrections-summary-2026-03-19.md docs/prds/memory/F1-hybrid-search.md docs/prds/memory/F4-knowledge-graph.md docs/prds/memory/F5-async-sleep-agents.md docs/prds/memory/F7-intentional-forgetting.md
git commit -m "docs: align memory PRDs with competitor audit"
```

If `F7` was skipped, omit it from the `git add` command.
