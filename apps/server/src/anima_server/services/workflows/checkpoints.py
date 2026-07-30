from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeWorkflowCheckpoint, RuntimeWorkflowRun
from anima_server.services.corefs.sealed_runtime import seal_runtime_fields
from anima_server.services.workflows.state import (
    CheckpointStatus,
    WorkflowResumePoint,
)

JsonObject = dict[str, Any]
TERMINAL_WORKFLOW_STATUSES = frozenset({"completed", "failed", "cancelled"})


def start_workflow(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None = None,
    workflow_type: str,
    input_json: JsonObject | None = None,
    max_retries: int = 3,
) -> RuntimeWorkflowRun:
    run = RuntimeWorkflowRun(
        user_id=user_id,
        thread_id=thread_id,
        workflow_type=workflow_type,
        status="created",
        current_state="created",
        input_json=None,
        result_json=None,
        retry_count=0,
        max_retries=max_retries,
    )
    seal_runtime_fields(
        db,
        row=run,
        row_type="runtime_workflow_run",
        owner_id=user_id,
        payload={"input_json": input_json, "result_json": None},
        placeholders={"input_json": None, "result_json": None},
    )
    return run


def append_checkpoint(
    db: Session,
    *,
    workflow_run_id: int,
    state_name: str,
    status: CheckpointStatus,
    idempotency_key: str,
    input_json: JsonObject | None = None,
    output_json: JsonObject | None = None,
    artifact_refs_json: JsonObject | None = None,
    error_json: JsonObject | None = None,
    pause_on_failure: bool = False,
) -> RuntimeWorkflowCheckpoint:
    run = db.get(RuntimeWorkflowRun, workflow_run_id)
    if run is None:
        raise ValueError(f"Workflow run {workflow_run_id} does not exist.")

    existing = db.scalar(
        select(RuntimeWorkflowCheckpoint).where(
            RuntimeWorkflowCheckpoint.workflow_run_id == workflow_run_id,
            RuntimeWorkflowCheckpoint.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing

    if run.status in TERMINAL_WORKFLOW_STATUSES:
        raise ValueError(
            f"Workflow run {workflow_run_id} is terminal ({run.status}); "
            "cannot append a new checkpoint."
        )

    latest_index = db.scalar(
        select(func.max(RuntimeWorkflowCheckpoint.checkpoint_index)).where(
            RuntimeWorkflowCheckpoint.workflow_run_id == workflow_run_id
        )
    )
    checkpoint = RuntimeWorkflowCheckpoint(
        workflow_run_id=workflow_run_id,
        checkpoint_index=int(latest_index or 0) + 1,
        state_name=state_name,
        status=status,
        input_json=None,
        output_json=None,
        artifact_refs_json=artifact_refs_json,
        idempotency_key=idempotency_key,
        error_json=error_json,
    )
    seal_runtime_fields(
        db,
        row=checkpoint,
        row_type="runtime_workflow_checkpoint",
        owner_id=int(run.user_id),
        payload={"input_json": input_json, "output_json": output_json},
        placeholders={"input_json": None, "output_json": None},
    )

    _apply_checkpoint_status(
        run,
        state_name=state_name,
        status=status,
        error_json=error_json,
        pause_on_failure=pause_on_failure,
    )
    db.add(run)
    db.flush()
    return checkpoint


def load_resume_point(
    db: Session,
    *,
    workflow_run_id: int,
) -> WorkflowResumePoint | None:
    run = db.get(RuntimeWorkflowRun, workflow_run_id)
    if run is None:
        return None

    latest_checkpoint = db.scalar(
        select(RuntimeWorkflowCheckpoint)
        .where(
            RuntimeWorkflowCheckpoint.workflow_run_id == workflow_run_id,
            RuntimeWorkflowCheckpoint.status == "completed",
        )
        .order_by(desc(RuntimeWorkflowCheckpoint.checkpoint_index))
        .limit(1)
    )
    return WorkflowResumePoint(
        run=run,
        latest_checkpoint=latest_checkpoint,
        next_state=None,
    )


def mark_workflow_awaiting_input(
    db: Session,
    run: RuntimeWorkflowRun,
    *,
    state_name: str | None = None,
    result_json: JsonObject | None = None,
) -> RuntimeWorkflowRun:
    run.status = "awaiting_input"
    if state_name is not None:
        run.current_state = state_name
    if result_json is not None:
        seal_runtime_fields(
            db,
            row=run,
            row_type="runtime_workflow_run",
            owner_id=int(run.user_id),
            payload={"input_json": run.input_json, "result_json": result_json},
            placeholders={"input_json": None, "result_json": None},
        )
    run.updated_at = datetime.now(UTC)
    db.add(run)
    db.flush()
    return run


def mark_workflow_completed(
    db: Session,
    run: RuntimeWorkflowRun,
    *,
    result_json: JsonObject | None = None,
) -> RuntimeWorkflowRun:
    now = datetime.now(UTC)
    run.status = "completed"
    if result_json is not None:
        seal_runtime_fields(
            db,
            row=run,
            row_type="runtime_workflow_run",
            owner_id=int(run.user_id),
            payload={"input_json": run.input_json, "result_json": result_json},
            placeholders={"input_json": None, "result_json": None},
        )
    run.completed_at = now
    run.updated_at = now
    db.add(run)
    db.flush()
    return run


def mark_workflow_failed(
    db: Session,
    run: RuntimeWorkflowRun,
    *,
    error_json: JsonObject | None = None,
) -> RuntimeWorkflowRun:
    now = datetime.now(UTC)
    run.status = "failed"
    if error_json is not None:
        run.error_json = error_json
    run.completed_at = now
    run.updated_at = now
    db.add(run)
    db.flush()
    return run


def cancel_workflow(db: Session, run: RuntimeWorkflowRun) -> RuntimeWorkflowRun:
    now = datetime.now(UTC)
    run.status = "cancelled"
    run.completed_at = now
    run.updated_at = now
    db.add(run)
    db.flush()
    return run


def _apply_checkpoint_status(
    run: RuntimeWorkflowRun,
    *,
    state_name: str,
    status: CheckpointStatus,
    error_json: JsonObject | None,
    pause_on_failure: bool,
) -> None:
    now = datetime.now(UTC)

    if status == "completed":
        run.status = "running"
        run.current_state = state_name
        run.started_at = run.started_at or now
    elif status == "awaiting_input":
        run.status = "awaiting_input"
        run.current_state = state_name
    elif status == "failed":
        run.status = "paused" if pause_on_failure else "failed"
        run.current_state = state_name
        if error_json is not None:
            run.error_json = error_json
        if not pause_on_failure:
            run.completed_at = now

    run.updated_at = now
