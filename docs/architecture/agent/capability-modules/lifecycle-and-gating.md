---
title: Capability Lifecycle And Gating
description: How modules become visible, available, degraded, or disabled
category: architecture
updated: 2026-06-29
---

# Capability Lifecycle And Gating

[Back to Capability Modules](README.md)

Capability modules move through several states before the agent can use them. This is important because a module can be installed but disabled, enabled but unconfigured, configured but missing a desktop bridge, upgradeable but held back, or available but blocked by model capability.

## State Model

| State | Meaning |
| --- | --- |
| `installed` | Module exists in the server codebase |
| `enabled` | User has enabled the module |
| `configured` | Required config fields are valid |
| `bridge_connected` | Required desktop bridge is currently connected |
| `available` | Module can be used right now |
| `degraded` | Module exists but has limited function |
| `disabled` | User or policy prevents use |
| `unavailable` | Needed dependency is missing |
| `upgrade_available` | A newer compatible module version exists |
| `upgrade_required` | Current module version cannot run safely |
| `migrating` | Module config/runtime data is being migrated |
| `migration_failed` | Migration failed and the module must stay unavailable |
| `incompatible` | Module cannot run with current Brain System, contract, provider, or bridge |

These states should be explicit in UI and tool behavior.

## Version Gating

Compatibility is checked before tool visibility.

```text
module installed
AND manifest contract supported
AND module version compatible with brain.core
AND config schema is current or migration succeeded
AND required provider/bridge capabilities are satisfied or degradable
= module may continue to enablement/tool checks
```

If version gating fails, the module should not expose agent-visible tools. It should report `incompatible`, `upgrade_required`, or `migration_failed` with a user-fixable reason.

## Tool Visibility

An agent-visible tool should appear only when policy says the model can use it.

For a camera tool, the full gate is:

```text
perception.camera installed
AND compatible with brain.core
AND config migration succeeded
AND enabled
AND agent_requested_capture_enabled
AND user is unlocked
AND desktop bridge can be reached
AND current model supports images
= show/use view_camera_snapshot
```

Manual chat capture has a different gate:

```text
perception.camera installed
AND enabled
AND manual_chat_capture_enabled
AND user is unlocked
AND browser/Tauri can access camera
= show Camera in attachment menu
```

Do not collapse these gates. Manual user capture and agent-requested capture are different trust events.

## Hidden Bridge Gating

Bridge primitives are never model-visible.

They are registered by desktop and consumed by server module code:

```text
desktop registers camera_capture_frame hidden action
server perception.camera module calls hidden action
agent sees only view_camera_snapshot
```

This keeps the agent reasoning at the semantic capability level instead of the raw device-control level.

## Availability Examples

### Disabled

User has never enabled Camera Perception.

Result:

- no camera tool in agent tool list
- camera settings show disabled
- manual camera attachment hidden or disabled

### Enabled But No Bridge

User enabled Camera Perception, but desktop is closed or locked.

Result:

- capability status: unavailable
- agent tool should either be hidden or return a clear "desktop bridge unavailable" error
- no capture attempt should happen

### Enabled But Non-Vision Model

User enabled Camera Perception, desktop bridge exists, but the configured model cannot process images.

Result:

- manual chat capture can still attach images if chat supports later handling
- agent-requested analysis fails before asking for a frame
- settings should explain that a vision-capable model is required

### Enabled And Available

User enabled Camera Perception, bridge is connected, model supports images, consent mode allows request.

Result:

- `view_camera_snapshot` can be used
- consent prompt runs if configured
- raw frame is transient unless retention policy says otherwise

## Runtime Failure Rule

The agent should never hallucinate that a missing capability worked.

If a capability is unavailable, the tool result should clearly say why:

- disabled by user
- bridge unavailable
- permission denied
- model lacks vision
- capture timed out
- frame invalid

These details are useful because the user can fix them.

## Prompt Impact

Capability status can be injected into the system prompt as a compact block:

```text
Capabilities:
- brain.core: available
- memory.core: available
- perception.camera: disabled
- voice.core: unavailable; missing local STT provider
```

This prevents the model from asking for senses it does not currently have.

Keep this block short. Detailed config belongs in tools/settings, not every prompt.
