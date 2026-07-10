# PR #86 Stream Lifecycle Repair Design

## Context

PR #86 extracts the live-turn and approval-resume SSE pumps into the shared
`_stream_via_queue()` async generator. The public `stream_agent()` and
`stream_approve_or_deny()` generators wrap that helper with `async for`.

Closing a public stream while it is suspended at `yield` does not explicitly
close the inner async generator. The outer `aclose()` returns, but the inner
pump and turn worker remain pending. The existing queue-full regression test
passes its immediate timeout assertion, then pytest hangs while its event-loop
fixture tries to cancel the orphaned task.

The same PR also leaves server lint failures and architecture documentation
that still describes the pre-ARH runtime.

## Goals

1. Ensure closing either public SSE stream closes the shared pump and cancels
   its worker before the outer `aclose()` returns.
2. Add a regression test that detects surviving pump or worker tasks, rather
   than checking only that `aclose()` returns promptly.
3. Preserve the shared pump abstraction and existing SSE event behavior.
4. Clear PR-introduced server lint failures.
5. Update the agent-runtime and memory-system architecture docs to match the
   implemented prompt-budget and sleep-orchestrator behavior.

## Non-Goals

- Redesign SSE framing, buffering, or error events.
- Change turn cancellation semantics beyond closing leaked work.
- Refactor the broader agent service.
- Change memory, retrieval, or migration behavior.

## Design

### Explicit inner-generator ownership

Each public SSE generator will own the `_stream_via_queue()` iterator with
`contextlib.aclosing()`:

```python
async with contextlib.aclosing(_stream_via_queue(...)) as stream:
    async for event in stream:
        yield event
```

This keeps the shared implementation while making ownership explicit. When a
client disconnect closes the public generator, the context manager awaits the
inner generator's `aclose()`. `_stream_via_queue()` then runs its existing
`finally` block, cancels the worker if necessary, and waits for cancellation to
finish.

The same pattern applies to `stream_agent()` and
`stream_approve_or_deny()` so their lifecycle contracts remain identical.

### Regression test

The queue-full shutdown test will capture tasks created by the stream pump,
close the public stream, yield once to the event loop, and assert that every
captured task is done. The test must fail on the current implementation because
the inner pump survives the outer close. A focused two-test sequence will also
remain as validation for the order-dependent teardown failure.

### Lint cleanup

Fix only the reported Ruff findings in changed files: unused imports,
ambiguous Unicode in comments, import ordering, direct boolean return, the
Python 3.12 generic-function syntax, and the built-in `TimeoutError` spelling.
No unrelated formatting or refactoring is included.

### Documentation synchronization

Update canonical architecture descriptions that still reference:

- `len(text) // 4` instead of the conservative `chars/3` estimate plus prompt
  scaffolding reservation;
- the removed `run_sleep_tasks()` path instead of `run_sleeptime_agents()` and
  its task grouping/gates;
- stream shutdown behavior where the shared queue pump is relevant.

Edits will preserve document intent and update the canonical docs' edit dates.
The documentation changelog will record the synchronization.

## Error Handling

`aclosing()` does not swallow failures. Existing stream errors continue through
the shared worker's error-event path. `CancelledError` and `GeneratorExit`
continue to cancel and await the worker, and the outer public generator does
not return from close until inner cleanup finishes.

## Validation

- Demonstrate the new task-lifecycle assertion fails before the production fix.
- Run the focused stream shutdown and following cancellation test together.
- Run `apps/server/tests/test_agent_service.py`.
- Run the PR's affected agent-runtime test suites.
- Run the complete server pytest suite.
- Run `bun run lint:server`.
- Verify the runtime Alembic graph has one head.
- Run `git diff --check` and the docs drift checker.

