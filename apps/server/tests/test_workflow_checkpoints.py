from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import RuntimeWorkflowCheckpoint, RuntimeWorkflowRun
from anima_server.services.workflows.checkpoints import (
    append_checkpoint,
    load_resume_point,
    mark_workflow_completed,
    start_workflow,
)
from sqlalchemy import inspect

pytest_plugins = ("conftest_runtime",)


def test_workflow_tables_registered(runtime_engine):
    RuntimeBase.metadata.create_all(runtime_engine)
    names = set(inspect(runtime_engine).get_table_names())
    assert RuntimeWorkflowRun.__tablename__ in names
    assert RuntimeWorkflowCheckpoint.__tablename__ in names


def test_workflow_run_status_fields_have_python_and_server_defaults():
    status = RuntimeWorkflowRun.__table__.c.status
    current_state = RuntimeWorkflowRun.__table__.c.current_state

    assert status.default is not None
    assert status.default.arg == "created"
    assert str(status.server_default.arg) == "'created'"

    assert current_state.default is not None
    assert current_state.default.arg == "created"
    assert str(current_state.server_default.arg) == "'created'"


def test_start_workflow_creates_created_run(runtime_db):
    run = start_workflow(
        runtime_db,
        user_id=7,
        thread_id=None,
        workflow_type="checkpoint_rag",
        input_json={"question": "where did the plan pause?"},
        max_retries=5,
    )

    assert run.id is not None
    assert run.user_id == 7
    assert run.thread_id is None
    assert run.workflow_type == "checkpoint_rag"
    assert run.status == "created"
    assert run.current_state == "created"
    assert run.input_json == {"question": "where did the plan pause?"}
    assert run.max_retries == 5


def test_append_checkpoint_advances_state(runtime_db):
    run = start_workflow(
        runtime_db,
        user_id=7,
        workflow_type="checkpoint_rag",
    )

    checkpoint = append_checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="retrieve_context",
        status="completed",
        idempotency_key="retrieve-context-1",
        input_json={"query": "workflow checkpoint"},
        output_json={"matches": [1, 2]},
        artifact_refs_json={"memory_ids": [1, 2]},
    )

    assert checkpoint.id is not None
    assert checkpoint.workflow_run_id == run.id
    assert checkpoint.checkpoint_index == 1
    assert checkpoint.state_name == "retrieve_context"
    assert checkpoint.status == "completed"
    assert checkpoint.input_json == {"query": "workflow checkpoint"}
    assert checkpoint.output_json == {"matches": [1, 2]}
    assert checkpoint.artifact_refs_json == {"memory_ids": [1, 2]}
    assert run.status == "running"
    assert run.current_state == "retrieve_context"


def test_append_checkpoint_is_idempotent_by_key(runtime_db):
    run = start_workflow(runtime_db, user_id=7, workflow_type="checkpoint_rag")

    first = append_checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="retrieve_context",
        status="completed",
        idempotency_key="same-key",
        output_json={"value": "first"},
    )
    second = append_checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="different_state",
        status="failed",
        idempotency_key="same-key",
        output_json={"value": "second"},
        error_json={"message": "should not be written"},
    )

    checkpoints = runtime_db.query(RuntimeWorkflowCheckpoint).all()
    assert second is first
    assert len(checkpoints) == 1
    assert checkpoints[0].checkpoint_index == 1
    assert checkpoints[0].state_name == "retrieve_context"
    assert checkpoints[0].status == "completed"
    assert checkpoints[0].output_json == {"value": "first"}


def test_load_resume_point_returns_latest_completed_checkpoint(runtime_db):
    run = start_workflow(runtime_db, user_id=7, workflow_type="checkpoint_rag")
    first = append_checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="retrieve_context",
        status="completed",
        idempotency_key="retrieve-context-1",
    )
    append_checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="await_user",
        status="awaiting_input",
        idempotency_key="await-user-1",
    )
    latest_completed = append_checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="rerank_context",
        status="completed",
        idempotency_key="rerank-context-1",
    )
    append_checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="summarize",
        status="failed",
        idempotency_key="summarize-1",
    )

    resume_point = load_resume_point(runtime_db, workflow_run_id=run.id)

    assert resume_point is not None
    assert resume_point.run is run
    assert resume_point.latest_checkpoint is latest_completed
    assert resume_point.latest_checkpoint is not first
    assert resume_point.next_state is None


def test_failed_checkpoint_marks_workflow_failed(runtime_db):
    run = start_workflow(runtime_db, user_id=7, workflow_type="checkpoint_rag")

    checkpoint = append_checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="generate_answer",
        status="failed",
        idempotency_key="generate-answer-1",
        error_json={"message": "model unavailable"},
    )

    assert checkpoint.error_json == {"message": "model unavailable"}
    assert run.status == "failed"
    assert run.current_state == "generate_answer"


def test_mark_workflow_completed_sets_completed_at(runtime_db):
    run = start_workflow(runtime_db, user_id=7, workflow_type="checkpoint_rag")

    completed = mark_workflow_completed(
        runtime_db,
        run,
        result_json={"answer": "resume from checkpoint 2"},
    )

    assert completed is run
    assert run.status == "completed"
    assert run.result_json == {"answer": "resume from checkpoint 2"}
    assert run.completed_at is not None
