from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from anima_server.models.runtime import RuntimeWorkflowCheckpoint, RuntimeWorkflowRun

WorkflowStatus = Literal[
    "created",
    "running",
    "awaiting_input",
    "paused",
    "completed",
    "failed",
    "cancelled",
]

CheckpointStatus = Literal[
    "completed",
    "awaiting_input",
    "failed",
    "skipped",
]


@dataclass(frozen=True, slots=True)
class WorkflowResumePoint:
    run: RuntimeWorkflowRun
    latest_checkpoint: RuntimeWorkflowCheckpoint | None
    next_state: str | None
