---
title: Local Action Module
description: Governed local execution and automation capability for ANIMA
category: architecture
updated: 2026-06-29
---

# Local Action Module

[Back to Capability Modules](README.md)

`action.local` lets ANIMA affect the local machine.

It answers:

```text
What is ANIMA allowed to do, what requires approval, and what must be audited?
```

Action is the highest-risk family because it changes state. It can edit files, launch processes, call local tools, control applications, or trigger external effects through integrations.

## Capability Id

```text
action.local
```

Possible submodules:

- `action.filesystem`
- `action.process`
- `action.app_control`
- `action.network`
- `action.animus`

## What Action Owns

Action modules own:

- local operation schemas
- approval policy
- risk classification
- bridge requirements
- dry-run previews
- execution sandbox settings
- result normalization
- action audit events
- rollback metadata when possible

Action modules do not own:

- user identity
- permanent memory writes
- raw shell access as a default model tool
- secret exfiltration
- destructive operations without approval

## Risk Rings

Actions should be classified into risk rings.

| Ring | Meaning | Examples | Default |
| --- | --- | --- | --- |
| `observe` | Read-only local state | list a project directory | allow with scope |
| `prepare` | Produce a proposed change | generate a patch, draft a file | allow with review |
| `modify` | Change local user data | edit a file, create folder | require approval unless trusted workflow |
| `execute` | Run code or commands | start dev server, run tests | require policy and audit |
| `external` | Contact network or third-party service | fetch remote data, send webhook | require explicit approval/config |
| `destructive` | Delete, overwrite, reset, revoke | remove directory, reset branch | require strong approval |

The same tool can map to different rings depending on arguments.

## Tool Shape

The agent should receive intention-level tools where possible.

Good:

- `apply_code_patch`
- `run_project_tests`
- `open_local_file`
- `create_user_draft`

Riskier:

- `run_shell_command`
- `write_arbitrary_file`

Hidden bridge primitives may execute the final local operation, but model-visible tools should encode policy and purpose.

## Approval

Action approval should consider:

- risk ring
- target path or resource
- whether operation is reversible
- whether secrets may be involved
- whether network access is involved
- whether user initiated the workflow
- whether the current session is trusted

Approval prompts should show what will happen, not raw implementation noise.

## Memory Boundary

Action results can become memory only when meaningful.

Good candidate:

```text
User prefers PRD and ticket updates before implementation.
```

Bad candidate:

```text
Command exited with code 0 at 16:42.
```

Operational traces belong in runtime/audit. Stable preferences and important project outcomes may become memory candidates.

## Existing Surfaces

The existing agent tool executor, client-action path, and Animus-style local operations likely belong under this family over time.

This does not mean all current tools must be rewritten immediately. It means future local-action work should converge on one policy language.

## Failure Cases

Expected failures:

- action disabled
- target outside allowed scope
- approval denied
- sandbox denied
- command timed out
- process exited non-zero
- file changed concurrently
- network unavailable
- required app not running

Action failures should be precise because the user may need to decide whether to retry, approve, or change scope.

## Future Extensions

Future action work can include:

- per-project trust profiles
- reversible action journal
- dry-run previews for local automation
- app-specific controllers
- scoped network capabilities
- safer secret handling policy
- local task automations triggered by Presence
