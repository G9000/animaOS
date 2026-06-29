# Camera Perception Capability Module Implementation Plan

**Goal:** Build camera perception as an optional FastAPI-side **Agent Capability Module**, not a required core capability. The first perception module is `perception.camera`, which enables manual chat snapshots and consented agent-requested one-frame perception.

**Architecture:** This is a client-assisted capability module. The FastAPI agent service owns module lifecycle/config/policy/tool gating. Desktop owns camera permission and one-frame capture. The server-side perception host owns transient vision analysis and memory boundary. The agent sees a tool only when the capability is enabled.

## Name And Boundaries

- Capability family: **Perception**
- Built-in module: **Camera Perception**
- Module id: `perception.camera`
- Agent tool: `view_camera_snapshot`
- Hidden desktop bridge action: `camera_capture_frame`

The camera itself is not a mandatory brain-core feature. Any generic code added to core should be named as a capability registry, perception host, or client-sensor bridge, not camera-specific product behavior.

## File Map

### PRD / Tickets

- Modify: `docs/prds/perception/camera-perception-v1.md`
- Modify: `tickets/camera-perception/*`

### Python Server

- Create: `apps/server/src/anima_server/services/agent/capabilities/perception_camera.py`
- Modify: `apps/server/src/anima_server/services/agent/capabilities/builtin.py`
- Modify: `apps/server/src/anima_server/services/agent/capabilities/registry.py`
- Create: `apps/server/src/anima_server/services/agent/perception.py`
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Modify: `apps/server/src/anima_server/services/agent/client_actions.py`
- Test: `apps/server/tests/test_camera_perception.py`

### Desktop

- Create: `apps/desktop/src/lib/perception-camera.ts`
- Create: `apps/desktop/src/context/PerceptionContext.tsx`
- Create: `apps/desktop/src/pages/settings/CapabilitySettings.tsx` or extend it if created by the capability-module work
- Modify: `apps/desktop/src/pages/chat/Chat.tsx`
- Modify: `packages/standard-templates/src/composed/AttachMenu.tsx`
- Modify: `packages/standard-templates/src/composed/PromptInput.tsx`
- Test: `apps/desktop/tests/perception-camera.test.ts`

## Phase 1: Planning And Naming

- [x] Reframe feature as optional Agent Capability Module.
- [x] Choose `perception.camera` as the first perception module id.
- [x] Define product boundaries: one-frame capture, opt-in, no face recognition, no always-on monitoring.
- [ ] Resolve existing WIP from the earlier core-first draft before implementation proceeds.

## Phase 2: Capability Contract

Add `perception.camera` as a built-in capability manifest under the FastAPI agent service, even though actual camera bytes come from desktop.

The module should declare:

- config schema for capture and retention policy
- setup/help copy explaining camera permission and consent modes
- enabled state consumed by desktop bridge and server tool gating
- tool metadata for `view_camera_snapshot`
- bridge requirement for `camera_capture_frame`
- retention and audit policy

## Phase 3: Desktop Sensor Bridge

Desktop implements the actual camera access.

Required behavior:

- Only runs while authenticated/unlocked.
- Reads `perception.camera` enabled/config state.
- Registers a hidden action named `camera_capture_frame` over the existing `/ws/agent` action channel, or a dedicated capability bridge if that path grows.
- Does not advertise `camera_capture_frame` directly to the LLM.
- Implements consent UI for `ask_each_time`.
- Captures one still frame with max dimensions and JPEG quality from capability config.
- Returns base64 image data only to the server-side perception host.
- Shows visible camera activity state during capture.

Manual chat capture:

- Adds Camera to attachment menu when `manualChatCaptureEnabled` is true.
- Captures one frame and places it in the pending chat attachment tray.
- Sends through the existing image attachment path.

## Phase 4: Server Perception Host

Server implements a generic perception boundary.

Required behavior:

- Tool is visible only when `perception.camera` is enabled.
- Non-vision models fail before any capture request.
- Agent calls `view_camera_snapshot(question, purpose)`.
- Server asks desktop bridge for one frame.
- Server validates MIME, size, and magic bytes.
- Server writes a temp file only because current provider serializers consume image paths.
- Server sends the frame to the configured vision model with a strict perception prompt.
- Server deletes the temp file in `finally`.
- Tool returns JSON/text with `status`, `retention`, dimensions, timestamp, and concise analysis.

The raw desktop primitive remains hidden. The main agent never receives raw base64 unless a future deliberate debugging mode is added.

## Phase 5: Audit, Retention, And Memory

Add a lightweight audit trail before calling the feature production-ready.

Record:

- requested by agent/manual chat
- consent mode
- approved or denied
- timestamp
- result status
- retention mode
- no raw bytes

Memory rules:

- `transient_only` is v1 default.
- Manual chat snapshots are retained as normal chat attachments.
- Agent-requested frames are deleted after analysis.
- Future `visual_memory_candidate` retention should integrate with Visual Memory Image Assets.

## Phase 6: Validation

Server tests:

- hidden action schemas are not model-visible
- async tools execute correctly
- non-vision model fails before desktop capture
- invalid image payload is rejected
- temp frame deletion happens after analysis

Desktop tests:

- default settings disable agent-requested capture
- ask-each-time denial returns an error result
- capture helper scales dimensions
- manual capture creates a pending image attachment

Capability tests:

- `perception.camera` declares expected config schema
- help/setup copy exists
- enabled config defaults are safe

Commands:

- `bun run build:desktop`
- `bun run test:server` or focused pytest for perception tests

## Rollout Notes

- Ship disabled by default.
- Existing users see no camera tool until they enable the capability.
- If the desktop bridge is unavailable, the tool should say the Camera Perception capability is enabled but no unlocked desktop bridge is connected.
- If the model is not vision-capable, settings should show that camera perception can capture but agent analysis requires a vision model.
