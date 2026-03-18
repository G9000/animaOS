---
name: security-auditor
description: Review code, configs, prompts, and runtime flows for exploitable security issues. Use when the user asks for a security audit, vulnerability review, AppSec review, auth/authz review, crypto review, attack-surface analysis, or hardening guidance grounded in implementation. Do not use for general code review or architecture-only threat modeling; use security-threat-model for repo-level threat models.
---

# Security Auditor

Perform an implementation-grounded security review. Prioritize real exploit paths over generic checklists, and keep evidence explicit.

## Quick Start

1. Define the scope: repo, path, diff, endpoint, feature, or workflow.
2. Trace the attack surface from entrypoint to sink.
3. Review auth, validation, execution, storage, logging, and external calls.
4. Validate exploitability, impact, and any mitigating controls.
5. Report findings first, ordered by severity, with concrete fixes.

## Review Workflow

### 1. Build the attack surface

- Identify externally reachable surfaces: routes, RPC handlers, webhooks, file uploads, background jobs, admin tools, CLI entrypoints, model/tool integrations.
- Map trust boundaries: user to app, tenant to tenant, app to DB, app to filesystem, model to tools, worker to external APIs.
- Follow sensitive data paths: input, normalization, authorization, persistence, logs, exports, and responses.

### 2. Hunt for high-signal vulnerability classes

- Auth and authorization: missing authn, broken session handling, IDOR/BOLA, tenant escapes, privilege escalation, unsafe approval bypasses.
- Input and execution: SQL/command/template injection, path traversal, unsafe deserialization, XSS, SSRF, regex/resource exhaustion, file parser abuse.
- Data handling: plaintext secrets, weak crypto, missing integrity checks, over-broad logs, unsafe temp files, backup/export leaks.
- Network and integrations: webhook spoofing, callback abuse, key leakage, insecure redirects, over-trusting upstream metadata.
- LLM and agent systems: prompt injection into tools, unsafe tool exposure, hidden-context leakage, memory poisoning, missing human approval on dangerous actions.
- Operational defaults: debug endpoints, unsafe CORS, missing rate limits, dangerous fallbacks, insecure local-dev assumptions shipping to production.

### 3. Validate before reporting

- Prefer findings with a concrete exploit story and clear preconditions.
- Distinguish pre-auth, post-auth, same-user, cross-user, and admin-impact cases.
- Check for existing mitigations before escalating severity.
- If evidence is incomplete, label it as a suspicion and say what to verify next.

## Output Contract

- Findings first, ordered by severity.
- For each finding, include: title, severity, exploit path, impact, evidence, and fix.
- Use precise file references when possible.
- After findings, list open questions or assumptions that affect confidence.
- If no findings are confirmed, state that explicitly and call out residual risk or testing gaps.

## Boundaries

- Use `security-threat-model` when the user wants architecture-wide abuse-path modeling rather than code/config auditing.
- For pull requests or diffs, focus on security regressions introduced by the changed lines.
- Avoid checklist dumps, speculative CVE hunting without code evidence, or advice that is not tied to this codebase.
