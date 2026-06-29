---
title: Brain System
description: The required agent runtime and state machine that capability modules attach to
category: architecture
updated: 2026-06-29
---

# Brain System

[Back to Agent Capability Modules](capability-modules/README.md)

Brain System is the non-optional agent runtime of ANIMA. Its module id is `brain.core`.

It is not a module in the same sense as camera or voice. It is the runtime state machine that makes a turn possible: identity context, thread coordination, system prompt construction, model invocation, tool routing, persistence, and the memory write boundary.

## Runtime Responsibilities

The Brain System owns the agent runtime loop:

- the conversation turn lifecycle
- thread/user locks
- prompt assembly
- memory block injection
- LLM provider calls
- tool orchestration rules
- approval flow
- cancellation
- persistence of runs/messages/steps
- final response policy
- module registry loading
- capability-aware tool assembly

These responsibilities are the minimum runtime surface for a turn.

## Agent State Machine

Every chat turn moves through a predictable state machine:

```text
receive input
-> acquire thread lock
-> load user/session/runtime context
-> resolve capability status
-> assemble prompt and gated tools
-> invoke model
-> execute approved tool calls
-> persist messages, runs, and steps
-> schedule memory/reflection background work
-> release turn
```

```mermaid
stateDiagram-v2
    [*] --> ReceiveInput
    ReceiveInput --> AcquireLock
    AcquireLock --> LoadContext
    LoadContext --> ResolveCapabilities
    ResolveCapabilities --> AssemblePrompt
    AssemblePrompt --> InvokeModel
    InvokeModel --> ToolDecision

    ToolDecision --> ExecuteTool: tool call allowed
    ExecuteTool --> InvokeModel: continue loop
    ToolDecision --> AwaitApproval: approval required
    ToolDecision --> PersistResult: final response
    ToolDecision --> Cancelled: cancel requested

    AwaitApproval --> PersistCheckpoint
    PersistCheckpoint --> [*]

    PersistResult --> ScheduleBackgroundWork
    ScheduleBackgroundWork --> ReleaseLock
    ReleaseLock --> [*]

    Cancelled --> PersistCancellation
    PersistCancellation --> ReleaseLock
```

Capability Modules attach to that state machine at explicit points:

- before model invocation, as compact capability status
- during tool assembly, as policy-gated semantic tools
- during tool execution, as module handlers
- after tool execution, as audit events or memory candidates
- after the turn, as background work inputs

## Full Turn Sequence

The Brain System is the runtime coordinator between API routes, runtime state, memory context, the model adapter, the tool executor, and capability handlers.

```mermaid
sequenceDiagram
    participant Client as Desktop / API Client
    participant Route as Chat Route
    participant Service as Agent Service
    participant Brain as brain.core
    participant Registry as Capability Registry
    participant RuntimeDB as Runtime DB
    participant Memory as Memory Context
    participant Model as Model Adapter
    participant Tools as Tool Executor
    participant Handler as Capability Handler

    Client->>Route: Send user turn
    Route->>Service: Validate user/session and start turn
    Service->>RuntimeDB: Create or load thread/run state
    Service->>Brain: Enter turn state machine
    Brain->>Registry: Resolve capability status and gated tools
    Brain->>Memory: Build prompt memory blocks
    Brain->>Model: Invoke with history, memory, and tools

    alt Model requests tool
        Model-->>Brain: Tool call
        Brain->>Tools: Validate tool policy and arguments
        Tools->>Handler: Execute semantic capability handler
        Handler-->>Tools: Structured tool result
        Tools-->>Brain: Tool result
        Brain->>Model: Continue with tool result
    else Model returns final response
        Model-->>Brain: Final response
    end

    Brain->>RuntimeDB: Persist messages, run steps, and result
    Brain->>Service: Schedule memory/reflection background work
    Service-->>Route: Return or stream response
    Route-->>Client: Response
```

## Runtime Identity

`brain.core` is the always-present runtime identity for the agent loop. It is the part of the server that receives a user turn, advances the state machine, coordinates model/tool execution, and commits the result back into runtime state.

In implementation terms, `brain.core` should map to the existing agent runtime and service orchestration path rather than becoming a separate feature package.

## Relationship To Tools

The Brain System does not hardcode every possible tool forever. It asks the capability registry for the active tool set:

```text
core tools
+ policy-gated capability tools
+ hidden bridge primitives for server handlers
= effective runtime capability surface
```

The LLM only sees semantic tools that policy allows. Hidden bridge tools exist for server code, not for direct model selection.

```mermaid
flowchart TD
    Brain["brain.core<br/>turn state machine"]
    Registry["Capability Registry"]
    Status["Capability Status<br/>enabled + configured + available"]
    Schemas["Semantic Tool Schemas"]
    Hidden["Hidden Bridge Primitives<br/>server-only"]
    Prompt["Prompt + model-visible tools"]
    Executor["Tool Executor"]
    Handler["Capability Handler"]

    Brain --> Registry
    Registry --> Status
    Registry --> Schemas
    Registry --> Hidden
    Status --> Prompt
    Schemas --> Prompt
    Prompt --> Brain
    Brain --> Executor
    Executor --> Handler
    Handler --> Hidden
```

## Relationship To Memory

The Brain System enforces the memory write boundary. Capability handlers can produce:

- transient call data
- semantic tool results
- an audit record
- memory candidates

Durable memory should be created only through Memory Core's promotion path.

```mermaid
flowchart LR
    Turn["Current Turn"]
    Handler["Capability Handler"]
    Result["Semantic Tool Result"]
    Audit["Audit Event"]
    Candidate["Memory Candidate"]
    Runtime["Runtime DB"]
    MemoryCore["memory.core<br/>promotion boundary"]
    Soul["Soul DB<br/>durable memory"]

    Turn --> Handler
    Handler --> Result
    Handler --> Audit
    Handler --> Candidate
    Result --> Turn
    Audit --> Runtime
    Candidate --> Runtime
    Runtime --> MemoryCore
    MemoryCore --> Soul
```

## Failure Mode

The Brain System should survive capability failure.

If a capability is unavailable, the turn state machine should still complete when possible. Missing capabilities degrade into explicit status and clear tool results, not runtime chaos.
