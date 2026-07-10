# PR #86 Mixed Tool Execution Order Repair

## Context

`ToolExecutor.execute_many()` currently gathers every delegated client tool in
a batch before it executes server tools. Results are written back into model
order, but side effects can occur out of order: a delegated client action can
leapfrog an earlier server tool.

## Requirement

Preserve the model's ordering boundary between server and delegated tools while
retaining safe parallel execution for adjacent delegated client tools.

## Considered Approaches

1. **Contiguous delegated groups (selected).** Walk the batch from left to
   right. Execute server tools at their position and gather each maximal run of
   adjacent delegated tools. This preserves ordering barriers and keeps useful
   client-tool parallelism.
2. **Sequential mixed batches.** Parallelize only when the entire batch is
   delegated. This is simpler, but unnecessarily serializes adjacent delegated
   calls inside otherwise mixed batches.
3. **Dependency graph scheduler.** Model explicit ordering dependencies between
   calls. This adds complexity without a current requirement beyond contiguous
   ordering barriers.

## Design

`execute_many()` will maintain a pending list of adjacent delegated call
indices. Before executing a server tool, it will flush that list with
`asyncio.gather()`, store results at their original indices, and then await the
server tool. After the scan, it will flush the final delegated group.

A group of one delegated tool may execute directly or through `gather()`; this
is an implementation detail as long as ordering and error propagation remain
unchanged. Exceptions continue to propagate according to the existing
`execute()` contract. Returned results remain in input order.

## Regression Coverage

Tests will use observable start/finish events to prove that:

- an earlier server tool finishes before a following delegated group starts;
- adjacent delegated calls overlap rather than becoming sequential;
- a later server tool starts only after the delegated group completes; and
- returned results remain aligned with the original tool-call order.

## Scope

Only `ToolExecutor.execute_many()` and its focused executor tests change. No
tool schema, delegation protocol, persistence behavior, or public API changes.
