# Agent Capability Modules Implementation Plan

**Goal:** Add the internal FastAPI-side Capability Module standard: the base factory/contract that lets future ANIMA capability parts register, configure, expose tools, connect bridges, audit use, and report status.

**Architecture:** The standard lives under `apps/server/src/anima_server/services/agent/capabilities/`. A registry loads module manifests, config, runtime availability, tool schemas, bridge requirements, retention rules, and audit policy. Desktop reads this registry to configure modules and provide local bridges.

**Doctrine:** One self, modular body, governed body systems. Modules extend the same ANIMA identity; they do not create separate agents or bypass Memory Core.

**Upgrade model:** Brain System and Capability Modules are versioned parts around the Portable Core. The Core carries identity; runtime/modules can update around it after compatibility and migration checks.

**Boundary:** This plan builds the module standard first. Camera, Voice, Action, Presence, and Memory-specific implementations are consumers of the standard and should live in their own workstreams.

## Reference Docs

- [Agent Capability Modules](../../architecture/agent/capability-modules/README.md)
- [Body System Doctrine](../../architecture/agent/capability-modules/body-system-doctrine.md)
- [Capability Runtime Flow](../../architecture/agent/capability-modules/runtime-flow.md)
- [Capability Data Boundaries](../../architecture/agent/capability-modules/data-boundaries.md)
- [Module Authoring Guide](../../architecture/agent/capability-modules/module-authoring-guide.md)
- [External Integration Boundary](../../architecture/system/external-integration-boundary.md)
- [Upgrade And Compatibility Model](../../architecture/agent/capability-modules/upgrade-and-compatibility.md)

## Phase 1: Module Contract And Registry Foundation

Files:

- Create: `apps/server/src/anima_server/services/agent/capabilities/types.py`
- Create: `apps/server/src/anima_server/services/agent/capabilities/registry.py`
- Create: `apps/server/src/anima_server/services/agent/capabilities/builtin.py`
- Test: `apps/server/tests/test_agent_capabilities.py`

Contract fields:

- `id`
- `version`
- `contract_version`
- `family`
- `display_name`
- `description`
- `required`
- `default_enabled`
- `min_brain_version`
- `max_brain_version`
- `config_schema_version`
- `config_schema`
- `tool_schemas`
- `bridge_requirements`
- `provider_requirements`
- `memory_policy`
- `audit_policy`
- `migrations`
- `degradation_behavior`

Acceptance:

- Registry can load and validate capability manifests.
- Registry supports simple built-in/reference manifests for tests.
- Manifest contract does not require implementing Camera, Voice, Action, Presence, or Memory modules.
- Manifest validation rejects hidden bridge primitives as agent-visible tools.
- Manifest validation rejects incompatible module/Brain System versions.

## Phase 2: Config And Status Store

Files:

- Create: `apps/server/src/anima_server/services/agent/capabilities/config.py`
- Create or modify runtime model/migration if persistent runtime config is needed.
- Create API route: `apps/server/src/anima_server/api/routes/capabilities.py`
- Test: `apps/server/tests/test_capabilities_api.py`

Acceptance:

- Desktop can read module list/status.
- Desktop can enable/disable optional modules once concrete modules exist.
- Config updates validate against schema.
- Disabled modules expose no tools.
- Incompatible, migration-failed, and upgrade-required modules expose no tools.

## Phase 2b: Versioning And Migration Hooks

Files:

- Modify: `apps/server/src/anima_server/services/agent/capabilities/types.py`
- Modify: `apps/server/src/anima_server/services/agent/capabilities/registry.py`
- Create: `apps/server/src/anima_server/services/agent/capabilities/migrations.py`
- Test: `apps/server/tests/test_capability_compatibility.py`

Acceptance:

- Registry checks `contract_version`, `min_brain_version`, and `max_brain_version`.
- Registry reports `incompatible`, `upgrade_required`, and `migration_failed` statuses.
- Config schema version is tracked per module.
- No-op migration hooks exist for built-in modules.
- Failed migrations preserve previous module config.

## Phase 3: Tool Gating

Files:

- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Create: `apps/server/src/anima_server/services/agent/capabilities/tools.py`
- Test: `apps/server/tests/test_agent_capability_tools.py`

Acceptance:

- Agent-visible tools are assembled from enabled modules.
- Lower-level bridge primitives remain hidden.
- Missing bridge/model capability produces clear tool errors.
- Capability status is compactly available to prompt assembly.

## Phase 4: Desktop Module Controls

Files:

- Create: `apps/desktop/src/lib/capabilities.ts`
- Create: `apps/desktop/src/pages/settings/CapabilitySettings.tsx`
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/pages/settings/Settings.tsx`

Acceptance:

- User can see module-standard status and any registered modules.
- Optional modules expose config forms once concrete modules exist.
- Per-module status explains unavailable/degraded/incompatible states.

## Phase 5: Bridge Channel

Files:

- Modify: `apps/server/src/anima_server/services/agent/client_actions.py`
- Create: `apps/desktop/src/lib/capability-bridge.ts`
- Test: server and desktop focused bridge tests.

Acceptance:

- Desktop can register hidden bridge actions for enabled modules.
- Hidden bridge actions are callable only by server module code.
- Hidden bridge actions are not advertised directly to the LLM.

## Phase 6: Audit And Retention

Files:

- Create: `apps/server/src/anima_server/services/agent/capabilities/audit.py`
- Add runtime model/migration if audit needs persistence.
- Test: `apps/server/tests/test_capability_audit.py`

Acceptance:

- Module use can be audited without raw sensitive payloads.
- Retention policy is explicit per module.
- Sensitive module outputs default to transient/runtime, not soul.
- Durable learning from module outputs enters through Memory Core and Soul Writer.

## Later Consumer Workstreams

Concrete modules should be implemented after the base standard exists.

Examples:

- Camera Perception: [Camera Perception PRD](../../prds/perception/camera-perception-v1.md), [Camera Perception Plan](2026-06-29-camera-perception.md)
- Voice Core
- Local Action
- Presence
- Memory status/compatibility surface

## Validation

- `bun run test:server`
- `bun run build:desktop`
- focused server tests for registry/config/tool gating
- focused desktop tests for settings rendering

## Rollout

- Ship registry with optional modules disabled by default.
- Keep existing memory/chat behavior stable.
- Add concrete capability modules only after the base module standard is in place.
