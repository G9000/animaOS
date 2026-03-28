from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_register_and_run_one():
    from anima_server.services.health.models import CheckResult
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()

    async def dummy_check(user_id: int) -> CheckResult:
        return CheckResult(
            name="dummy",
            status="healthy",
            message="all good",
            details={"ping": True},
            duration_ms=1.0,
        )

    registry.register("dummy", dummy_check)
    result = await registry.run_one("dummy", user_id=1)
    assert result.status == "healthy"
    assert result.name == "dummy"


@pytest.mark.asyncio
async def test_run_all():
    from anima_server.services.health.models import CheckResult
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()

    async def healthy_check(user_id: int) -> CheckResult:
        return CheckResult(
            name="a", status="healthy", message="ok", details={}, duration_ms=1.0
        )

    async def degraded_check(user_id: int) -> CheckResult:
        return CheckResult(
            name="b", status="degraded", message="slow", details={}, duration_ms=2.0
        )

    registry.register("a", healthy_check)
    registry.register("b", degraded_check)

    report = await registry.run_all(user_id=1)
    assert report.status == "degraded"
    assert len(report.checks) == 2
    assert "a" in report.checks
    assert "b" in report.checks


@pytest.mark.asyncio
async def test_run_one_unknown_raises():
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()
    with pytest.raises(KeyError):
        await registry.run_one("nonexistent", user_id=1)


@pytest.mark.asyncio
async def test_check_failure_returns_unhealthy():
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()

    async def broken_check(user_id: int):
        raise RuntimeError("DB exploded")

    registry.register("broken", broken_check)
    result = await registry.run_one("broken", user_id=1)
    assert result.status == "unhealthy"
    assert "DB exploded" in result.message


@pytest.mark.asyncio
async def test_format_report_text():
    from anima_server.services.health.models import CheckResult
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()

    async def ok_check(user_id: int) -> CheckResult:
        return CheckResult(
            name="db_integrity", status="healthy", message="SQLite OK", details={}, duration_ms=5.0
        )

    registry.register("db_integrity", ok_check)
    report = await registry.run_all(user_id=1)
    text = registry.format_report(report)
    assert "HEALTHY" in text
    assert "[OK]" in text
    assert "db_integrity" in text.lower() or "Database" in text
