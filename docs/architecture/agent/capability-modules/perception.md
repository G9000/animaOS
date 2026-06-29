---
title: Perception Modules
description: How ANIMA gains governed senses such as camera, screen, window, and media perception
category: architecture
updated: 2026-06-29
---

# Perception Modules

[Back to Capability Modules](README.md)

Perception Modules give ANIMA senses.

They answer the question:

```text
What can ANIMA observe, under what consent, and for how long?
```

Perception is not surveillance. It is query-scoped, consented sensing that helps ANIMA understand the current situation.

## Initial Modules

| Module id | Purpose | Status |
| --- | --- | --- |
| `perception.camera` | One-frame webcam sight through desktop bridge | first implementation target |
| `perception.screen` | Explicit screen or region inspection | future |
| `perception.window` | Explicit app/window inspection | future |
| `perception.media` | User-provided image/video/audio analysis | future |

## What Perception Owns

Perception modules own:

- sensor-specific consent rules
- bridge requirements
- payload validation
- provider capability checks
- semantic observation prompts
- raw payload retention rules
- perception audit events
- memory candidate policy for observations

Perception modules do not own:

- core identity
- durable memory writes
- direct model access to raw sensors
- continuous background capture by default
- face recognition or protected-trait inference

## Sensing Modes

| Mode | Meaning | Example |
| --- | --- | --- |
| Manual attachment | User intentionally sends a visual artifact | Camera snapshot attached in chat |
| Agent-requested snapshot | ANIMA asks to inspect a current scene | "Can I look at the camera to check the cable?" |
| Selected region | User chooses a screen/window/area | "Look at this error dialog" |
| Ambient perception | Sensor runs in background | out of scope for v1 |

v1 should prioritize manual and agent-requested one-shot perception. Background perception needs a separate privacy and product review.

## Tool Shape

Perception tools should be semantic.

Good:

- `view_camera_snapshot(question, purpose)`
- `inspect_screen_region(question, purpose, region_hint)`
- `describe_attached_image(question, attachment_id)`

Bad:

- `camera_capture_frame`
- `screenshot_desktop_raw`
- `get_video_stream`

Hidden bridge primitives can exist, but the LLM should not call them directly.

## Observation Output

Perception output should be task-relevant and modest.

Good output:

```text
The visible desk area has a laptop, an external keyboard, and a cable plugged into the left side. I cannot tell whether the cable is fully seated from this angle.
```

Bad output:

```text
I see Julio in his room and he looks tired.
```

Perception should describe visible context, avoid identity guesses, and avoid emotional claims from appearance alone.

## Consent

Perception consent should be explicit and local.

Suggested consent modes:

| Mode | Meaning |
| --- | --- |
| `manual_only` | User can attach images, agent cannot request capture |
| `ask_each_time` | Agent may request, desktop prompts every time |
| `allow_during_session` | User grants temporary permission for active session |
| `disabled` | Module cannot capture |

Do not add always-on perception as a default mode.

## Memory Boundary

Perception can produce three kinds of information:

1. Raw payload: image, screenshot, video frame.
2. Observation: text description of what was visible.
3. Inference: possible meaning or relevance.

Default retention:

- raw payload: transient
- observation: current turn/runtime
- inference: current turn/runtime
- durable memory: memory candidate only when meaningful and policy allows

## Provider Requirements

Perception modules may need model capability checks.

Examples:

- vision input support
- max image size
- supported MIME types
- local-only provider preference
- safety constraints for visual analysis

The module should fail before capturing a sensor payload if the current model cannot use it.

## Future Extensions

Future perception can include:

- screen/window perception
- document camera mode
- OCR as a local preprocessor
- visual memory candidates for stable, user-approved context
- wearable or mobile camera bridges
- accessibility-oriented UI inspection

Each extension should preserve the same doctrine: one-shot by default, consented, audited, and memory-gated.
