---
title: Module Authoring Guide
description: Checklist for designing and implementing production-grade Agent Capability Modules
category: architecture
updated: 2026-06-29
---

# Module Authoring Guide

[Back to Capability Modules](README.md)

Use this guide before adding a new Agent Capability Module.

A module is production-grade only when it has a contract, policy, lifecycle, tests, UI status, audit, and memory boundary. A tool function alone is not a module.

## Start With The Question

Before writing code, answer:

1. What new sense, voice, action, presence, or memory surface does this add?
2. Why does it belong inside FastAPI rather than `apps/anima-mod`?
3. What user consent is needed?
4. What raw data does it touch?
5. What can be retained?
6. What should the model see?
7. What must remain hidden from the model?
8. How does it fail?
9. What should be audited?
10. Can its outputs become memory, and through what path?

If those answers are vague, the module is not ready.

## Manifest Checklist

Every module manifest should declare:

- stable dotted `id`
- `family`
- user-facing display name
- concise description
- required or optional
- default enabled state
- config schema
- runtime status resolver
- semantic tools
- hidden bridge requirements
- provider requirements
- memory policy
- audit policy
- degradation behavior

Sensitive modules default disabled.

## Code Placement

Suggested server layout:

```text
apps/server/src/anima_server/services/agent/capabilities/
    __init__.py
    types.py
    registry.py
    builtin.py
    config.py
    status.py
    tools.py
    audit.py
    modules/
        perception_camera.py
        memory_core.py
        voice_core.py
        action_local.py
        presence_core.py
```

The exact layout can change, but the registry/contract/module split should stay.

## Tool Design Rules

Agent-visible tools should be:

- semantic
- purpose-scoped
- policy-aware
- typed
- easy to deny safely
- free of raw hardware primitives

Hidden bridge actions should be:

- unavailable to the LLM
- callable only by server module code
- typed
- timeout-bound
- audited when sensitive

## UI Requirements

Desktop should be able to display:

- module name
- enabled state
- required/optional state
- availability
- degradation reason
- config controls
- bridge state
- retention mode
- recent audit summary for sensitive modules

Required modules can be shown as locked-on rather than hidden. That helps users understand the body model.

## Test Requirements

Server tests:

- manifest validates
- defaults are conservative
- required modules cannot be disabled
- optional modules can be enabled/disabled
- config schema rejects invalid values
- status explains unavailable states
- tools appear only when allowed
- hidden bridge primitives are excluded from model tool list
- audit events omit raw payloads
- retention policy is enforced

Desktop tests:

- settings render required and optional modules
- disabled modules show disabled controls
- bridge unavailable state is visible
- consent prompts appear for sensitive requests
- manual capture and agent-requested capture stay separate

## Documentation Requirements

Each new module should have:

- architecture doc
- PRD entry or PRD update if product scope changes
- dated implementation plan if sequencing matters
- parent/child tickets
- tests listed in the ticket validation

## Definition Of Done

A module is done when:

- it has a manifest
- it has persisted or derived config
- it reports status
- it exposes only semantic model tools
- it hides bridge primitives
- it has audit behavior
- it has retention behavior
- it degrades clearly
- it has tests
- it has desktop visibility
- it is documented

Anything less is a prototype, not a production-grade body system.
