# PDP-009 - Retrieval Eval Harness, Docs, and Final Validation

- Status: todo
- Priority: P1
- Scope: `apps/server`, `docs/architecture`
- Parent: `PDP-000`
- Depends on: `PDP-001`, `PDP-002`, `PDP-003`, `PDP-004`, `PDP-005`, `PDP-006`, `PDP-007`, `PDP-008`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-10

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
