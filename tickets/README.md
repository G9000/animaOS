# Tickets

Canonical index for repo-native initiatives tracked under `tickets/`.

## Active Initiatives

- [Capability Module Standard](./agent-capability-modules/ACM-000-parent.md) (`backlog`; [overview](./agent-capability-modules/README.md))
- [Camera Perception Capability Module](./camera-perception/CAM-000-parent.md) (`in_progress`; [overview](./camera-perception/README.md))
- [Gateway Runtime Online](./gateway-runtime-online/GWR-000-parent.md) (`backlog`; [overview](./gateway-runtime-online/README.md))
- [Inner Life v1](./inner-life-v1/IL-000-parent.md) (`backlog`; [overview](./inner-life-v1/README.md))
- [Local Turso Core and Runtime](./local-turso-core-runtime/LTR-000-parent.md) (`backlog`; [overview](./local-turso-core-runtime/README.md))
- [Memory Atlas Graph](./memory-atlas-graph/MAG-000-parent.md) (`backlog`; [overview](./memory-atlas-graph/README.md))
- [Memory Package Boundary Hardening](./memory-package-boundary/MPB-000-parent.md) (`backlog`; [overview](./memory-package-boundary/README.md))
- [Portable Core Filesystem](./portable-core-filesystem/PCF-000-portable-core-filesystem.md) (`in_progress`; [overview](./portable-core-filesystem/README.md))
- [Production Document Processing](./production-document-processing/PDP-000-production-document-processing.md) (`backlog`; [overview](./production-document-processing/README.md))
- [Social Memory Identity Discovery](./social-memory-identity-discovery/SID-000-parent.md) (`backlog`; [overview](./social-memory-identity-discovery/README.md))
- [Voice Foundation v1](./voice-foundation-v1/VCE-000-parent.md) (`backlog`; [overview](./voice-foundation-v1/README.md))

## Completed Initiatives

- [Agent Runtime Hardening](./agent-runtime-hardening/ARH-000-parent.md) (`done`; [overview](./agent-runtime-hardening/README.md))
- [Animus Rust Coding TUI](./animus-coding-tui/ACT-000-parent.md) (`done`; [overview](./animus-coding-tui/README.md))
- [Local Runtime Daemon](./local-runtime-daemon/LRD-000-parent.md) (`done`; [overview](./local-runtime-daemon/README.md))
- [OKF LLM Wiki Ingestion](./okf-llm-wiki-ingestion/OKF-000-okf-llm-wiki-ingestion.md) (`done`; [overview](./okf-llm-wiki-ingestion/README.md))
- [Repo Workflow](./repo-workflow/RWF-000-parent.md) (`done`; [overview](./repo-workflow/README.md))
- [Single-User Temporal Memory v2](./single-user-temporal-memory-v2/SUM-000-parent.md) (`done`; [overview](./single-user-temporal-memory-v2/README.md))
- [Visual Memory Image Assets](./visual-memory-image-assets/VMI-000-parent.md) (`done`; [overview](./visual-memory-image-assets/README.md))

## Legacy or Unclassified

- [agent-server-audit-remediation](./agent-server-audit-remediation/) has no conforming parent tracker.

## Conventions

- Ticket IDs are local to this repo.
- Each initiative should have one parent tracker ticket plus child tickets.
- `P1` means must-do foundation or security work.
- `P2` means important follow-on work.
- `P3` means expansion work after the boundary is stable.
- Use [TEMPLATE.md](./TEMPLATE.md) for new tickets.
- Follow [prd-ticket-workflow.md](../docs/ops/prd-ticket-workflow.md) for pickup, status, timestamps, and completion rules.
- `bun run check:repo` is the mechanical consistency check being added by `RWF-003`.
