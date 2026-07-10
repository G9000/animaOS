# PDP-007 - LLM-Wiki Compiler Wiring and Sleep-Agent Auto-Compile

- Status: todo
- Priority: P1
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: `PDP-002`, `PDP-003`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-10

## Goal

Turn the OKF layer from built-but-idle into live. Wire a real model into the existing `compile_source_to_concepts` contract (`services/ingestion/compiler.py`) and auto-trigger compilation from the sleep agent, so ingested sources actually become maintained, cross-linked, citable concept pages.

## Design

- **Model wiring.** Replace the deterministic payload lambda in `compile_source_knowledge` (`services/ingestion/document_compiler.py`) with the runtime's configured model behind the compiler's existing `model` callable contract. Strict-JSON output already enforced by the compiler; malformed output already records a failed bundle run — keep both. The deterministic builder remains the explicit no-LLM fallback (settings flag `KNOWLEDGE_COMPILER=llm|deterministic`).
- **Prompt.** Compile prompt receives: source metadata, section-pathed spans (PDP-003), and existing concepts that hybrid-match the source content (PDP-002) so the model updates/merges rather than duplicates — the deterministic merge rules stay as the final arbiter. Cross-source synthesis and `supports`/`contradicts`/`updates` links are explicit prompt objectives. Every emitted concept must cite span ids; uncited claims are dropped before persist (lint already flags them — enforce at write time too).
- **Triggers.** Sleep-agent maintenance task: find sources with spans but no compiled concepts (the existing orphan lint rule is the query), respect a per-cycle budget (e.g. 2 sources/cycle) and a cooldown per source. Policy setting `KNOWLEDGE_AUTOCOMPILE=off|markdown_only|all` — default `markdown_only` initially (cheap, high-signal), `all` once evals look good. Explicit compile endpoint and ingest-time `compile: true` keep working and use the LLM path.
- **Modes.** `initial` for uncompiled sources; `refresh` when span content hashes changed (stale lint rule); `repair` reserved for lint-driven fixes (follow-up).

## Acceptance

- Compiling a real markdown source with the LLM path produces concepts with citations, tags, and links that pass lint; re-compiling is idempotent (no duplicates, hash-stable where content unchanged).
- Two sources covering the same topic produce one merged concept with citations from both (fixture pair + scripted model test for determinism; one manual real-model run recorded).
- Sleep agent compiles orphan sources within budget and records bundle runs; `off` policy verified inert.
- Malformed model output leaves existing concepts untouched (existing failure-path tests extended to the wired model).
- Deterministic fallback still passes `test_llm_wiki_compiler.py` and `test_okf_import_export.py` unchanged.

## Validation

- Commands: server test suite (compiler + sleep agent + lint subsets), ruff, manual compile of a small corpus with lint report attached to the ticket.
- Changed paths: `apps/server/src/anima_server/services/ingestion/document_compiler.py`, `apps/server/src/anima_server/services/agent/sleep_agent.py`, settings, tests.
