"""Inner Life: deterministic affect/drive dynamics for continuous presence.

Everything under this package is pure `(state, event, Δt) → state` arithmetic.
Side effects (DB reads/writes, notifications) live at the edges: `store.py`
and the wiring call sites in `services/agent/consolidation.py` and
`services/agent/proactive.py`.
"""

from __future__ import annotations
