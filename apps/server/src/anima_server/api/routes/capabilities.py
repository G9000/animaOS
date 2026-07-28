from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.services.capabilities import collect_capabilities

router = APIRouter(prefix="/api", tags=["capabilities"])


@router.get("/capabilities")
async def get_capabilities(request: Request) -> dict[str, Any]:
    await require_unlocked_session_async(request)
    return collect_capabilities()
