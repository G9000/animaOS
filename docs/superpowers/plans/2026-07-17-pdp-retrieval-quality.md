# Production Document Processing — Plan 2: Retrieval Quality

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dense retrieval that always works (bundled fastembed/ONNX embeddings as the default provider), full-document context stuffing for small documents, and an ONNX reranker replacing the never-installed sentence-transformers extra — per spec `docs/superpowers/specs/2026-07-15-document-processing-production-design.md` §4–§6.

**Architecture:** A new in-process `fastembed` embedding provider joins the dispatch in `embeddings.py` (third branch — not HTTP) and becomes the fallback default so retrieval no longer dies with the user's LLM choice. `_build_document_context_block` gains a full-doc path: when the selected documents' total text fits a budget derived from the existing `agent_context_window_tokens` machinery, whole documents are injected instead of retrieved chunks. `reranker.py` swaps `sentence_transformers.CrossEncoder` for `fastembed.rerank.TextCrossEncoder` (ONNX, no torch) and defaults on, as do contextual blurbs.

**Tech Stack:** Python 3.12, fastembed (ONNX Runtime — already a base transitive dep via chromadb), FastAPI, SQLAlchemy, pytest.

## Global Constraints

- Worktree: `/Users/julio/animaOS/.claude/worktrees/pdp-retrieval-quality`; run test commands from `apps/server/`: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest <paths> -q`. `uv sync`/`uv lock` from the worktree root.
- Ruff baseline: 7 pre-existing errors on main — introduce ZERO new (`uv run --project . ruff check src tests`).
- Known pre-existing test-order sensitivity: include `tests/test_corefs_keyslots.py` in any pytest invocation that runs `tests/test_documents_api.py` or other TestClient-based files standalone; do not investigate it.
- Never assume Ollama or any specific LLM provider. The bundled embedding provider must work offline and never depend on the chat provider.
- Tests must NEVER download real ONNX models. Every test monkeypatches the model factory; real-model behavior is covered later by Plan 3's golden-corpus evals.
- Bundled embedding model: `BAAI/bge-small-en-v1.5` (384 dims). Bundled reranker model: `Xenova/ms-marco-MiniLM-L-6-v2`. Model files cache under `settings.data_dir / "models" / "fastembed"`.
- Embedding-dimension switches (768→384 for users who previously embedded via Ollama) are handled by the EXISTING contract machinery (`embedding_contract.check_embedding_contract`, `_reconcile_embedding_dimension`) — do not build new migration logic; do add a test that the dim resolves correctly for the new model.
- Commit after every green task on branch `feature/pdp-011-retrieval-quality`.

---

### Task 1: fastembed provider — in-process embedding backend

**Files:**
- Modify: `apps/server/pyproject.toml` (add `fastembed>=0.4` to `[project] dependencies`; `uv lock` must keep `onnxruntime==1.27.0`-compatible resolution)
- Create: `apps/server/src/anima_server/services/agent/fastembed_backend.py`
- Modify: `apps/server/src/anima_server/services/agent/embeddings.py`
- Modify: `apps/server/src/anima_server/config.py` (KNOWN_EMBEDDING_DIMS + its `_DEFAULT_EMBEDDING_MODELS` copy)
- Modify: `apps/server/src/anima_server/services/agent/llm.py` (SUPPORTED_PROVIDERS — decide per Step 3)
- Test: `apps/server/tests/test_fastembed_backend.py`

**Interfaces:**
- Produces: `fastembed_backend.embed_texts(texts: list[str], *, model_name: str) -> list[list[float] | None]` — synchronous, in-process; loads the ONNX model once per process (double-checked lock, mirroring `reranker.py`'s `_load_model` pattern) with `cache_dir=settings.data_dir / "models" / "fastembed"`; on any load/inference failure logs a warning once, sets a failed flag, and returns all-`None` (callers already treat `None` as "no dense arm" — same degradation contract as provider outages). Also `_reset_backend_for_tests()`.
- Produces: provider name `"fastembed"` usable in `generate_embedding` / `generate_embeddings_batch` — dispatched to the in-process backend via `asyncio.to_thread` (the entry points are async; ONNX inference is sync CPU-bound). No base URL, no API key, no HTTP cooldown (`_provider_failure_key`/cooldown paths are skipped for it exactly like the backend's own failed-flag handles repeated failure cheaply).
- Consumes: `settings.data_dir`; the existing `_note_detected_embedding_dim` call flow (a successful fastembed embed must still flow through it so the contract check runs).

- [ ] **Step 1: Write the failing backend tests**

```python
# tests/test_fastembed_backend.py
from __future__ import annotations

import pytest
from anima_server.services.agent import fastembed_backend


@pytest.fixture(autouse=True)
def reset_backend():
    fastembed_backend._reset_backend_for_tests()
    yield
    fastembed_backend._reset_backend_for_tests()


class _FakeModel:
    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        for index, _text in enumerate(texts):
            yield [float(index + 1)] * self.dim


def test_embed_texts_returns_vectors(monkeypatch) -> None:
    fake = _FakeModel()
    monkeypatch.setattr(fastembed_backend, "_create_model", lambda model_name: fake)

    vectors = fastembed_backend.embed_texts(["alpha", "beta"], model_name="test-model")

    assert vectors == [[1.0] * 4, [2.0] * 4]
    assert fake.calls == [["alpha", "beta"]]


def test_model_loads_once_and_is_reused(monkeypatch) -> None:
    created: list[str] = []

    def factory(model_name: str):
        created.append(model_name)
        return _FakeModel()

    monkeypatch.setattr(fastembed_backend, "_create_model", factory)

    fastembed_backend.embed_texts(["a"], model_name="test-model")
    fastembed_backend.embed_texts(["b"], model_name="test-model")

    assert created == ["test-model"]


def test_load_failure_degrades_to_none_and_fast_fails(monkeypatch) -> None:
    calls: list[str] = []

    def factory(model_name: str):
        calls.append(model_name)
        raise RuntimeError("onnx model missing")

    monkeypatch.setattr(fastembed_backend, "_create_model", factory)

    first = fastembed_backend.embed_texts(["a"], model_name="test-model")
    second = fastembed_backend.embed_texts(["b"], model_name="test-model")

    assert first == [None]
    assert second == [None]
    assert calls == ["test-model"]  # failed flag prevents reload storm
```

- [ ] **Step 2: Run to verify failure**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_fastembed_backend.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the backend and wire the provider**

Add `"fastembed>=0.4",` to `[project] dependencies` in `apps/server/pyproject.toml`; run `uv lock` from the worktree root and confirm it resolves against the locked `onnxruntime`/`tokenizers` (report the resolved fastembed version).

```python
# src/anima_server/services/agent/fastembed_backend.py
"""Bundled in-process embedding backend (fastembed / ONNX Runtime).

This is the provider that makes dense retrieval work with zero setup and no
external services: models are ONNX, run on CPU, and cache under the app data
dir. Failure never raises to callers — a load or inference error logs once,
latches a failed flag, and yields ``None`` vectors, the same degradation
contract the HTTP providers have during an outage.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from anima_server.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_model: Any | None = None
_model_name_loaded: str | None = None
_failed = False


def _create_model(model_name: str) -> Any:
    from fastembed import TextEmbedding

    cache_dir = settings.data_dir / "models" / "fastembed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))


def embed_texts(texts: list[str], *, model_name: str) -> list[list[float] | None]:
    model = _load_model(model_name)
    if model is None:
        return [None] * len(texts)
    try:
        return [list(map(float, vector)) for vector in model.embed(texts)]
    except Exception:
        logger.warning("fastembed inference failed; degrading to no dense arm", exc_info=True)
        return [None] * len(texts)


def _load_model(model_name: str) -> Any | None:
    global _model, _model_name_loaded, _failed
    if _model is not None and _model_name_loaded == model_name:
        return _model
    if _failed:
        return None
    with _lock:
        if _model is not None and _model_name_loaded == model_name:
            return _model
        if _failed:
            return None
        try:
            _model = _create_model(model_name)
            _model_name_loaded = model_name
        except Exception:
            _failed = True
            logger.warning(
                "Failed to load fastembed model %r; dense retrieval will be "
                "unavailable until the model can be loaded.",
                model_name,
                exc_info=True,
            )
            return None
    return _model


def _reset_backend_for_tests() -> None:
    global _model, _model_name_loaded, _failed
    with _lock:
        _model = None
        _model_name_loaded = None
        _failed = False


__all__ = ["embed_texts"]
```

Wire the provider name (scout-verified integration sites):

1. `llm.py` `SUPPORTED_PROVIDERS` (L21-28): add `"fastembed"`. It is a real, selectable provider (unlike `"scaffold"`), so it belongs in the master tuple; check every consumer of `SUPPORTED_PROVIDERS` (grep) and confirm none assumes providers are chat-capable — if any does (e.g. chat-model validation paths), special-case `"fastembed"` there per what you find and record it in the report.
2. `embeddings.py`:
   - `_DEFAULT_EMBEDDING_MODELS` (L55-61): `"fastembed": "BAAI/bge-small-en-v1.5"`.
   - `_resolve_embedding_base_url` (L124-137): return `""` for `"fastembed"` (no HTTP) — and confirm nothing downstream requires a non-empty URL for it (the dispatch happens before any URL use).
   - `_validate_embedding_provider_configuration` (L140-160): no API key required for fastembed.
   - `generate_embedding` (L382-387) and `generate_embeddings_batch` (L1360-1384): add the third dispatch arm BEFORE the ollama/openai-compatible split:
     ```python
     if provider == "fastembed":
         from anima_server.services.agent.fastembed_backend import embed_texts

         vectors = await asyncio.to_thread(embed_texts, [prepared_text], model_name=model)
         embedding = vectors[0]
     ```
     (batch: pass all prepared texts in one call). Successful vectors must still flow through `_note_detected_embedding_dim` exactly as the HTTP arms do. Skip the cooldown machinery for this provider (no `_mark_provider_unavailable` — the backend's failed flag serves that role).
3. `config.py`: `KNOWN_EMBEDDING_DIMS` gains `"BAAI/bge-small-en-v1.5": 384`; the duplicate `_DEFAULT_EMBEDDING_MODELS` table (L290-296) gains `"fastembed": "BAAI/bge-small-en-v1.5"`.

- [ ] **Step 4: Add provider-dispatch tests**

Append to `tests/test_agent_llm.py`, following its existing monkeypatch style (save/restore `settings.agent_embedding_provider`):

```python
def test_generate_embedding_dispatches_to_fastembed_backend(monkeypatch) -> None:
    from anima_server.services.agent import fastembed_backend

    monkeypatch.setattr(settings, "agent_embedding_provider", "fastembed")
    monkeypatch.setattr(settings, "agent_embedding_model", "BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(
        fastembed_backend, "embed_texts",
        lambda texts, *, model_name: [[0.5] * 384 for _ in texts],
    )

    result = asyncio.run(embeddings.generate_embedding("hello world"))

    assert result == [0.5] * 384


def test_resolve_embedding_dim_knows_bundled_model(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_embedding_provider", "fastembed")
    monkeypatch.setattr(settings, "agent_embedding_model", "BAAI/bge-small-en-v1.5")
    clear_detected_embedding_dim()

    assert resolve_embedding_dim() == 384
```

(Adapt imports/cache-clearing to the file's existing helpers — read neighbouring dim tests at L480-521 first. If module-level embedding caches interfere, use the file's established cache-reset pattern.)

- [ ] **Step 5: Run tests, then commit**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_fastembed_backend.py tests/test_agent_llm.py tests/test_embedding_contract.py -q` → PASS; ruff baseline unchanged.

```bash
git add -A apps/server uv.lock
git commit -m "feat(embeddings): bundled fastembed ONNX provider"
```

---

### Task 2: fastembed becomes the default embedding provider

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/embeddings.py` (`_resolve_embedding_provider`, L82-86)
- Modify: `apps/server/src/anima_server/api/routes/config.py` (`AVAILABLE_PROVIDERS` L72-94 — surface fastembed so the UI can show it)
- Test: `apps/server/tests/test_agent_llm.py`

**Interfaces:**
- Produces: new resolution order in `_resolve_embedding_provider()`: `settings.agent_embedding_provider` (explicit user choice, unchanged) → **`"fastembed"`** (bundled default). The old implicit piggyback on `settings.agent_provider` is REMOVED — the chat provider no longer silently determines the embedding provider. EXCEPTION preserving explicit intent: when `agent_embedding_provider` is empty but `agent_embedding_model` or `agent_embedding_base_url` IS set, keep the legacy `agent_provider` fallback (the user configured embedding details against their chat provider) — one guard clause, tested.
- Consumes: Task 1's provider arm.

- [ ] **Step 1: Failing tests**

```python
def test_embedding_provider_defaults_to_fastembed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_embedding_provider", "")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")
    monkeypatch.setattr(settings, "agent_provider", "ollama")

    assert embeddings._resolve_embedding_provider() == "fastembed"


def test_explicit_embedding_provider_still_wins(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_embedding_provider", "openai")

    assert embeddings._resolve_embedding_provider() == "openai"


def test_legacy_piggyback_kept_when_embedding_model_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_embedding_provider", "")
    monkeypatch.setattr(settings, "agent_embedding_model", "nomic-embed-text")
    monkeypatch.setattr(settings, "agent_provider", "ollama")

    assert embeddings._resolve_embedding_provider() == "ollama"
```

- [ ] **Step 2: Verify fail, implement, verify pass**

Implement the resolution change in `_resolve_embedding_provider()`. Then audit fallout: run the FULL embedding-adjacent suites — `tests/test_agent_llm.py tests/test_embedding_contract.py tests/test_embedding_sync.py tests/test_document_rag.py tests/test_memory_*.py` (glob what exists) — some existing tests implicitly relied on the ollama piggyback (e.g. cooldown tests with `agent_provider="ollama"` and empty embedding provider); update those tests to set `agent_embedding_provider="ollama"` explicitly, preserving what they test. List every such test change in your report.

- [ ] **Step 3: Surface in the provider list**

In `api/routes/config.py` `AVAILABLE_PROVIDERS`, add `ProviderInfo(name="fastembed", defaultModel="BAAI/bge-small-en-v1.5", requiresApiKey=False)` — read the ProviderInfo shape first; if the list is chat-provider-only by contract (check its consumers in the desktop app via grep in apps/desktop/src), instead SKIP this and note in the report that embedding-provider UI selection is deferred (the settings route has no embedding fields at all today — that UI gap is Plan 3 §capability work).

- [ ] **Step 4: Run + commit**

Covering run as Step 2 plus `tests/test_config_api.py` (or wherever routes/config.py is tested — grep). Ruff baseline.

```bash
git add -A apps/server
git commit -m "feat(embeddings): default to bundled fastembed provider"
```

---

### Task 3: full-document context mode

**Files:**
- Modify: `apps/server/src/anima_server/config.py` (new settings)
- Modify: `apps/server/src/anima_server/services/agent/service.py` (`_build_document_context_block`, L1764-1921)
- Test: `apps/server/tests/test_chat_document_context.py`

**Interfaces:**
- New settings in `config.py` (place near the other `document_*` settings, L67-80):
  ```python
  # Full-document context: when every selected document's text fits the
  # budget, inject whole documents instead of retrieved chunks (matches
  # cloud-assistant file-upload behavior; retrieval covers what doesn't fit).
  document_full_context: Literal["off", "auto"] = "auto"
  # Fraction of the resolved context budget the full-doc block may use.
  document_full_context_budget_ratio: float = 0.5
  # Hard ceiling in characters regardless of window size.
  document_full_context_char_cap: int = 120_000
  ```
- Produces: inside `_build_document_context_block`, BEFORE the chunk search (L1804): compute the full-doc budget as `min(resolve_context_budget_tokens() * settings.agent_block_budget_ratio_tokens→chars conversion, char cap)` — concretely: `budget_chars = min(int(resolve_context_budget_tokens() * settings.document_full_context_budget_ratio) * 3, settings.document_full_context_char_cap)` (3 chars/token mirrors `prompt_budget.estimate_char_tokens`'s conservative ratio — import and reuse that module's constant/helper rather than hardcoding 3 if one exists; read `prompt_budget.py` L163-168 first).
- New helper `_full_document_texts(runtime_db, *, user_id, document_ids) -> list[tuple[RuntimeDocument, str]] | None` — loads each document's chunks ordered by `chunk_index` (reuse `list_document_chunks` from `services/documents/store.py`), joins `content_text` with `"\n\n"`, returns `None` if the COMBINED length of all selected docs exceeds the budget (all-or-nothing: mixed full+retrieved is confusing evidence; if any doc overflows, the whole turn uses retrieval as today).
- When full-doc mode applies, the returned `MemoryBlock` keeps `label="document_context"` (downstream consumers key on the label — verify by grepping `document_context`) with value listing each document as `--- {filename} (complete document) ---\n{text}`, and knowledge-concept hits (`_document_knowledge_hits`) are still appended as today.
- Full-doc mode only fires when `document_ids` is non-empty and `settings.document_full_context == "auto"`; empty selection (no docs on the turn) behaves exactly as today.

- [ ] **Step 1: Failing tests**

Read `tests/test_chat_document_context.py` first and mirror its fixture style (it drives `_build_document_context_block` with a runtime_db and registered documents+chunks). Add:

```python
def test_small_document_injected_whole(runtime_db, monkeypatch) -> None:
    # one doc, two chunks, total well under budget
    # assert block.value contains BOTH chunks' full text in chunk_index order,
    # the "(complete document)" marker, and does NOT call search_document_chunks
    # (monkeypatch it at the service module to raise if called)
    ...

def test_oversized_selection_falls_back_to_retrieval(runtime_db, monkeypatch) -> None:
    # monkeypatch settings.document_full_context_char_cap = 50
    # assert search path used (spy on search_document_chunks), no "(complete document)" marker
    ...

def test_full_context_off_uses_retrieval(runtime_db, monkeypatch) -> None:
    # settings.document_full_context = "off" → search path even for tiny docs
    ...

def test_budget_scales_with_context_window(monkeypatch) -> None:
    # settings.agent_context_window_tokens = 200_000 vs None → budget_chars differs;
    # unit-test the budget helper directly
    ...
```

Write them as real tests (the file's existing helpers make document+chunk setup ~5 lines); the sketches above define required assertions, not literal code.

- [ ] **Step 2: Verify fail, implement, verify pass**

Implementation reuses: `resolve_context_budget_tokens` from `prompt_budget.py` (L112-133), `list_document_chunks` from the documents store. Keep the function's existing fail-open error handling style (L1780-1799).

- [ ] **Step 3: Run + commit**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_chat_document_context.py tests/test_document_rag.py tests/test_corefs_keyslots.py -q` → PASS; ruff baseline.

```bash
git add -A apps/server
git commit -m "feat(chat): full-document context mode with window-scaled budget"
```

---

### Task 4: ONNX reranker + defaults on

**Files:**
- Modify: `apps/server/src/anima_server/services/documents/reranker.py` (swap backend)
- Modify: `apps/server/src/anima_server/config.py` (`retrieval_reranker` default `"local"`, `retrieval_reranker_model` default `"Xenova/ms-marco-MiniLM-L-6-v2"`, `contextual_chunks` default `"on"`)
- Modify: `apps/server/pyproject.toml` (DELETE the `reranker` extra — sentence-transformers goes away entirely)
- Test: `apps/server/tests/test_contextual_rerank.py`

**Interfaces:**
- `rerank_chunk_ids(query, candidates) -> list[int] | None` — signature, guard (`retrieval_reranker != "local"` or <2 candidates → None), degradation contract (any failure → None, fused order kept), and the single call site in `rag.py` L173-185 ALL stay identical. Only `_load_model` changes:
  ```python
  def _create_model() -> Any:
      from fastembed.rerank import TextCrossEncoder

      cache_dir = settings.data_dir / "models" / "fastembed"
      cache_dir.mkdir(parents=True, exist_ok=True)
      return TextCrossEncoder(model_name=settings.retrieval_reranker_model, cache_dir=str(cache_dir))
  ```
  and scoring becomes `scores = list(model.rerank(query, [text for _id, text in candidates]))` — VERIFY the fastembed TextCrossEncoder API (import path `fastembed.rerank.TextCrossEncoder`, method name and return shape — it yields relevance scores in input order) against the locked fastembed version's source in the venv before writing; if the API differs (e.g. `rerank_pairs`, generator of objects with `.score`), adapt and record in the report. Keep the `_model_failed` latch and `_reset_model_cache_for_tests()`.
- Defaults flip in `config.py`: `retrieval_reranker: Literal["off", "local"] = "local"`, `retrieval_reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"`, `contextual_chunks: Literal["off", "on"] = "on"`.
- The warning message in `_load_model` no longer mentions the `reranker` extra (it's deleted) — it should say the model download may not have completed and name the cache dir.

- [ ] **Step 1: Failing tests**

Update `tests/test_contextual_rerank.py`: the existing reranker tests (L270-320) monkeypatch a fake CrossEncoder — repoint the fakes at the new `_create_model` seam (`monkeypatch.setattr(reranker, "_create_model", lambda: fake)`) with a fake exposing the verified fastembed API shape. Add one test asserting the NEW DEFAULTS: with settings untouched, `settings.retrieval_reranker == "local"` and `settings.contextual_chunks == "on"`.

Then audit the blast radius of the default flips BEFORE implementing: grep tests for `retrieval_reranker` and `contextual_chunks` — every test that implicitly assumed `"off"` defaults (e.g. rag tests not expecting the over-fetch at L80-82, contextual tests assuming no blurbs) must be updated to pin the setting it assumes. List each in the report.

- [ ] **Step 2: Verify fail, implement, verify pass**

Also delete `reranker = ["sentence-transformers>=3.0.0"]` from pyproject and `uv lock`. Grep the repo for remaining references to `sentence-transformers` and the `reranker` extra (README, plan docs are fine — code and pyproject comments must be updated).

- [ ] **Step 3: Full-suite gate + commit**

Run the FULL suite once (`ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest -q`, ~13 min — expect only the known pre-existing `test_dev_session_continuity` failure) plus ruff baseline. The default flips touch global behavior; the full suite is the only honest gate here.

```bash
git add -A apps/server uv.lock
git commit -m "feat(retrieval): ONNX reranker via fastembed; reranker and contextual blurbs default on"
```

---

## Deferred to Plan 3 (unchanged)

Capability/status endpoint + UI surfacing (including embedding-provider selection UI — the config route has no embedding fields today); OKF recompile queueing via sleep agent; golden-corpus evals with REAL models (first real download/inference of bge-small + ms-marco + docling — validates every model name and API this plan pins); dev scripts/README; `docling.utils.model_downloader` import verification.
