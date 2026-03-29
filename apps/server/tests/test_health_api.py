from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import managed_test_client

from anima_server.services.health.models import CheckResult, HealthReport, HealthStatus


def _make_check_result(
    name: str, status: HealthStatus = "healthy", message: str = "OK"
) -> CheckResult:
    return CheckResult(
        name=name,
        status=status,
        message=message,
        details={},
        duration_ms=1.0,
    )


def _make_report(status: HealthStatus = "healthy") -> HealthReport:
    checks = {
        "db_integrity": _make_check_result("db_integrity"),
        "llm_connectivity": _make_check_result("llm_connectivity"),
        "background_tasks": _make_check_result("background_tasks"),
    }
    return HealthReport(status=status, checks=checks)


def _mock_session(user_id: int = 1) -> MagicMock:
    session = MagicMock()
    session.user_id = user_id
    return session


def test_health_detailed_returns_report_structure() -> None:
    mock_report = _make_report("healthy")

    async def fake_run_all(user_id: int) -> HealthReport:
        return mock_report

    with managed_test_client("anima-health-api-", invalidate_agent=False) as client:
        with patch(
            "anima_server.api.routes.health.require_unlocked_session",
            return_value=_mock_session(),
        ), patch(
            "anima_server.api.routes.health.get_default_registry"
        ) as mock_get_registry:
            mock_registry = AsyncMock()
            mock_registry.run_all = AsyncMock(return_value=mock_report)
            mock_get_registry.return_value = mock_registry

            response = client.get("/api/health/detailed")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"
        assert "checks" in data
        assert isinstance(data["checks"], dict)
        assert "db_integrity" in data["checks"]
        assert "llm_connectivity" in data["checks"]
        assert "background_tasks" in data["checks"]
        # Each check should have the expected fields
        for check in data["checks"].values():
            assert "name" in check
            assert "status" in check
            assert "message" in check
            assert "details" in check
            assert "duration_ms" in check


def test_health_check_one_returns_single_check() -> None:
    mock_result = _make_check_result("db_integrity", "healthy", "SQLite OK")

    with managed_test_client("anima-health-api-", invalidate_agent=False) as client:
        with patch(
            "anima_server.api.routes.health.require_unlocked_session",
            return_value=_mock_session(),
        ), patch(
            "anima_server.api.routes.health.get_default_registry"
        ) as mock_get_registry:
            mock_registry = AsyncMock()
            mock_registry.run_one = AsyncMock(return_value=mock_result)
            mock_get_registry.return_value = mock_registry

            response = client.get("/api/health/check/db_integrity")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "db_integrity"
        assert data["status"] == "healthy"
        assert data["message"] == "SQLite OK"


def test_health_logs_summary_returns_counts() -> None:
    with managed_test_client("anima-health-api-", invalidate_agent=False) as client:
        # Emit some warn/error events through a fresh EventLogger
        import tempfile
        from pathlib import Path

        from anima_server.services.health.event_logger import EventLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            el = EventLogger(log_dir=Path(tmpdir), min_level="trace")
            el.emit("llm", "failure", "error", data={"reason": "timeout"})
            el.emit("llm", "failure", "warn", data={"reason": "slow"})
            el.emit("tool", "crash", "error", data={"tool": "search"})
            el.emit("db", "query", "info")  # info should not count
            el.flush()

            with patch(
                "anima_server.api.routes.health.require_unlocked_session",
                return_value=_mock_session(),
            ), patch(
                "anima_server.api.routes.health.get_event_logger", return_value=el
            ):
                response = client.get("/api/health/logs/summary?hours=1")

        assert response.status_code == 200
        summary = response.json()
        assert isinstance(summary, dict)
        assert summary.get("llm", 0) == 2  # 1 error + 1 warn
        assert summary.get("tool", 0) == 1  # 1 error
        assert "db" not in summary  # only info events, no warn/error


def test_health_logs_returns_event_list() -> None:
    with managed_test_client("anima-health-api-", invalidate_agent=False) as client:
        import tempfile
        from pathlib import Path

        from anima_server.services.health.event_logger import EventLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            el = EventLogger(log_dir=Path(tmpdir), min_level="trace")
            el.emit("llm", "invoke", "info")
            el.emit("llm", "failure", "error", data={"reason": "500"})
            el.flush()

            with patch(
                "anima_server.api.routes.health.require_unlocked_session",
                return_value=_mock_session(),
            ), patch(
                "anima_server.api.routes.health.get_event_logger", return_value=el
            ):
                response = client.get("/api/health/logs?category=llm&since_hours=1")

        assert response.status_code == 200
        events = response.json()
        assert isinstance(events, list)
        assert len(events) == 2
        categories = {e["category"] for e in events}
        assert categories == {"llm"}


def test_health_detailed_with_degraded_status() -> None:
    checks = {
        "db_integrity": _make_check_result("db_integrity", "healthy", "OK"),
        "llm_connectivity": _make_check_result(
            "llm_connectivity", "degraded", "High error rate"
        ),
        "background_tasks": _make_check_result("background_tasks", "healthy", "OK"),
    }
    mock_report = HealthReport(status="degraded", checks=checks)

    with managed_test_client("anima-health-api-", invalidate_agent=False) as client:
        with patch(
            "anima_server.api.routes.health.require_unlocked_session",
            return_value=_mock_session(),
        ), patch(
            "anima_server.api.routes.health.get_default_registry"
        ) as mock_get_registry:
            mock_registry = AsyncMock()
            mock_registry.run_all = AsyncMock(return_value=mock_report)
            mock_get_registry.return_value = mock_registry

            response = client.get("/api/health/detailed")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["checks"]["llm_connectivity"]["status"] == "degraded"


def test_health_endpoints_require_auth() -> None:
    """Verify that all health endpoints return 401 without a session token."""
    with managed_test_client("anima-health-api-", invalidate_agent=False) as client:
        for path in [
            "/api/health/detailed",
            "/api/health/check/db_integrity",
            "/api/health/logs",
            "/api/health/logs/summary",
        ]:
            response = client.get(path)
            assert response.status_code == 401, f"{path} should require auth"
