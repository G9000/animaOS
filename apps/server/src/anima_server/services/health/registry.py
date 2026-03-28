from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from anima_server.services.health.models import CheckResult, HealthReport, HealthStatus

logger = logging.getLogger(__name__)

CheckFn = Callable[[int], Awaitable[CheckResult]]

_STATUS_LABELS: dict[HealthStatus, str] = {
    "healthy": "[OK]",
    "degraded": "[WARN]",
    "unhealthy": "[FAIL]",
}


class HealthCheckRegistry:
    """Coordinator that runs registered health checks and aggregates results."""

    def __init__(self) -> None:
        self._checks: dict[str, CheckFn] = {}

    def register(self, name: str, check_fn: CheckFn) -> None:
        self._checks[name] = check_fn

    async def run_one(self, name: str, *, user_id: int) -> CheckResult:
        if name not in self._checks:
            raise KeyError(f"Unknown health check: {name}")
        return await self._safe_run(name, self._checks[name], user_id)

    async def run_all(self, *, user_id: int) -> HealthReport:
        results: dict[str, CheckResult] = {}
        for name, check_fn in self._checks.items():
            results[name] = await self._safe_run(name, check_fn, user_id)
        return HealthReport.from_checks(results)

    @staticmethod
    async def _safe_run(name: str, check_fn: CheckFn, user_id: int) -> CheckResult:
        start = time.monotonic()
        try:
            result = await check_fn(user_id)
            return result
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning("Health check %s failed: %s", name, exc)
            return CheckResult(
                name=name,
                status="unhealthy",
                message=f"Check failed: {exc}",
                details={"error": str(exc)},
                duration_ms=elapsed,
            )

    @staticmethod
    def format_report(report: HealthReport) -> str:
        header = f"System Health: {report.status.upper()}\n"
        lines: list[str] = [header]
        for check in report.checks.values():
            label = _STATUS_LABELS.get(check.status, "[??]")
            lines.append(f"{label} {check.name} — {check.message}")
        return "\n".join(lines)
