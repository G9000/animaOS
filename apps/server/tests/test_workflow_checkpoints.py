from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import RuntimeWorkflowCheckpoint, RuntimeWorkflowRun
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
