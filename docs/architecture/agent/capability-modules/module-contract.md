---
title: Capability Module Contract
description: Manifest and policy fields every Agent Capability Module declares
category: architecture
updated: 2026-06-29
---

# Capability Module Contract

[Back to Capability Modules](README.md)

A capability module is a governed extension of Brain System. It is not just a Python import and not just a tool list. It declares what it is, what it needs, what it exposes, what it may store, and how it should fail.

## Manifest Shape

Each built-in module should declare a manifest similar to:

```python
CapabilityManifest(
    id="perception.camera",
    version="1.0.0",
    contract_version="1",
    family="perception",
    display_name="Camera Perception",
    description="Use the local desktop camera for consented one-frame visual perception.",
    required=False,
    default_enabled=False,
    min_brain_version="1.0.0",
    max_brain_version=None,
    config_schema_version="1",
    config_schema={...},
    tools=[...],
    bridge_requirements=[...],
    memory_policy={...},
    audit_policy={...},
    migrations=[...],
)
```

The exact implementation can evolve, but these concepts should stay.

## Required Fields

| Field | Meaning |
| --- | --- |
| `id` | Stable dotted id, for example `perception.camera` |
| `version` | Module implementation version |
| `contract_version` | Capability manifest/tool contract version |
| `family` | `memory`, `perception`, `voice`, `action`, `presence`, etc. |
| `display_name` | User-facing name |
| `description` | Short explanation for settings/help |
| `required` | Whether the module can be disabled |
| `default_enabled` | Whether optional module starts enabled |
| `min_brain_version` | Oldest Brain System version this module supports |
| `max_brain_version` | Optional upper bound for incompatible future Brain System versions |
| `config_schema_version` | Version of the module's persisted config shape |
| `config_schema` | Typed config fields rendered by desktop |
| `tools` | Semantic agent tools this module contributes |
| `bridge_requirements` | Desktop/hardware bridge needs |
| `memory_policy` | What outputs can be retained |
| `audit_policy` | What events must be recorded |
| `migrations` | Config/runtime migrations the module can run during upgrade |

## Version And Compatibility

Modules are versioned parts attached to the Brain System runtime.

The registry should reject or degrade a module before exposing tools if:

- the module's `contract_version` is unsupported
- `min_brain_version` is newer than the running Brain System
- `max_brain_version` is older than the running Brain System
- persisted config cannot be migrated to `config_schema_version`
- required bridge/provider capabilities are missing

Version compatibility is checked separately from user enablement. A module can be installed and disabled while still needing an upgrade, or installed and enabled while unavailable because the current Brain System cannot run it.

## Config Schema

Config schema should be human-comprehensible and local-first.

For `perception.camera`, config might include:

| Field | Default | Purpose |
| --- | --- | --- |
| `agent_requested_capture_enabled` | `false` | Whether ANIMA may request a snapshot |
| `manual_chat_capture_enabled` | `true` | Whether user can attach a camera snapshot in chat |
| `consent_mode` | `ask_each_time` | Capture consent policy |
| `retention_mode` | `transient_only` | Raw frame retention policy |
| `max_width` | `1280` | Capture scaling limit |
| `max_height` | `720` | Capture scaling limit |
| `audit_enabled` | `true` | Whether use is recorded |

Config is not just UI. It is policy input.

When `config_schema_version` changes, the module should provide migration logic or mark itself `migration_failed` until the user or system can resolve it.

## Tools

Modules expose semantic tools, not hardware primitives.

Good:

- `view_camera_snapshot(question, purpose)`
- `transcribe_voice_note()`
- `summarize_current_screen_region()`

Bad:

- `camera_capture_frame`
- `mic_get_pcm_buffer`
- `screenshot_raw_desktop`

Low-level bridge primitives can exist, but they are hidden from the LLM and callable only by server module code.

## Bridge Requirements

A bridge requirement says: "This module needs a local surface the server cannot own."

Examples:

| Module | Bridge |
| --- | --- |
| `perception.camera` | desktop camera frame capture |
| `voice.core` | desktop microphone and speaker |
| `perception.screen` | desktop screen/window capture |
| `action.local` | local action client |

Bridge status affects module availability. Enabled does not always mean available.

## Memory Policy

Every module declares what its outputs may become.

| Policy | Meaning |
| --- | --- |
| `transient` | Used during current call/turn only |
| `runtime` | Stored in runtime operational state |
| `archive` | Stored in encrypted transcript/archive |
| `soul_candidate` | Proposed to durable memory pipeline |
| `soul` | Durable identity/memory after approved write path |

Sensitive modules should default to `transient` or `runtime`.

## Audit Policy

Audit policy should record enough to make module behavior inspectable without hoarding raw payloads.

For camera perception, audit might record:

- requested by agent or user
- timestamp
- consent mode
- approved or denied
- bridge availability
- model capability result
- retention mode
- success/failure

It should not store the raw image unless the user explicitly chooses a retention path.

## Required Modules

Required modules may use the same manifest shape, but their `required` flag prevents disablement.

This gives Brain System a consistent way to describe its own subsystems without pretending every subsystem is optional.
