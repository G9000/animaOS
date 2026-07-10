# PR #86 Stream Lifecycle Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both public SSE streams close their shared pump and worker deterministically, then restore lint-clean and documentation-synchronized PR validation.

**Architecture:** Keep `_stream_via_queue()` as the single queue/worker implementation. Give each public async generator explicit ownership of the inner pump with `contextlib.aclosing()`, so outer-generator shutdown awaits the inner generator's existing cancellation cleanup. Limit all remaining edits to PR-introduced Ruff findings and factual architecture-doc drift.

**Tech Stack:** Python 3.12, asyncio async generators, FastAPI SSE, pytest/pytest-asyncio, Ruff, Alembic, Markdown.

---

## File Map

- Modify `apps/server/tests/test_agent_service.py`: add lifecycle regression coverage for both public stream wrappers.
- Modify `apps/server/src/anima_server/services/agent/service.py`: explicitly close each shared stream pump.
- Modify the Ruff-reported Python/test files only: mechanical cleanup with no behavior changes except Python 3.12 generic syntax.
- Modify `docs/architecture/agent/agent-runtime.md`: prompt estimation and shared SSE shutdown behavior.
- Modify `docs/architecture/memory/memory-system.md`: current sleep orchestrator naming and flow.
- Modify `docs/CHANGELOG.md`: record the canonical documentation synchronization.

### Task 1: Prove the public stream ownership regression

**Files:**
- Modify: `apps/server/tests/test_agent_service.py` (lines 2181-2274 before edits)
- Test: `apps/server/tests/test_agent_service.py`

- [ ] **Step 1: Add a parameterized failing lifecycle test for both wrappers**

Add a test that replaces `_stream_via_queue` with a controlled inner async
generator, opens each public wrapper, consumes one event, closes the outer
generator, and asserts the inner generator's `finally` ran:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["agent", "approval"])
async def test_public_stream_closes_shared_pump(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    inner_closed = asyncio.Event()
    created_streams: list[object] = []

    def fake_stream_via_queue(*args: object, **kwargs: object):
        del args, kwargs

        async def inner():
            try:
                yield agent_service.build_error_event("probe")
                await asyncio.Future()
            finally:
                inner_closed.set()

        stream = inner()
        created_streams.append(stream)
        return stream

    monkeypatch.setattr(agent_service, "_stream_via_queue", fake_stream_via_queue)
    stream = (
        agent_service.stream_agent("hello", 1, object(), object())
        if entrypoint == "agent"
        else agent_service.stream_approve_or_deny(1, 1, True, object(), object())
    )

    try:
        await anext(stream)
        await stream.aclose()
        assert inner_closed.is_set()
    finally:
        for created in created_streams:
            await created.aclose()
```

Use the appropriate `AsyncGenerator` annotation if Ruff requires one; do not
weaken the assertion or rely on garbage collection.

- [ ] **Step 2: Strengthen the production-shaped test to detect the real worker**

In `test_stream_shutdown_does_not_deadlock_when_queue_full`, snapshot
`asyncio.all_tasks()` before opening the stream. After `gen.aclose()`, yield one
event-loop turn and collect every newly-created pending task whose coroutine
qualified name contains `_stream_via_queue.<locals>.worker`. Assert the list is
empty, but cancel and gather any leaked tasks in `finally` so the expected RED
run exits cleanly instead of hanging pytest teardown:

```python
tasks_before = set(asyncio.all_tasks())
# open stream, consume one event, and close it as the existing test does
await asyncio.sleep(0)
leaked_workers = [
    task
    for task in asyncio.all_tasks() - tasks_before
    if not task.done()
    and "_stream_via_queue.<locals>.worker" in task.get_coro().__qualname__
]
try:
    assert leaked_workers == []
finally:
    for task in leaked_workers:
        task.cancel()
    if leaked_workers:
        await asyncio.gather(*leaked_workers, return_exceptions=True)
```

This assertion exercises the real `_stream_via_queue()` pump and proves its
worker has reached a terminal state before public `aclose()` returns.

- [ ] **Step 3: Run both tests and verify RED**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
bun run test:server -- -vv `
  apps/server/tests/test_agent_service.py::test_public_stream_closes_shared_pump `
  apps/server/tests/test_agent_service.py::test_stream_shutdown_does_not_deadlock_when_queue_full
```

Expected: the parameterized cases fail because `inner_closed` is false and the
production-shaped test fails with one pending shared-pump worker. The command
still exits because the test cleanup cancels the captured leaked worker.

### Task 2: Close the shared pump from both SSE entry points

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py` (lines 649-653 before edits)
- Modify: `apps/server/src/anima_server/services/agent/service.py` (lines 3162-3166 before edits)
- Test: `apps/server/tests/test_agent_service.py`

- [ ] **Step 1: Implement explicit ownership with `contextlib.aclosing`**

Apply the same minimal pattern to `stream_approve_or_deny()` and
`stream_agent()`:

```python
async with contextlib.aclosing(
    _stream_via_queue(run_turn, failure_log=...)
) as stream:
    async for event in stream:
        yield event
```

Do not change `_stream_via_queue()` event ordering, queue size, sentinel logic,
or exception handling.

- [ ] **Step 2: Run the new lifecycle test and verify GREEN**

Run the Task 1 focused command. Expected: `3 passed` (two parameterized public
wrapper cases plus the production-shaped worker-lifecycle case).

- [ ] **Step 3: Verify the production-shaped teardown sequence**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
bun run test:server -- -vv `
  apps/server/tests/test_agent_service.py::test_stream_shutdown_does_not_deadlock_when_queue_full `
  apps/server/tests/test_agent_service.py::test_cancel_agent_run_does_not_leak_preset_event_for_terminal_run
```

Expected: `2 passed` and the process exits normally.

- [ ] **Step 4: Run the complete service test module**

Run:

```powershell
bun run test:server -- -vv apps/server/tests/test_agent_service.py
```

Expected: all tests pass and pytest exits without hanging.

- [ ] **Step 5: Commit the lifecycle repair**

```powershell
git add apps/server/src/anima_server/services/agent/service.py apps/server/tests/test_agent_service.py
git -c commit.gpgsign=false commit -m "server: close shared SSE pumps on disconnect"
```

### Task 3: Clear the PR-introduced Ruff failures

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/threads.py`
- Modify: `apps/server/src/anima_server/services/agent/compaction.py`
- Modify: `apps/server/src/anima_server/services/agent/emotional_patterns.py`
- Modify: `apps/server/src/anima_server/services/agent/llm.py`
- Modify: `apps/server/src/anima_server/services/agent/persistence.py`
- Modify: `apps/server/src/anima_server/services/agent/runtime.py`
- Modify: `apps/server/tests/test_background_dirty_checks.py`
- Modify: `apps/server/tests/test_context_token_hygiene.py`
- Modify: `apps/server/tests/test_llm_client_robustness.py`
- Modify: `apps/server/tests/test_llm_retry.py`
- Modify: `apps/server/tests/test_sleep_agent.py`
- Modify: `apps/server/tests/test_soul_writer.py`

- [ ] **Step 1: Run Ruff's safe fixes**

From `apps/server` run:

```powershell
uv run --project . ruff check src tests --fix
```

Review the diff and retain only changes corresponding to reported PR findings.

- [ ] **Step 2: Fix the non-automatic findings minimally**

- Replace ambiguous en dash/multiplication symbols in comments with ASCII.
- Return the emotional-pattern condition directly.
- Convert `invoke_with_retry` to Python 3.12 generic syntax and remove `TypeVar`/`_T`.
- Remove only the unused imports reported by Ruff.
- Keep runtime behavior unchanged.

- [ ] **Step 3: Re-run server lint**

Run `bun run lint:server` from the repository root.

Expected: Ruff exits 0 with no findings.

- [ ] **Step 4: Run the directly affected tests**

Run:

```powershell
bun run test:server -- `
  apps/server/tests/test_context_token_hygiene.py `
  apps/server/tests/test_llm_client_robustness.py `
  apps/server/tests/test_llm_retry.py `
  apps/server/tests/test_sleep_agent.py `
  apps/server/tests/test_soul_writer.py `
  apps/server/tests/test_background_dirty_checks.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the lint cleanup**

```powershell
git add apps/server/src apps/server/tests
git -c commit.gpgsign=false commit -m "server: clear runtime hardening lint findings"
```

### Task 4: Synchronize the canonical runtime documentation

**Files:**
- Modify: `docs/architecture/agent/agent-runtime.md` (frontmatter and runtime sections near lines 131, 179, 520, and 1023)
- Modify: `docs/architecture/memory/memory-system.md` (maintenance sections near lines 205, 812, and 870)
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Correct the prompt-estimation description**

Replace all `chars/4` examples with the implemented conservative `chars/3`
fallback and explain the fixed scaffolding/tool-schema reservation. Keep exact
token counting provider-dependent rather than claiming every estimate is exact.

- [ ] **Step 2: Correct the sleep orchestration description**

Replace stale `run_sleep_tasks()` references with `run_sleeptime_agents()` and
describe the current grouped task issuance, freshness gates, heat gates, and
manual `force=True` behavior without inventing product behavior.

- [ ] **Step 3: Document shared SSE shutdown ownership**

In the streaming section, state that both live-turn and approval-resume streams
use `_stream_via_queue()` and that closing the public stream closes the pump and
cancels/awaits its worker.

- [ ] **Step 4: Update metadata and changelog**

Set `updated: 2026-07-10` on canonical docs with that frontmatter field and add
a `2026-07-10` entry to `docs/CHANGELOG.md` describing the synchronization.

- [ ] **Step 5: Run docs drift checks**

Run:

```powershell
$env:UV_CACHE_DIR="$env:TEMP\uv-cache"
uv run python C:\Users\leoca\.codex\skills\docs-code-sync\scripts\check_docs_code_sync.py --repo-root . --docs-root docs
rg -n "chars/4|// 4|run_sleep_tasks\(\)" docs/architecture/agent/agent-runtime.md docs/architecture/memory/memory-system.md
```

Expected: checker exits 0 and `rg` finds no stale references in the canonical
documents.

- [ ] **Step 6: Commit the documentation sync**

```powershell
git add docs/architecture/agent/agent-runtime.md docs/architecture/memory/memory-system.md docs/CHANGELOG.md
git -c commit.gpgsign=false commit -m "docs: sync agent runtime hardening architecture"
```

### Task 5: Final branch validation

**Files:**
- Verify only; no planned modifications.

- [ ] **Step 1: Run the PR-focused runtime suites**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
bun run test:server -- `
  apps/server/tests/test_agent_runtime.py `
  apps/server/tests/test_agent_service.py `
  apps/server/tests/test_agent_compaction.py `
  apps/server/tests/test_llm_client_robustness.py `
  apps/server/tests/test_prompt_caching.py `
  apps/server/tests/test_embedding_contract.py `
  apps/server/tests/test_extraction_durability.py `
  apps/server/tests/test_retrieval_scoring.py `
  apps/server/tests/test_retry_hygiene.py `
  apps/server/tests/test_sleep_agent.py `
  apps/server/tests/test_soul_block_locking.py `
  apps/server/tests/test_step_loop_characterization.py `
  apps/server/tests/test_ttft_optimizations.py `
  apps/server/tests/test_soul_writer.py
```

Expected: all collected tests pass and pytest exits normally.

- [ ] **Step 2: Run the complete server test suite**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
bun run test:server
```

Expected: exit 0 with zero failed tests and no teardown hang.

- [ ] **Step 3: Run the repository-required build**

Run:

```powershell
bun run build
```

Expected: server, desktop, and Animus build/check targets exit 0.

- [ ] **Step 4: Smoke auth, chat, memory, settings, and health flows**

Run the API-level smoke modules:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
bun run test:server -- `
  apps/server/tests/test_auth.py `
  apps/server/tests/test_chat.py `
  apps/server/tests/test_memory_api.py `
  apps/server/tests/test_config_personas.py `
  apps/server/tests/test_health.py
```

Expected: all modules pass. `test_auth.py` exercises `GET /api/health` before
and after provisioning, providing an in-process health-endpoint verification
without launching a second developer server against local data.

- [ ] **Step 5: Re-run lint and migration checks**

```powershell
bun run lint:server
Push-Location apps/server
uv run --project . alembic -c alembic_runtime.ini heads
Pop-Location
```

Expected: lint exits 0; runtime Alembic reports exactly
`026_reembed_completions (head)`.

- [ ] **Step 6: Check patch hygiene and branch state**

```powershell
git diff --check origin/main...HEAD
git status --short --branch
```

Expected: no whitespace errors and no uncommitted files.

- [ ] **Step 7: Review the final diff against the approved scope**

Confirm the final commits contain only the lifecycle repair, regression tests,
reported lint cleanup, and canonical documentation sync. Do not push or alter
GitHub review state without explicit user authorization.
