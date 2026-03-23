import hmac
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from .api.routes.auth import router as auth_router
from .api.routes.chat import router as chat_router
from .api.routes.config import router as config_router
from .api.routes.consciousness import router as consciousness_router
from .api.routes.core import router as core_router
from .api.routes.db import router as db_router
from .api.routes.forgetting import router as forgetting_router
from .api.routes.graph import router as graph_router
from .api.routes.memory import router as memory_router
from .api.routes.soul import router as soul_router
from .api.routes.tasks import router as tasks_router
from .api.routes.telegram import router as telegram_router
from .api.routes.users import router as users_router
from .api.routes.vault import router as vault_router
from .api.routes.ws import router as ws_router
from .config import settings
from .db.user_store import ensure_per_user_databases_ready
from .services.core import acquire_core_lock, ensure_core_manifest, is_provisioned


def get_cors_origins() -> list[str]:
    origins = [
        "tauri://localhost",
        "https://tauri.localhost",
    ]
    if settings.app_env == "development":
        origins.extend(
            [
                "http://localhost:1420",
                "http://localhost:5173",
                "http://tauri.localhost",
            ]
        )
    return origins


# Paths exempt from sidecar-nonce validation.
_NONCE_EXEMPT_PATHS = frozenset({"/health", "/api/health"})
logger = logging.getLogger(__name__)


class SidecarNonceMiddleware(BaseHTTPMiddleware):
    """Reject requests that do not carry the expected sidecar nonce.

    When ``ANIMA_SIDECAR_NONCE`` is set, every request (except the health
    endpoints) must include the header ``x-anima-nonce`` with the matching
    value.  This binds the desktop client to the exact sidecar process it
    launched, preventing rogue localhost processes from being trusted.

    The nonce is **not** exposed over HTTP — it is delivered to the
    desktop frontend via a trusted Tauri IPC command so that other
    local processes cannot obtain it.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        if not settings.sidecar_nonce and settings.app_env != "development":
            logger.warning("Sidecar nonce is not configured in non-development environment")

    # type: ignore[override]
    async def dispatch(self, request: Request, call_next):
        nonce = settings.sidecar_nonce
        if nonce and request.url.path not in _NONCE_EXEMPT_PATHS:
            header_value = (request.headers.get("x-anima-nonce") or "").strip()
            if not hmac.compare_digest(header_value, nonce):
                return JSONResponse(
                    status_code=403,
                    content={"error": "Invalid or missing sidecar nonce."},
                )
        response = await call_next(request)
        return response


def create_app() -> FastAPI:
    if (
        settings.core_require_encryption
        and not settings.sidecar_nonce
        and settings.app_env != "development"
    ):
        raise RuntimeError("Sidecar nonce must be configured when encryption is required.")
    if not settings.sidecar_nonce and settings.app_env != "development":
        logger.warning("Sidecar nonce is not configured in non-development environment")
    ensure_core_manifest()
    acquire_core_lock()
    ensure_per_user_databases_ready()
    app = FastAPI(title=settings.app_name)

    # Sidecar nonce enforcement — added before CORSMiddleware so that
    # Starlette's reverse-add ordering makes CORS the outermost layer,
    # allowing OPTIONS preflights to succeed before the nonce check runs.
    app.add_middleware(SidecarNonceMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if isinstance(exc.detail, str):
            content: dict[str, object] = {"error": exc.detail}
        else:
            content = {"error": "Request failed", "details": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = []
        for error in exc.errors():
            normalized = dict(error)
            ctx = normalized.get("ctx")
            if isinstance(ctx, dict):
                normalized["ctx"] = {
                    key: str(value) if isinstance(value, Exception) else value
                    for key, value in ctx.items()
                }
            details.append(normalized)
        return JSONResponse(
            status_code=422,
            content={
                "error": "Invalid request",
                "details": jsonable_encoder(details),
            },
        )

    @app.get("/health", tags=["system"])
    @app.get("/api/health", tags=["system"])
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "server",
            "environment": settings.app_env,
            "provisioned": is_provisioned(),
        }

    @app.on_event("shutdown")
    async def _drain_background_tasks() -> None:
        from .services.agent.consolidation import drain_background_memory_tasks
        from .services.agent.reflection import cancel_pending_reflection

        await cancel_pending_reflection()
        await drain_background_memory_tasks()

    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(config_router)
    app.include_router(consciousness_router)
    app.include_router(core_router)
    app.include_router(db_router)
    app.include_router(forgetting_router)
    app.include_router(graph_router)
    app.include_router(memory_router)
    app.include_router(soul_router)
    app.include_router(tasks_router)
    app.include_router(telegram_router)
    app.include_router(users_router)
    app.include_router(vault_router)
    app.include_router(ws_router)

    return app


app = create_app()
