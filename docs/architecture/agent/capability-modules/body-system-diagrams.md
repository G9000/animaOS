---
title: Body System Diagrams
description: Mermaid diagrams for the Brain System and modular ANIMA body systems
category: architecture
updated: 2026-06-29
---

# Body System Diagrams

[Back to Capability Modules](README.md)

This is the visual version of the Agent Capability Modules doctrine.

ANIMA has one continuous self, a required Brain System, and optional governed body systems. The code name stays boring and precise: `capabilities`.

```text
The self is continuous.
The body is modular.
Each body system is governed.
```

## Body Map

This is the body-system view. Brain System is the agent runtime and state machine. Memory Core is the soul and recall boundary. Perception, Voice, Action, and Presence are bolt-on body systems with policy and status.

```mermaid
flowchart TB
    User["User"] --> Desktop["Desktop Shell<br/>Tauri + React"]
    User --> Chat["Chat / Voice / UI Surface"]

    subgraph Server["FastAPI Agent Service"]
        Brain["brain.core<br/>agent runtime + state machine"]
        Registry["Capability Registry<br/>manifests + status + policy"]
        Tools["Gated Semantic Tools"]
        MemoryBoundary["Memory Core Boundary<br/>recall + promotion"]

        subgraph Body["Governed Body Systems"]
            Perception["perception.*<br/>camera + screen + media"]
            Voice["voice.core<br/>speech + listening"]
            Action["action.local<br/>local execution + automation"]
            Presence["presence.core<br/>nudges + ambient state"]
        end

        Brain --> Registry
        Registry --> Body
        Registry --> Tools
        Brain --> MemoryBoundary
        Tools --> Brain
    end

    subgraph DesktopBridges["Desktop Bridges"]
        CameraBridge["camera_capture_frame<br/>hidden primitive"]
        AudioBridge["audio_capture_input / audio_play_output<br/>hidden primitives"]
        ScreenBridge["screen_capture_region<br/>hidden primitive"]
        LocalBridge["local_perform_action<br/>hidden primitive"]
    end

    subgraph Core["Portable Core"]
        Soul["Soul DB<br/>durable identity"]
        Runtime["Runtime DB<br/>working state"]
        Archive["Encrypted Archive<br/>transcripts"]
    end

    Chat --> Brain
    Desktop --> DesktopBridges

    Perception --> CameraBridge
    Perception --> ScreenBridge
    Voice --> AudioBridge
    Action --> LocalBridge

    MemoryBoundary --> Soul
    MemoryBoundary --> Runtime
    MemoryBoundary --> Archive
    Presence --> Runtime
    Action --> Runtime
    Perception --> Runtime
    Voice --> Runtime
```

## Turn Flow

Every turn resolves the body before the model speaks. The agent gets only the capabilities that are enabled, configured, available, and safe for the current model/provider.

```mermaid
sequenceDiagram
    participant User as User
    participant Desktop as Desktop Shell
    participant Service as Agent Service
    participant Registry as Capability Registry
    participant Brain as brain.core
    participant Module as Capability Module
    participant Bridge as Desktop Bridge
    participant Audit as Audit
    participant Memory as Memory Core Boundary

    User->>Desktop: Sends message or starts interaction
    Desktop->>Service: Chat turn request
    Service->>Registry: Resolve enabled modules and bridge status
    Registry-->>Service: Capability status block and semantic tools
    Service->>Brain: Invoke turn with memory blocks and gated tools
    Brain->>Module: Calls semantic tool if needed
    Module->>Registry: Re-check policy and availability

    alt Module needs hardware
        Module->>Bridge: Request hidden primitive
        Bridge-->>Module: Transient payload or denial
    end

    Module->>Audit: Record sensitive capability event
    Module->>Memory: Emit memory candidate only if policy allows
    Module-->>Brain: Return semantic result
    Brain-->>Service: Final response
    Service-->>Desktop: Stream or return response
    Desktop-->>User: Display response
```

## Camera Perception Flow

Camera sight is a one-frame perception sense. It is not continuous video and not background watching.

```mermaid
flowchart TD
    Start["Agent needs visual context"] --> Tool["view_camera_snapshot(question, purpose)"]
    Tool --> Enabled{"perception.camera enabled?"}
    Enabled -- No --> Disabled["Return policy error<br/>camera disabled"]
    Enabled -- Yes --> AgentCapture{"Agent-requested capture allowed?"}
    AgentCapture -- No --> CaptureBlocked["Return policy error<br/>agent capture disabled"]
    AgentCapture -- Yes --> Vision{"Current model supports images?"}
    Vision -- No --> NoVision["Fail before requesting camera"]
    Vision -- Yes --> Bridge{"Desktop bridge connected?"}
    Bridge -- No --> NoBridge["Return bridge unavailable"]
    Bridge -- Yes --> Consent{"Consent granted?"}
    Consent -- No --> Denied["Return consent denied"]
    Consent -- Yes --> Frame["Desktop captures one frame<br/>camera_capture_frame"]
    Frame --> Analyze["Vision model analyzes frame"]
    Analyze --> Delete["Delete raw payload / temp file"]
    Delete --> Audit["Record audit event<br/>no raw image bytes"]
    Audit --> Result["Return text observation<br/>to Brain System"]
    Result --> Memory{"Worth durable memory?"}
    Memory -- No --> RuntimeOnly["Keep as turn/runtime context"]
    Memory -- Yes --> Candidate["Create memory candidate<br/>Memory Core decides"]
```

## Retention Boundary

This is the most important privacy diagram: sensation does not automatically become soul.

```mermaid
flowchart LR
    Raw["Raw sensation<br/>image, audio, screen, local trace"]
    Transient["Transient processing<br/>current tool call"]
    DeleteRaw["Default path<br/>delete raw payload"]
    Observation["Semantic observation<br/>text result"]
    Runtime["Runtime trace<br/>operational state"]
    Drop["No durable write<br/>if not meaningful"]
    Archive["Encrypted archive<br/>if part of chat record"]
    Candidate["Memory candidate<br/>pending review/promotion"]
    SoulWriter["Soul Writer<br/>dedupe + evidence + policy"]
    Soul["Soul DB<br/>durable identity/memory"]

    Raw --> Transient
    Transient --> DeleteRaw
    Transient --> Observation
    Observation --> Runtime
    Observation --> Drop
    Observation --> Archive
    Runtime --> Candidate
    Archive --> Candidate
    Candidate --> SoulWriter
    SoulWriter --> Soul
```

Module rule:

```text
Body systems can experience.
Memory Core decides what becomes durable.
```

## Capability Lifecycle

Enabled is not the same as available. A body system can exist but still be unusable because config, bridge, permission, or model support is missing.

```mermaid
stateDiagram-v2
    [*] --> Installed
    Installed --> Disabled: optional module default
    Installed --> Enabled: required module or user enables
    Disabled --> Enabled: user enables
    Enabled --> Configured: valid config
    Enabled --> Degraded: missing optional config
    Configured --> BridgeConnected: desktop bridge online
    Configured --> Unavailable: required bridge missing
    BridgeConnected --> Available: provider and policy pass
    BridgeConnected --> Degraded: partial provider support
    Available --> Degraded: bridge/provider weakens
    Available --> Unavailable: bridge disconnects
    Degraded --> Available: dependency restored
    Unavailable --> Available: dependency restored
    Enabled --> Disabled: user disables
```

## Prompt View

Brain System should receive a compact status block, not every config detail.

```text
Host:
- brain.core: available

Memory boundary:
- memory.core: available

Capability modules:
- perception.camera: available; consent required per capture
- voice.core: disabled
- action.local: degraded; destructive actions require approval
- presence.core: available; quiet hours active
```

That gives ANIMA body awareness without flooding every turn.

## Implementation Spine

The code path should eventually look like this:

```mermaid
flowchart TD
    Builtins["builtin.py<br/>built-in manifests"]
    Types["types.py<br/>CapabilityManifest"]
    Config["config.py<br/>user settings"]
    Status["status.py<br/>availability resolver"]
    Registry["registry.py<br/>module registry"]
    ToolGate["tools.py<br/>semantic tool projection"]
    Audit["audit.py<br/>sensitive event records"]
    Modules["modules/*<br/>module handlers"]

    Types --> Builtins
    Builtins --> Registry
    Config --> Registry
    Status --> Registry
    Registry --> ToolGate
    ToolGate --> Modules
    Modules --> Audit
    Modules --> Registry
```

This is the production-grade shape: manifest first, policy before tools, bridges hidden, audit without hoarding, and memory protected.
