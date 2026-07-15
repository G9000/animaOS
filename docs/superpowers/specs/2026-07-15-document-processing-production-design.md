# Production Document Processing — Docling-First Design

**Date:** 2026-07-15
**Status:** Draft for review
**Supersedes:** the tiered-escalation parsing design from PDP-004 (escalation heuristics, pypdf fast tier as a durable artifact source)

## Problem

The PDP-001..009 pipeline has the right production architecture (tiered parse → structured chunks → contextual enrichment → hybrid retrieval → rerank → evals), but every quality stage is dormant in practice:

- The Docling quality tier is an optional extra that no install path (`bun dev`, `uv sync`, `python:sync`, README) ever installs, and uv's exact sync uninstalls it even when added manually.
- The pypdf fast tier is the weakest common parsing choice: scrambled multi-column reading order, destroyed tables, glued/split words, no OCR, no headings — so the structure-aware chunker degrades to flat chunks and the OKF wiki compiler distills from garbled spans.
- The auto-escalation gate only fires on sparse pages (≥50% of pages under 15 words), so garbled-but-present text never escalates even when Docling is installed.
- Dense retrieval silently dies when the embedding provider (historically assumed to be local Ollama) is unreachable; search degrades to BM25-only with no user-visible signal.
- Contextual blurbs and the reranker default off; the reranker extra is never installed.
- The retrieval eval ran once with stub embeddings (`token-hash-stub`), so none of this was visible in metrics.
- The end-to-end quality-tier validation from PDP-004 is still marked PENDING and has never run on any machine.

## Goals

1. **High-quality parsing for every document, always** — no quality forks that depend on install luck.
2. **Zero-setup baseline** in the packaged desktop app: documents are chattable and searchable out of the box, offline, with no external services.
3. **Provider-agnostic LLM usage**: users hook any model (cloud, local, VLM). Nothing in the pipeline assumes Ollama or any specific provider. LLM-dependent stages (enrichment, wiki compilation, full-doc chat) use whatever is configured.
4. **The OKF LLM wiki compiles only from best-quality artifacts** with precise span citations.
5. **No silent degradation**: every document records how it was parsed; missing capabilities are visible states, not quiet quality loss.
6. **Honest evals**: retrieval quality measured with real embeddings, on golden documents that include scanned and table-heavy PDFs.

## Non-Goals

- Packaging the Python server into the Tauri desktop build (separate epic; this design makes the pipeline ship-ready for it).
- VLM-based parsing (rendering pages to images and transcribing with the user's vision model). Deliberately deferred: with Docling always-on it is redundant for v1. Recorded as a future optional tier.
- DOCX/PPTX/XLSX parsing (Docling supports them; wiring non-PDF formats through this pipeline is follow-up work).
- Changes to personal memory (SQLCipher) boundaries. This design touches the runtime evidence/knowledge layers only.

## Architecture

One canonical parser, one preview path, bundled embeddings, threshold-based context strategy.

```
drop PDF
  │
  ├─► pdfium preview extract (<1s) ──► preview text
  │        │                             ├─► small doc: full text into chat context (immediately chattable)
  │        │                             └─► large doc: provisional chunks + index, marked quality=preview
  │
  └─► Docling parse (background, always) ──► structured markdown + page/bbox locators
           │
           ├─► structured chunks (section paths) ──► embeddings (bundled fastembed) + BM25
           ├─► source artifacts + citable spans (runtime_source_* tables)
           ├─► contextual blurbs (user's LLM, budget-capped) → index text only
           └─► OKF wiki compiler (user's LLM) — consumes Docling-quality spans only
```

### 1. Parsing: Docling is the only durable parser

- Every document is parsed by Docling (layout analysis + TableFormer + OCR, `do_ocr=True`), producing structured markdown with page locators. There are no quality tiers, no escalation heuristics, and no scoring gate for durable artifacts. The escalation machinery from PDP-004 (`should_escalate_extraction`, tier routing in `parsing.py`) is removed.
- This matches server-side production practice (Unstructured, LlamaParse run their full pipeline on everything). Tiering was a size optimization, not a quality strategy; the size problem is solved by download-on-demand instead.
- Docling failures (corrupt file, OCR produces nothing, timeout) leave the document in a visible `parse_failed` state with the preview artifact retained for chat; they never silently fall back to preview text as the durable artifact.

### 2. Parsing pack: download-on-demand (the Ollama pattern)

- Docling + PyTorch + models (~1.5–2.5 GB installed) cannot ship in the installer. A **parsing pack manager** downloads it once, at onboarding or on first document ingest, with progress reported through the existing workflow-status surface.
- Until the pack is present, documents stay in a visible `awaiting_parser` state: chattable via preview, searchable at preview quality, clearly labeled. Ingest never hard-fails for lack of the pack.
- The pack manager exposes: `status()` (absent / downloading(pct) / ready / error), `ensure()` (idempotent download trigger), and a server API + desktop UI hook. Downloads resume on interruption.
- Model weights are pinned by version + checksum so parses are reproducible and the pack is upgradable deliberately, not accidentally.
- In dev, the pack is installed eagerly: dev scripts request the full dependency set (see §9).

### 3. pdfium preview path (replaces pypdf entirely)

- `pypdfium2` (few MB, base dependency) extracts text per page in under a second for instant use. pypdf is removed.
- Preview text is used for: (a) immediate full-doc chat context, (b) provisional chunking + indexing of large docs so retrieval works while Docling runs.
- Preview artifacts are temporary: when Docling completes, chunks are re-cut from Docling markdown, re-embedded, spans re-anchored, and the preview artifact is deleted. Every chunk/artifact row carries `parse_quality: preview | docling | legacy` (`legacy` marks pre-upgrade pypdf artifacts, see Migration) while it exists; nothing marked `preview` ever feeds the wiki compiler.

### 4. Embeddings: bundled ONNX default, provider-agnostic upgrades

- `fastembed` (ONNX Runtime) with a small multilingual embedding model (~100 MB, e.g. bge-small class) becomes the **default embedding provider**, shipped/downloaded with the app baseline so dense retrieval always works — offline, regardless of which LLM the user hooks up.
- The existing provider chain remains for upgrades: if the user configures a provider with an embeddings API (OpenAI, etc.) it can replace the bundled model, with the existing content-hash re-embed machinery handling the switch.
- The failure mode "embedding provider unreachable → silent BM25-only" is eliminated for the default path and made *visible* for user-configured providers (see §7).

### 5. Context strategy: full-doc stuffing with a threshold

- If a document's best-available extracted text fits a context budget, chat injects the whole document instead of retrieved chunks. This matches ChatGPT/Claude file-upload behavior and directly closes the "pasting the doc into my own LLM chat works better" gap.
- The budget scales with the configured model's context window (a Gemini-class model gets far more than an 8k local model): `min(model_context_tokens × utilization_factor, hard_cap)`, with the utilization factor and cap as settings. `model_context_tokens` must be the **effective runtime context, not the model's advertised maximum** — local runtimes often run far below the ceiling (Ollama defaults every model to 2048 and silently clips overflow), so the provider layer reports the configured value and the budget respects it; never stuff more than the runtime will actually accept. Multi-document questions and over-budget docs use retrieval.
- After Docling completes, full-doc mode uses the Docling markdown (tables survive); before that, preview text.

### 6. Retrieval defaults: everything on

- Hybrid dense + BM25 with RRF fusion stays as built.
- **Contextual blurbs default on** (Anthropic-style contextual retrieval, already implemented): generated with the user's configured LLM, budget-capped as today, skipped (visibly) when no LLM is configured.
- **Reranker**: replace the sentence-transformers/PyTorch extra with a quantized ONNX cross-encoder (bge-reranker class) running on the same ONNX Runtime as fastembed. If the quantized model is ≤~150 MB it joins the baseline download; otherwise it joins the parsing pack. Default on when present.
- BM25 per-query index rebuild over all chunks is a known scaling cost; acceptable now, noted for follow-up (persistent lexical index) rather than solved in this design.

### 7. Degraded-mode visibility

Documents and the pipeline expose explicit states instead of silent quality loss:

- Document lifecycle: `uploaded → preview_ready → parsing → indexed → compiled`, with `awaiting_parser` and `parse_failed` as visible variants; each document records the parser version that produced its artifacts.
- A capability status endpoint reports: parsing pack (absent/downloading/ready), embedding provider in use (bundled/custom/unreachable), reranker (on/off), LLM provider configured (yes/no → blurbs and wiki compilation availability).
- The desktop UI shows a status chip per document and a one-line banner when a capability is missing ("Documents are searchable at preview quality — parsing pack downloading, 43%").

### 8. OKF wiki integration

- The compiler's input contract tightens: **only `parse_quality=docling` spans are eligible for concept compilation.** Documents pending the parser are excluded from sleep-agent auto-compile until parsed.
- Docling markdown's section paths and page locators flow into `runtime_source_spans` so concept citations stay precisely drill-down-able (heading path + page).
- When a document is re-parsed (pack installed later, Docling version upgrade), its spans are re-anchored and the sleep agent queues affected concepts for recompilation — early low-quality knowledge never stays frozen in the wiki. Recompile scheduling reuses the existing `runtime_knowledge_bundle_runs` queue.

### 9. Dev environment and dependency changes

- `pypdfium2` and `fastembed` become base dependencies. `pypdf` is removed after migration.
- `docling` remains an extra for lean CI paths, but every dev entry point installs it: `python:sync` becomes `uv sync --all-packages --all-extras`; the server dev target runs `uv run --all-extras …`. The sentence-transformers `reranker` extra is deleted in favor of the ONNX reranker.
- `apps/server/README.md` documents the real setup, including the parsing pack behavior.

### 10. Evals and validation

- The retrieval eval harness runs with **real embeddings by default** (bundled fastembed makes this free and deterministic — no API keys needed); the stub-vector mode remains only for unit-speed CI.
- A golden-document corpus is added: at least one born-digital simple PDF, one multi-column paper, one table-heavy report, one scanned PDF. End-to-end assertions: parse succeeds, section titles present, tables survive as units, known Q→chunk retrieval hits, wiki compile cites correct spans. This finally discharges the PENDING PDP-004 validation.
- CI matrix: base install (preview-path behavior, `awaiting_parser` states) and full install (Docling path, golden corpus e2e).

## Error handling

- **Docling crash/timeout per document** → `parse_failed`, preview retained, error surfaced; retry action available. Never blocks other documents (worker isolation as today via the workflow checkpoints).
- **Pack download failure** → resumable; documents stay `awaiting_parser`; UI banner persists.
- **Encrypted/password PDFs** → explicit user-facing error at upload (as today).
- **No LLM configured** → blurbs and wiki compilation visibly skipped/queued; parsing, chunking, embedding, retrieval, and full-doc chat all still work (they never require an LLM).
- **Embedding model file corrupt/missing** → capability endpoint reports it; retrieval degrades to BM25 *with the state visible*, and re-download is offered.

## Migration

- Existing indexed documents keep working (their chunks/embeddings remain valid). On first launch after upgrade they are marked `parse_quality=legacy` and queued for background re-parse through Docling once the pack is present; wiki concepts citing legacy spans are queued for recompile after re-parse.
- The `document_parser_tier` setting and escalation code are removed; `retrieval_reranker` gains the ONNX backend and defaults on; `contextual_chunks` defaults on.

## Testing strategy

- Unit: pack manager states, threshold math for full-doc mode, preview→docling swap (chunks re-cut, embeddings replaced, spans re-anchored, preview deleted), quality-gate on the wiki compiler input.
- Integration: ingest lifecycle across both install profiles; provider-agnostic enrichment (mock LLM); capability endpoint truthfulness.
- E2E (full install): golden corpus through parse→chunk→embed→retrieve→compile with real assertions.
- Eval: recall@k / nDCG on real embeddings tracked in CI; regression threshold fails the build.

## Decomposition sketch (for the implementation plan)

1. pdfium preview path + pypdf removal
2. Parsing pack manager + Docling-always parse + document states
3. Preview→Docling artifact swap + re-chunk/re-embed/re-anchor
4. Bundled fastembed default embedding provider
5. Full-doc context mode with model-scaled threshold
6. ONNX reranker + defaults-on for blurbs/reranker
7. Capability endpoint + UI status surfacing
8. OKF compiler quality gate + recompile-on-reparse
9. Dev scripts/README/dependency changes
10. Golden corpus + real-embedding evals + CI matrix
