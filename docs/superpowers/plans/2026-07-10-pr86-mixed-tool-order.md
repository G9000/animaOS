# PR #86 Mixed Tool Execution Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve server/delegated tool-call ordering while retaining parallel execution for adjacent delegated client tools.

**Architecture:** `ToolExecutor.execute_parallel()` scans calls left to right. Server calls are awaited ordering barriers; maximal contiguous delegated-only groups are flushed with `asyncio.gather()` and written back to their original result indices.

**Tech Stack:** Python 3.12, asyncio, pytest, pytest-asyncio

---

### Task 0: Commit the reviewed planning artifact (completed in `222bd81b`)

**Files:**
- Add: `docs/superpowers/plans/2026-07-10-pr86-mixed-tool-order.md`

- [x] **Step 1: Commit the approved plan before execution**

```powershell
git add docs/superpowers/plans/2026-07-10-pr86-mixed-tool-order.md
git -c commit.gpgsign=false commit -m "docs: plan mixed tool execution ordering"
```

### Task 1: Characterize mixed scheduling boundaries

**Files:**
- Modify: `apps/server/tests/test_ttft_optimizations.py`

- [ ] **Step 1: Add deterministic test helpers and the failing mixed-order regression**

Import `DelegatedToolResult`. Add a local async server tool that records
`<name>:start` and `<name>:finish`. Add this regression:

```python
@pytest.mark.asyncio
async def test_mixed_tool_execution_preserves_ordering_barriers() -> None:
    events: list[str] = []
    both_clients_started = asyncio.Event()
    release_clients = asyncio.Event()
    started_count = 0

    class ServerTool:
        def __init__(self, name: str) -> None:
            self.name = name

        async def ainvoke(self, payload: dict) -> str:
            del payload
            events.append(f"{self.name}:start")
            await asyncio.sleep(0)
            events.append(f"{self.name}:finish")
            return self.name

    async def delegate(call_id: str, name: str, args: dict) -> DelegatedToolResult:
        nonlocal started_count
        del args
        events.append(f"{name}:start")
        started_count += 1
        if started_count == 2:
            both_clients_started.set()
        await release_clients.wait()
        events.append(f"{name}:finish")
        return DelegatedToolResult(call_id=call_id, name=name, output=name)

    executor = ToolExecutor(
        [ServerTool("server_before"), ServerTool("server_after")],
        delegate=delegate,
        delegated_tool_names=frozenset({"client_a", "client_b"}),
    )
    task = asyncio.create_task(
        executor.execute_parallel(
            [
                (_tool_call("server_before", "s1"), False),
                (_tool_call("client_a", "c1"), False),
                (_tool_call("client_b", "c2"), False),
                (_tool_call("server_after", "s2"), False),
            ]
        )
    )
    results = None
    try:
        await asyncio.wait_for(both_clients_started.wait(), timeout=1)
        assert events[:2] == ["server_before:start", "server_before:finish"]
        assert {"client_a:start", "client_b:start"}.issubset(events)
        assert "server_after:start" not in events
    finally:
        release_clients.set()
        results = await asyncio.wait_for(task, timeout=1)

    assert [result.call_id for result in results] == ["s1", "c1", "c2", "s2"]
    assert events[-2:] == ["server_after:start", "server_after:finish"]
```

- [ ] **Step 2: Run the regression and verify RED**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --project apps/server pytest -q apps/server/tests/test_ttft_optimizations.py::test_mixed_tool_execution_preserves_ordering_barriers
```

Expected: FAIL because delegated calls start before `server_before`; `finally`
releases them and drains the task.

- [ ] **Step 3: Add exact error-result continuation coverage**

Use a real delegate that raises for `client_fail` and succeeds for `client_ok`:

```python
@pytest.mark.asyncio
async def test_delegated_error_result_continues_to_later_server_barrier() -> None:
    events: list[str] = []

    class ServerTool:
        name = "server_after"

        async def ainvoke(self, payload: dict) -> str:
            del payload
            events.append("server_after:start")
            await asyncio.sleep(0)
            events.append("server_after:finish")
            return "server_after"

    async def delegate(call_id: str, name: str, args: dict) -> DelegatedToolResult:
        del args
        events.append(name)
        if name == "client_fail":
            raise RuntimeError("delegated failure")
        return DelegatedToolResult(call_id=call_id, name=name, output=name)

    executor = ToolExecutor(
        [ServerTool()],
        delegate=delegate,
        delegated_tool_names=frozenset({"client_fail", "client_ok"}),
    )
    results = await executor.execute_parallel(
        [
            (_tool_call("client_fail", "c1"), False),
            (_tool_call("client_ok", "c2"), False),
            (_tool_call("server_after", "s1"), False),
        ]
    )

    assert [result.call_id for result in results] == ["c1", "c2", "s1"]
    assert results[0].is_error is True
    assert results[1].is_error is False
    assert set(events[:2]) == {"client_fail", "client_ok"}
    assert events[-2:] == ["server_after:start", "server_after:finish"]
```

- [ ] **Step 4: Add exact caller-cancellation coverage**

Use two blocked delegated calls and drain the scheduler even if setup times out:

```python
@pytest.mark.asyncio
async def test_execute_parallel_cancellation_skips_later_server_barrier() -> None:
    events: list[str] = []
    both_clients_started = asyncio.Event()
    never_release = asyncio.Event()
    started_count = 0

    class ServerTool:
        name = "server_after"

        async def ainvoke(self, payload: dict) -> str:
            del payload
            events.append("server_after:start")
            return "server_after"

    async def delegate(call_id: str, name: str, args: dict) -> DelegatedToolResult:
        nonlocal started_count
        del args
        events.append(f"{name}:start")
        started_count += 1
        if started_count == 2:
            both_clients_started.set()
        await never_release.wait()
        return DelegatedToolResult(call_id=call_id, name=name, output=name)

    executor = ToolExecutor(
        [ServerTool()],
        delegate=delegate,
        delegated_tool_names=frozenset({"client_a", "client_b"}),
    )
    task = asyncio.create_task(
        executor.execute_parallel(
            [
                (_tool_call("client_a", "c1"), False),
                (_tool_call("client_b", "c2"), False),
                (_tool_call("server_after", "s1"), False),
            ]
        )
    )
    try:
        await asyncio.wait_for(both_clients_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert "server_after:start" not in events
```

- [ ] **Step 5: Run all three characterization nodes**

```powershell
uv run --project apps/server pytest -q `
  apps/server/tests/test_ttft_optimizations.py::test_mixed_tool_execution_preserves_ordering_barriers `
  apps/server/tests/test_ttft_optimizations.py::test_delegated_error_result_continues_to_later_server_barrier `
  apps/server/tests/test_ttft_optimizations.py::test_execute_parallel_cancellation_skips_later_server_barrier
```

Expected before implementation: the mixed-order node fails; error and
cancellation characterization nodes pass.

### Task 2: Schedule contiguous delegated groups

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/executor.py:187-223`
- Test: `apps/server/tests/test_ttft_optimizations.py`

- [ ] **Step 1: Implement the minimal scheduler**

Replace the global delegated-index gather and sequential pass with:

```python
results: list[ToolExecutionResult | None] = [None] * len(tool_calls)
delegated_group: list[int] = []

async def flush_delegated_group() -> None:
    if not delegated_group:
        return
    gathered = await asyncio.gather(
        *(
            self.execute(tool_calls[index][0], is_terminal=tool_calls[index][1])
            for index in delegated_group
        )
    )
    for index, result in zip(delegated_group, gathered, strict=True):
        results[index] = result
    delegated_group.clear()

for index, (tool_call, terminal) in enumerate(tool_calls):
    if self._delegate is not None and tool_call.name in self._delegated_tool_names:
        delegated_group.append(index)
        continue
    await flush_delegated_group()
    results[index] = await self.execute(tool_call, is_terminal=terminal)

await flush_delegated_group()
return [result for result in results if result is not None]
```

- [ ] **Step 2: Run the three new nodes and verify GREEN**

Run the Task 1 Step 5 command. Expected: three pass.

- [ ] **Step 3: Run focused executor/runtime suites**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --project apps/server pytest -q `
  apps/server/tests/test_ttft_optimizations.py `
  apps/server/tests/test_runtime_enhancements.py `
  apps/server/tests/test_executor_isolation.py `
  apps/server/tests/test_agent_runtime.py
bun run lint:server
git diff --check
```

- [ ] **Step 4: Run repository validation and smoke coverage**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
bun run test:server
bun run build:server
uv run --project apps/server pytest -q `
  apps/server/tests/test_health.py `
  apps/server/tests/test_chat.py `
  apps/server/tests/test_ttft_optimizations.py::test_mixed_tool_execution_preserves_ordering_barriers
```

Expected: full backend suite, server build, `/health`, chat, and mixed-tool smoke
coverage pass.

- [ ] **Step 5: Commit implementation**

```powershell
git add apps/server/src/anima_server/services/agent/executor.py `
  apps/server/tests/test_ttft_optimizations.py
git -c commit.gpgsign=false commit -m "server: preserve mixed tool execution order"
```

### Task 3: Publish and close the review loop

**Files:**
- No additional local file changes expected.

- [ ] **Step 1: Push and verify the PR head**

```powershell
git push origin worktree-agent-runtime-hardening-p6
$local = git rev-parse HEAD
$prHead = gh pr view 86 --repo G9000/animaOS --json headRefOid --jq .headRefOid
if ($local -ne $prHead) { throw "PR head mismatch" }
```

- [ ] **Step 2: Reply inline and resolve the thread**

```powershell
$sha = git rev-parse --short HEAD
$commentNode = 'PRRC_kwDORPzHkM7UEVeK'
$databaseId = gh api graphql -f query='query($id: ID!) { node(id: $id) { ... on PullRequestReviewComment { databaseId } } }' -f id=$commentNode --jq .data.node.databaseId
$body = "Fixed in ${sha}: execute_parallel now gathers only contiguous delegated groups, preserving server-tool barriers while retaining delegated concurrency. Added ordering, error-result continuation, and cancellation regressions."
gh api "repos/G9000/animaOS/pulls/86/comments/$databaseId/replies" -f body=$body
$threadId = 'PRRT_kwDORPzHkM6P1onx'
gh api graphql -f query='mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id isResolved } } }' -f id=$threadId
```

- [ ] **Step 3: Re-request review**

```powershell
gh pr comment 86 --repo G9000/animaOS --body '@codex review'
```

- [ ] **Step 4: Wait for the fresh current-head review and re-query threads**

Run this query on each poll:

```powershell
$query = @'
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      headRefOid
      reviews(last: 50) {
        nodes { submittedAt author { login } commit { oid } }
      }
      reviewThreads(first: 100) {
        nodes { id isResolved isOutdated path line }
      }
    }
  }
}
'@
$state = gh api graphql -f query=$query -f owner=G9000 -f repo=animaOS -F number=86 | ConvertFrom-Json
$pr = $state.data.repository.pullRequest
$latestCodex = @($pr.reviews.nodes) |
  Where-Object { $_.author.login -like 'chatgpt-codex-connector*' } |
  Sort-Object submittedAt -Descending |
  Select-Object -First 1
$unresolved = @($pr.reviewThreads.nodes | Where-Object { -not $_.isResolved -and -not $_.isOutdated })
[pscustomobject]@{
  headRefOid = $pr.headRefOid
  latestCodexReviewOid = $latestCodex.commit.oid
  unresolvedCount = $unresolved.Count
} | ConvertTo-Json
```

If `latestCodexReviewOid != headRefOid`, wait and poll again. If the OIDs match
but `unresolvedCount > 0`, address those threads and repeat Tasks 2-3. Complete
only when the OIDs match and `unresolvedCount == 0`.
