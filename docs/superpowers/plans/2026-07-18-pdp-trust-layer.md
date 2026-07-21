# Production Document Processing — Plan 3: Trust Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pipeline's state visible and self-healing: capability status surfaced to the UI, sleep-agent auto-reparse when the parsing pack arrives, embedding-provider configuration exposed end-to-end, model warm-up off the chat path with retrying failure latches, golden-corpus evals against REAL models (discharging the epic's never-run validations), and dev-env/docs truth restored. Spec: `docs/superpowers/specs/2026-07-15-document-processing-production-design.md` §2, §7, §8, §10 + the recorded Plan-2 follow-ups in that branch's ledger.

**Architecture:** A `GET /api/capabilities` endpoint aggregates parsing-pack state, embedding backend health, reranker state, and LLM-configured status; the desktop settings page gains an Embeddings section and a capability strip; the api-client gains the missing document/capability methods. The sleep agent gets a `reparse_pending_documents` task consuming Plan 1's `list_reparse_candidates`. Both ONNX failure latches gain a retry TTL and a startup warm-up task loads them off the request path. A `golden_corpus` pytest marker drives real PDFs through the real Docling path.

**Tech Stack:** Python 3.12/FastAPI (server), TypeScript/React (apps/desktop + packages/api-client), fastembed/docling, pytest.

## Global Constraints

- Worktree: `/Users/julio/animaOS/.claude/worktrees/pdp-trust-layer`; server tests from `apps/server/`: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest <paths> -q`; include `tests/test_corefs_keyslots.py` alongside TestClient-based suites (known pre-existing order sensitivity — do not investigate).
- Known pre-existing failures (exist on main, leave alone): `test_dev_session_continuity::test_global_store_restores_snapshot_during_module_import`, `test_runtime_db::test_runtime_migration_repairs_missing_profile_candidates_after_bad_stamp`.
- Ruff: delta zero over the pre-existing baseline (`uv run --project . ruff check src tests`).
- Ordinary tests must NEVER download models (conftest raiser stubs exist for `fastembed_backend._create_model` and `reranker._create_model` — keep them working). ONLY the `golden_corpus`-marked tests (Task 5) may download models, and only when explicitly run.
- Never assume a specific LLM provider. "fastembed" must remain non-selectable as a chat provider.
- Desktop/TS changes: match existing component and api-client conventions; `bun run lint:desktop` clean (check the actual lint command in package.json).
- Commit after every green task on branch `feature/pdp-012-trust-layer`.

---

### Task 1: retrying failure latches + startup model warm-up

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/fastembed_backend.py` (lines 20-76: `_failed` latch)
- Modify: `apps/server/src/anima_server/services/documents/reranker.py` (lines 23-93: `_model_failed` latch; also fix the stale `_prefetch_models`-style docstring if present)
- Modify: `apps/server/src/anima_server/main.py` (`lifespan`, add warm-up task near the sweep tasks at lines ~182-184)
- Test: `apps/server/tests/test_fastembed_backend.py`, `apps/server/tests/test_contextual_rerank.py`, new `apps/server/tests/test_model_warmup.py`

**Interfaces:**
- Both latches become TTL-based: replace `_failed: bool` with `_failed_at: float | None`; guard becomes `if _failed_at is not None and time.monotonic() - _failed_at < _RETRY_TTL_SECONDS: return None` (retry after the TTL expires; a repeat failure re-stamps the time). `_RETRY_TTL_SECONDS = 300.0` in both modules with a comment: model loads are heavier than HTTP probes, so 10× the HTTP provider cooldown (30s); a laptop that starts offline recovers dense retrieval/reranking within 5 minutes of connectivity instead of requiring a restart. Update both module docstrings that claim to mirror "the same degradation contract the HTTP providers have" to state the actual TTL semantics.
- New `warm_up_retrieval_models() -> None` (put it in `fastembed_backend.py`; import the reranker lazily inside): synchronous; loads the fastembed embedding model (via the module's `_load_model` with the resolved default model name — reuse the same resolution `generate_embedding` uses) and, when `settings.retrieval_reranker == "local"`, the reranker model. Never raises. First run downloads ~210MB total (bge-small + ms-marco) to the fastembed cache dir — this is the intended bundled-default behavior.
- `main.py` lifespan: after `ensure_runtime_tables()`, add `warmup_task = asyncio.create_task(asyncio.to_thread(warm_up_retrieval_models))` alongside the periodic sweeps, cancelled on shutdown like the sweep tasks (mirror their pattern exactly). Do NOT call `ensure_parsing_pack()` here — the docling pack stays on-demand (multi-GB, UI-consented).
- Tests: TTL behavior (failure → None during TTL with no reload attempt; after monkeypatched `time.monotonic` jump → reload attempted again); warm-up calls both loaders when reranker on, only embeddings when off, never raises when loaders fail (monkeypatch `_create_model` seams — no downloads).

- [ ] Step 1: failing tests for TTL semantics in both modules (mirror existing latch tests; monkeypatch `time.monotonic` at module) + `test_model_warmup.py`
- [ ] Step 2: verify fail → implement latch TTL + warm-up + lifespan wiring → verify pass
- [ ] Step 3: covering run: `pytest tests/test_fastembed_backend.py tests/test_contextual_rerank.py tests/test_model_warmup.py tests/test_agent_llm.py -q`; ruff delta zero
- [ ] Step 4: commit `feat(retrieval): retrying model-load latches and startup warm-up`

---

### Task 2: sleep-agent auto-reparse task

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/sleep_agent.py` (always-run list at lines 330-358; new `_task_reparse_pending_documents` modeled on `_task_knowledge_autocompile` at line 917)
- Modify: `apps/server/src/anima_server/config.py` (new settings)
- Test: `apps/server/tests/` — find the sleep-agent test file (grep `run_sleeptime_agents`) and mirror its harness

**Interfaces:**
- New settings: `document_auto_reparse: Literal["off", "on"] = "on"`, `document_auto_reparse_budget: int = 2` (documents per sleep cycle — reparse runs Docling synchronously, so the budget keeps cycles bounded).
- New task `_task_reparse_pending_documents(user_id, *, runtime_db_factory=None, **_) -> str`, registered in the always-run list as `("document_reparse", _task_reparse_pending_documents, {})`:
  1. No-op (return a skip summary string, matching the other tasks' return style) when `settings.document_auto_reparse != "on"` or `not parsing_pack_ready()`.
  2. `candidates = list_reparse_candidates(runtime_db, user_id=user_id)[: settings.document_auto_reparse_budget]`.
  3. For each: `reparse_document(runtime_db, user_id=user_id, document_id=doc_id)` — statuses `upgraded`/`upgraded_unembedded` count as processed; `pack_not_ready`/`parse_degraded`/`parser_unavailable` abort the loop (pack state changed or parser is sick — retry next cycle).
  4. Return a summary string like the sibling tasks (`"reparsed 2 documents (1 pending)"`).
- Recompile-on-reparse already happens inside `reparse_document` via `sync_document_source(compile_knowledge=True)` — the task must NOT add a second compile pass; note this in a comment.
- Tests: task registered in the always-run list; skips when pack not ready (no reparse calls); processes at most budget candidates; aborts on `parser_unavailable`; summary strings. Monkeypatch `parsing_pack_ready`, `list_reparse_candidates`, `reparse_document` at the sleep_agent module import site (check how it imports them — match).

- [ ] Step 1: read the sleep-agent test harness; failing tests
- [ ] Step 2: verify fail → implement → verify pass
- [ ] Step 3: covering run: sleep-agent test file + `tests/test_document_reparse.py` + corefs workaround file; ruff delta zero
- [ ] Step 4: commit `feat(documents): sleep-agent auto-reparse when parsing pack ready`

---

### Task 3: capabilities endpoint + health check + api-client methods

**Files:**
- Create: `apps/server/src/anima_server/api/routes/capabilities.py` (router; register in main.py where other routers are included — find the include_router block)
- Modify: `apps/server/src/anima_server/services/health/registry.py` (register a `retrieval_capabilities` check; check fn in `services/health/checks.py` following its existing check style)
- Create: `apps/server/src/anima_server/services/capabilities.py` (the aggregation logic, so route and health check share it)
- Modify: `packages/api-client/src/client.ts` (+ `types.ts`): `documents.parsingPack()`, `documents.downloadParsingPack()`, `documents.reparse(documentId)`, `system.capabilities()`
- Test: `apps/server/tests/test_capabilities_api.py`; api-client: match how packages/api-client is tested (grep for its test setup; if none exists, typecheck via the package's build/lint command is the gate)

**Interfaces:**
- `services/capabilities.py::collect_capabilities() -> dict` returning:
  ```python
  {
    "parsingPack": {"state": ..., "progress": ..., "error": ...},          # pack_status()
    "embeddings": {"provider": ..., "model": ..., "dim": ...,              # resolved via the existing resolvers
                    "backend": "ready" | "cold" | "failed_retrying"},      # from fastembed_backend latch state (add a small
                                                                            # status fn there: model loaded / no attempt yet / in TTL window)
    "reranker": {"enabled": settings.retrieval_reranker == "local", "model": ..., "backend": ...},  # same latch-status pattern
    "llm": {"configured": bool},                                            # a chat provider+model resolve to something usable
                                                                            # (reuse resolve_background_chat_targets-style logic or the
                                                                            # simplest truthful signal — document the choice)
    "contextualChunks": settings.contextual_chunks == "on",
    "fullDocumentContext": settings.document_full_context == "auto",
  }
  ```
  Requires small read-only status helpers in `fastembed_backend.py`/`reranker.py` (e.g. `backend_status() -> str`) exposing the latch state added in Task 1 — read-only, no loads triggered.
- Route: `GET /api/capabilities` (auth: match the documents routes' session dependency), returns the dict verbatim (camelCase keys as above).
- Health check `retrieval_capabilities`: healthy unless embeddings backend is `failed_retrying` or parsing pack is `error` — mirror the existing checks' result shape in `services/health/checks.py`.
- api-client: follow client.ts's existing namespace/method conventions exactly (types in types.ts: `ParsingPackStatus`, `CapabilitiesResponse`, `ReparseResult`).

- [ ] Step 1: failing server tests (route payload shape with monkeypatched pack/backend states; health check registration)
- [ ] Step 2: verify fail → implement server side → verify pass
- [ ] Step 3: api-client methods + types; run the package's lint/typecheck gate
- [ ] Step 4: covering run incl. corefs workaround; ruff delta zero
- [ ] Step 5: commit `feat(api): capabilities endpoint, health check, client methods`

---

### Task 4: embedding-provider configuration end-to-end (+ resolver de-dup)

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/config.py` (AgentConfigResponse/UpdateRequest + get/update)
- Create: `apps/server/src/anima_server/services/agent/embedding_resolution.py` (shared leaf module ending the duplicated-resolver drift risk)
- Modify: `apps/server/src/anima_server/config.py` + `apps/server/src/anima_server/services/agent/embeddings.py` (both import the leaf module; delete the duplicated copies + KEEP IN SYNC comments)
- Modify: `packages/api-client/src/types.ts` + `client.ts` (AgentConfig fields)
- Modify: `apps/desktop/src/pages/settings/AiSettings.tsx` (Embeddings section)
- Test: `apps/server/tests/test_config_api.py` (or wherever config routes are tested — grep), `tests/test_agent_llm.py`

**Interfaces:**
- Leaf module `embedding_resolution.py`: move the resolution trio there — `resolve_embedding_provider()`, `resolve_embedding_model(provider)`, plus the piggyback-intent predicate — importable by BOTH `config.py` and `embeddings.py` without cycles (it may import `anima_server.config.settings` lazily inside functions if needed — verify the import graph; the whole point is one copy). All existing resolution tests keep passing against the new home (update imports, not assertions).
- Config API: `AgentConfigResponse` gains `embeddingProvider: str` (resolved effective provider), `embeddingModel: str` (resolved), `embeddingIsExplicit: bool` (user set something vs bundled default), `hasEmbeddingApiKey: bool`. `AgentConfigUpdateRequest` gains optional `embeddingProvider?`, `embeddingModel?`, `embeddingApiKey?` — empty string means "reset to bundled default" (clears all three settings). Validation: `embeddingProvider` must be in `SUPPORTED_PROVIDERS` (fastembed IS valid here — it's the embedding side) or `""`. On change: set settings, `persist_runtime_settings()`, `clear_embedding_cache()` (dim/model switches flow through the existing contract machinery — add a comment, build nothing).
- AiSettings.tsx: new "Embeddings" card below the provider section — default state shows "Built-in (recommended) — BAAI/bge-small-en-v1.5, runs on this device"; an "Advanced" toggle reveals provider dropdown (fastembed/ollama/openai/vllm/doubleword — NOT scaffold/anthropic/openrouter: check `_embedding_skip_reason` for the skip list and exclude those), model text field, API key field (masked, same pattern as the chat key). Save flows through the same `api.config.update` call. Match the page's existing styling/components exactly.
- Server tests: GET returns resolved embedding fields; PUT round-trips explicit provider; PUT with `embeddingProvider: ""` resets to default; invalid provider → 400; fastembed accepted here while still rejected as chat provider (regression-guard both directions).

- [ ] Step 1: failing server tests (config API) + resolution-module move (tests keep passing via import updates)
- [ ] Step 2: verify fail → implement server side → verify pass
- [ ] Step 3: api-client types + AiSettings UI; desktop lint/typecheck gate
- [ ] Step 4: covering run: config tests + test_agent_llm.py + embedding contract/sync suites + corefs workaround; ruff delta zero
- [ ] Step 5: commit `feat(config): embedding provider selection end-to-end; shared resolution module`

---

### Task 5: golden-corpus eval (REAL models — discharges the epic's pending validations)

**Files:**
- Create: `apps/server/tests/fixtures/golden_corpus/` — `generate_fixtures.py` (checked in, deterministic) + the generated PDFs (checked in as binaries) + `gold.json` (queries → expected chunks/sections)
- Create: `apps/server/tests/test_golden_corpus.py` (marker `golden_corpus`)
- Modify: `apps/server/pyproject.toml` (register marker, exclude from default addopts like `retrieval_eval`)
- Modify: `apps/server/src/anima_server/services/documents/parsing_pack.py` ONLY IF the real docling install shows `_prefetch_models`'s import is wrong (the epic's open item: `from docling.utils.model_downloader import download_models` was never verified)
- Test: the new file itself is the deliverable

**Interfaces:**
- `generate_fixtures.py` produces four PDFs deterministically (no network): `simple.pdf` (single column, headings — extend the `tests/pdf_fixtures.py` hand-crafted writer), `multicolumn.pdf` (two text columns per page at different x offsets), `tables.pdf` (grid of positioned text cells forming a 4×6 table with distinctive cell values), `scanned.pdf` (pages containing ONLY an embedded image of rendered text — generate the image with pypdfium2's rasterizer from a text PDF, then embed; zero text layer). Each has a `gold.json` entry: queries with expected content assertions.
- `test_golden_corpus.py` (marker `golden_corpus`, excluded by default):
  1. Skips with a clear message unless docling is installed (`importlib.util.find_spec`) — the run instructions in the module docstring: `uv sync --project . --extra docling && ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest -m golden_corpus -s`.
  2. First: verify the `_prefetch_models` import path against the real install (`from docling.utils.model_downloader import download_models`) — assert it imports; if it fails, FIX `parsing_pack.py` and record the correction.
  3. `ensure_parsing_pack()` + wait for ready (generous timeout; model download allowed here).
  4. Drive each PDF through the REAL `extract_document_text` → assert `parse_quality == "docling"`; simple/multicolumn: expected phrases present and, for multicolumn, in correct reading order (column 1's sentence before column 2's); tables: cell values present and adjacent cells from the same ROW appear closer than same-column values (reading-order sanity); scanned: OCR recovered the known phrases (allow fuzzy match ≥80% token overlap — OCR isn't exact).
  5. Chunk + embed with REAL fastembed embeddings (no stub) + rerank with the REAL reranker: gold queries hit their expected chunks in top-5 (this also validates bge-small + ms-marco end-to-end for the first time).
  6. Wiki-gate assertion: preview-quality ingest of the same PDF produces zero knowledge concepts; docling-quality allows compile (mock the LLM compile like existing ingestion tests do — the gate is what's under test, not the LLM).
- The implementer RUNS this for real in the worktree (docling extra install ≈2GB, model downloads; use background execution for the long parts) and reports actual results — this is the PDP-004 pending validation plus the model-stack validation, finally executed. If the environment genuinely cannot (disk/network), report BLOCKED with exact instructions rather than faking it.

- [ ] Step 1: fixture generator + PDFs + gold.json (verify generator determinism: two runs byte-identical)
- [ ] Step 2: the golden test, skip-guarded; verify it SKIPS cleanly without docling
- [ ] Step 3: install the extra in the worktree venv, run for real, fix what breaks (parsing_pack import, OCR assertions tuning — record every adjustment)
- [ ] Step 4: full default suite still green (golden marker excluded); ruff delta zero
- [ ] Step 5: commit `test(documents): golden-corpus eval against real docling + onnx models`

---

### Task 6: dev-env truth + docs truth

**Files:**
- Modify: `package.json:31` (`python:sync` → `uv sync --all-packages --all-extras`), `apps/server/project.json:11` (dev command → `uv run --all-extras --project . uvicorn …`)
- Modify: `apps/server/README.md` (setup commands with `--all-extras`; a short "Document processing" note: pack download-on-demand, bundled embeddings, capabilities endpoint)
- Modify: `docs/architecture/agent/document-processing.md` — rewrite the stale sections per the scout's findings: lines 151-179 (tier system → pdfium preview + Docling-always + pack manager + reparse), line 359 (scanned-PDF failure mode → DocumentAwaitingParserError semantics), lines 367/373/374/375 (settings table: delete `ANIMA_DOCUMENT_PARSER_TIER`, `contextual_chunks` default on, `retrieval_reranker` default local, reranker row → fastembed ONNX not sentence-transformers extra; add `document_full_context*`, `document_auto_reparse*` rows)
- Test: none (docs/scripts); gate is `bun run dev:server` still boots (smoke: server starts, /health 200) and one full default suite run

- [ ] Step 1: script + README changes; smoke-boot the server via the dev command (background, kill after /health responds)
- [ ] Step 2: docs rewrite (accuracy over volume — every stated default must match config.py TODAY; cite settings names exactly)
- [ ] Step 3: full default suite + ruff; commit `chore(dev): install extras by default; true up document-processing docs`

---

## Ledger notes for the final review

Carried items closed by this plan: latch TTL (T1), preload off turn path (T1), sleep-agent reparse + recompile queueing (T2), embedding-provider UI (T4), resolver de-dup (T4), docling model_downloader verification + PDP-004 pending validation + first real model-stack run (T5), dev scripts/README/docs (T6). Remaining open after Plan 3: bundling models into the installer vs first-run download (packaging epic territory); BM25 per-query index rebuild (perf, noted in spec §6).
