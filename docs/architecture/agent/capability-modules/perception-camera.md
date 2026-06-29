---
title: Camera Perception Module
description: First concrete Perception module for one-frame webcam sight
category: architecture
updated: 2026-06-29
---

# Camera Perception Module

[Back to Capability Modules](README.md)

`perception.camera` is the first concrete Agent Capability Module.

It gives ANIMA a controlled visual sense through the local desktop camera. It is not a default feature, not background monitoring, and not continuous video. It is a one-frame, consented perception capability.

## Why Camera Is A Module

Camera access is intimate. It can expose rooms, faces, bystanders, documents, medicine bottles, family photos, and private context the user did not intend to store.

So camera perception must be optional, visible, and policy-governed.

It belongs in the Perception family because it gives ANIMA a sense, not because it is a generic external service integration.

## Capability Id

```text
perception.camera
```

User-facing name:

```text
Camera Perception
```

Agent-facing tool:

```text
view_camera_snapshot
```

Hidden desktop bridge primitive:

```text
camera_capture_frame
```

## User Flows

### Manual Chat Capture

The user chooses Camera from the chat attachment menu.

Flow:

1. Desktop checks `perception.camera` is enabled.
2. Desktop checks `manual_chat_capture_enabled`.
3. Desktop asks OS/browser for camera permission if needed.
4. User captures one frame.
5. Frame appears in pending attachments.
6. User sends it like any other chat image.

This is a deliberate user-provided image, so it follows normal chat image retention.

### Agent-Requested Snapshot

ANIMA asks to look only when the capability and policy allow it.

Flow:

1. Agent calls `view_camera_snapshot(question, purpose)`.
2. Server checks `perception.camera` is enabled.
3. Server checks agent-requested capture is enabled.
4. Server checks model supports image input.
5. Server asks desktop bridge for one frame.
6. Desktop prompts user if consent mode requires it.
7. Desktop captures one frame and returns transient bytes.
8. Server analyzes frame with vision model.
9. Server deletes raw temp frame.
10. Agent receives text perception report.

The main agent should not receive raw base64 image data.

## Configuration

| Field | Default | Meaning |
| --- | --- | --- |
| `agent_requested_capture_enabled` | `false` | Whether the agent may request a frame |
| `manual_chat_capture_enabled` | `true` | Whether user-triggered camera attachments are available |
| `consent_mode` | `ask_each_time` | How agent-requested capture is approved |
| `retention_mode` | `transient_only` | Raw frame retention |
| `max_width` | `1280` | Capture scaling limit |
| `max_height` | `720` | Capture scaling limit |
| `quality` | `0.86` | JPEG quality |
| `audit_enabled` | `true` | Record perception events without raw bytes |

Defaults should be conservative. In particular, agent-requested capture starts disabled.

## Semantic Tool Contract

`view_camera_snapshot` should accept:

| Argument | Purpose |
| --- | --- |
| `question` | What ANIMA is trying to inspect |
| `purpose` | Why the frame is needed |

Tool output should include:

- `status`
- `source`
- `retention`
- `captured_at`
- `dimensions`
- `analysis`
- optional `error`

The analysis should be concise and task-relevant.

## Vision Prompt Rules

The vision-analysis prompt should tell the model:

- describe visible, task-relevant details
- do not identify people
- do not infer protected traits
- do not perform face recognition
- do not guess emotions from faces
- mention uncertainty when unclear

Camera perception should help ANIMA understand context, not become a biometric classifier.

## Retention

Default retention:

```text
transient_only
```

Agent-requested frames:

- raw bytes are temporary
- server may create a temp file only for provider serialization
- temp file is deleted in `finally`
- analysis text may appear in runtime tool trace
- durable memory requires explicit memory path

Manual chat frames:

- treated as user-sent image attachments
- stored through the existing chat image pipeline
- future visual-memory indexing should use the visual asset pipeline

## Audit

Audit should record:

- request source: agent or manual
- timestamp
- consent mode
- approved or denied
- bridge status
- model capability result
- retention mode
- success/failure

Audit should not record:

- raw image bytes
- base64 payload
- full frame path after deletion

## Failure Cases

| Failure | Expected behavior |
| --- | --- |
| Capability disabled | Tool hidden or clear disabled error |
| Agent capture disabled | Tool hidden or policy error |
| Desktop not connected | Bridge unavailable error |
| User denies consent | Normal denied result |
| Camera permission denied | Permission error |
| Model lacks vision | Fail before requesting frame |
| Frame too large | Reject payload |
| Invalid MIME | Reject payload |
| Vision model empty response | Return captured-but-no-description warning |

## Future Extensions

Possible later work:

- selected camera device
- capture preview before send
- visual memory candidate mode
- object/text extraction when supported
- screen/window perception sibling modules
- wearable or mobile camera bridges

Do not add continuous background capture until the consent, audit, and retention model is much stronger.
