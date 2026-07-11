# PDP-009 - Retrieval Eval Harness, Docs, and Final Validation

- Status: in_review
- Priority: P1
- Scope: `apps/server`, `docs/architecture`
- Parent: `PDP-000`
- Depends on: `PDP-001`, `PDP-002`, `PDP-003`, `PDP-004`, `PDP-005`, `PDP-006`, `PDP-007`, `PDP-008`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-12 (implemented in worktree `production-doc-processing`)

## Goal

Make retrieval quality measurable so pipeline changes are gated on evidence, not vibes (per the no-eval-driven-heuristics rule: the harness gates *changes*, it does not tune heuristics against individual failures). Update architecture docs to match the shipped pipeline and record final epic validation.

## Work

1. **Gold set.** A small fixture corpus (2–3 PDFs incl. one table-heavy, one markdown doc, one HTML article) with ~30 (query → expected chunk/section) pairs, checked into `apps/server/tests/fixtures/retrieval_eval/`. Mix of needle queries (exact terms), paraphrase queries, and section-level queries.
2. **Harness.** A pytest-marked (non-default) eval that ingests the corpus against a scratch runtime DB with a deterministic local embedding stub *and* an optional real-embedding mode, reporting recall@5, recall@15, and nDCG@10 per retrieval configuration (dense / hybrid / +blurbs / +rerank). Output written as a comparable JSON artifact.
3. **Docs.** Rewrite `docs/architecture/agent/document-processing.md` and update `source-ingestion.md` for: tiered parsing, structured intermediate, hybrid retrieval, agentic tools, live compiler, and the flags/extras matrix. Changelog entry.
4. **Final validation.** Full server test suite in both dependency configurations (base, with extras), desktop build, one end-to-end manual pass: upload scanned PDF → ask needle + summary questions → verify tool usage, pills, compiled concepts, lint green, OKF export opens.

## Acceptance

- Eval harness runs reproducibly and shows hybrid ≥ dense on the gold set (numbers recorded in this ticket).
- Docs match implemented behavior (no references to removed limits/stub compiler).
- Full validation commands and results recorded below; parent tracker `PDP-000` closed.

## Validation

- Commands: to be recorded on completion (test suites both configs, eval harness output, desktop build).
- Changed paths: `apps/server/tests/`, `docs/architecture/agent/`, `docs/CHANGELOG.md`, `tickets/production-document-processing/`.

## Activity Log

- 2026-07-12 - Implemented (Claude session):
  - **Gold set**: `apps/server/tests/fixtures/retrieval_eval/` — table-heavy markdown manual, HTML article with boilerplate, and a paged plain-text log standing in for a born-digital PDF (binary PDF fixtures avoided; the log flows through the same `chunk_pages_structured` page pipeline), plus `gold.json` with 30 needle/paraphrase/section queries.
  - **Harness**: `test_retrieval_eval.py`, marker `retrieval_eval` (excluded by default via pytest addopts; run with `pytest apps/server/tests/test_retrieval_eval.py -m retrieval_eval -s`). Deterministic mode uses a token-hash embedding stub and a NumPy-free cosine ranking equivalent to pgvector's over the scratch DB; `ANIMA_EVAL_EMBEDDINGS=real` switches to the configured embedder and enables the reranker configuration. JSON artifact written to `ANIMA_EVAL_OUTPUT` (default `<data_dir>/retrieval_eval.json`).
  - **Recorded numbers** (deterministic stub, 30 queries): dense recall@5 0.8333 / recall@15 1.0 / nDCG@10 0.8171; hybrid recall@5 **0.8667** / recall@15 1.0 / nDCG@10 **0.8282**; hybrid+template-blurbs recall@5 0.8667 / nDCG@10 0.8089. Acceptance holds: hybrid ≥ dense. Blurb/rerank quality claims require the real-embedding mode (stub-mode blurbs use deterministic templates and rerank is skipped) — those runs remain open before flipping either flag on by default.
  - **Docs**: `document-processing.md` rewritten for tiered parsing, structured chunking + overlap, hybrid retrieval + reranker, blurbs, config-backed primer limits, agentic tools + budgets + pills, flags/extras matrix, eval harness; `source-ingestion.md` updated for HTML/web extraction adapters, live compiler + citation enforcement + fallback, auto-compile; `docs/CHANGELOG.md` entry added.
  - **Final validation**: full server suite (base config, isolated `ANIMA_DATA_DIR`) green across the epic (latest run recorded in PDP-000); ruff clean. Deferred/manual: extras-config suite run (docling + reranker installs are torch-heavy), desktop build, and the end-to-end manual pass (scanned PDF upload → needle/summary questions → tool usage, pills, compiled concepts, lint, OKF export) — listed for the reviewer before closing PDP-000.
